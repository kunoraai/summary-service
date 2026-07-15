from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, field_validator

from summary_service.metrics import ServiceMetrics
from summary_service.models import JobStatus, NewJob
from summary_service.repository import QueueFullError, Repository
from summary_service.security import ApiKeyVerifier, issue_task_token, sha256_hex
from summary_service.settings import Settings


class SummaryRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


def iso_timestamp(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def build_router(settings: Settings, repository: Repository, metrics: ServiceMetrics) -> APIRouter:
    router = APIRouter()
    verifier = ApiKeyVerifier(settings.api_key_hashes)

    async def authenticate(x_api_key: Annotated[str | None, Header()] = None) -> str:
        if not x_api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
        client_id = verifier.authenticate(x_api_key)
        if client_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
        return client_id

    @router.post("/v1/summaries", status_code=status.HTTP_202_ACCEPTED)
    async def create_summary(
        body: SummaryRequest,
        client_id: Annotated[str, Depends(authenticate)],
        idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
    ) -> dict[str, str]:
        text_bytes = len(body.text.encode("utf-8"))
        if text_bytes > settings.max_input_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="text exceeds 256 KiB UTF-8 limit",
            )
        if idempotency_key:
            token, token_hash = verifier.issue_idempotent_token(
                client_id,
                idempotency_key,
                settings.idempotency_secret,
            )
        else:
            token, token_hash = issue_task_token()
        try:
            job, created = await repository.create_job(
                NewJob(
                    token_hash=token_hash,
                    client_id=client_id,
                    idempotency_key=idempotency_key,
                    input_text=body.text,
                    text_bytes=text_bytes,
                    prompt_version=settings.prompt_version,
                ),
                max_active=settings.max_active_jobs,
            )
        except QueueFullError as error:
            metrics.queue_rejected.inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="active job queue is full",
                headers={"Retry-After": "30"},
            ) from error
        if created:
            metrics.jobs_created.inc()
        return {
            "token": token,
            "status": job.status.value,
            "submitted_at": iso_timestamp(job.created_at_ms) or "",
        }

    @router.get("/v1/summaries/{token}")
    async def get_summary(
        token: str,
        client_id: Annotated[str, Depends(authenticate)],
    ) -> dict[str, str | int | None]:
        job = await repository.get_by_token(client_id, sha256_hex(token))
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
        if job.status == JobStatus.EXPIRED:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="task expired")
        payload: dict[str, str | int | None] = {
            "status": job.status.value,
            "submitted_at": iso_timestamp(job.created_at_ms),
        }
        if job.status == JobStatus.SUCCEEDED:
            payload.update(summary=job.summary, completed_at=iso_timestamp(job.completed_at_ms))
        elif job.status == JobStatus.FAILED:
            payload.update(
                error_code=job.error_code,
                error_message=job.error_message,
                completed_at=iso_timestamp(job.completed_at_ms),
            )
        return payload

    @router.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz")
    async def ready() -> dict[str, str]:
        if await repository.scalar("SELECT 1") != 1:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return {"status": "ready"}

    @router.get("/metrics")
    async def scrape_metrics(_: Request) -> Response:
        counts = await repository.count_by_status()
        active = 0
        for job_status in JobStatus:
            count = counts.get(job_status, 0)
            metrics.jobs_by_status.labels(status=job_status.value).set(count)
            if job_status in (JobStatus.QUEUED, JobStatus.RUNNING):
                active += count
        metrics.queue_depth.set(active)
        return Response(metrics.render(), media_type=CONTENT_TYPE_LATEST)

    return router
