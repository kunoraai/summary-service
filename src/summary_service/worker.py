from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from typing import Protocol

from summary_service.llm import LLMExhausted, SummaryOutput, generate_with_retries
from summary_service.metrics import ServiceMetrics
from summary_service.models import Job
from summary_service.repository import Repository


class SummaryGenerator(Protocol):
    async def summarize(self, text: str) -> SummaryOutput: ...


class WorkerPool:
    def __init__(
        self,
        repository: Repository,
        generator: SummaryGenerator,
        *,
        concurrency: int,
        heartbeat_seconds: float,
        lease_seconds: int,
        success_retention_seconds: int,
        failure_retention_seconds: int,
        max_attempts: int = 3,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        if concurrency != 5:
            raise ValueError("worker concurrency must be exactly 5")
        self.repository = repository
        self.generator = generator
        self.concurrency = concurrency
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self.success_retention_seconds = success_retention_seconds
        self.failure_retention_seconds = failure_retention_seconds
        self.max_attempts = max_attempts
        self.metrics = metrics

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    async def process_available(self) -> None:
        await asyncio.gather(
            *(self._drain_worker(f"worker-{index}-{uuid.uuid4().hex}") for index in range(5))
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        await asyncio.gather(
            *(
                self._run_worker(f"worker-{index}-{uuid.uuid4().hex}", stop_event)
                for index in range(5)
            )
        )

    async def _run_worker(self, owner: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            job = await self.repository.claim_next(
                owner,
                now_ms=self._now_ms(),
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    continue
                continue
            await self._process_job(job, owner)

    async def _drain_worker(self, owner: str) -> None:
        while True:
            job = await self.repository.claim_next(
                owner,
                now_ms=self._now_ms(),
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                return
            await self._process_job(job, owner)

    async def _process_job(self, job: Job, owner: str) -> None:
        started = time.monotonic()
        heartbeat = asyncio.create_task(self._heartbeat(job.id, owner))
        try:
            if job.input_text is None:
                raise ValueError("job input is unavailable")
            output, attempts = await generate_with_retries(
                lambda: self.generator.summarize(job.input_text or ""),
                max_attempts=self.max_attempts,
            )
            completed_at = self._now_ms()
            await self.repository.complete_success(
                job.id,
                owner,
                output.summary,
                attempts=attempts,
                completed_at_ms=completed_at,
                expires_at_ms=completed_at + self.success_retention_seconds * 1000,
            )
            if self.metrics:
                self.metrics.jobs_completed.labels(status="succeeded").inc()
                self.metrics.llm_attempts.observe(attempts)
        except LLMExhausted as error:
            completed_at = self._now_ms()
            await self.repository.complete_failure(
                job.id,
                owner,
                f"llm_{error.classification}_failure",
                "LLM summary generation failed",
                attempts=error.attempts,
                completed_at_ms=completed_at,
                expires_at_ms=completed_at + self.failure_retention_seconds * 1000,
            )
            if self.metrics:
                self.metrics.jobs_completed.labels(status="failed").inc()
                self.metrics.llm_attempts.observe(error.attempts)
        finally:
            if self.metrics:
                self.metrics.llm_latency.observe(time.monotonic() - started)
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, job_id: int, owner: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            updated = await self.repository.heartbeat(
                job_id,
                owner,
                now_ms=self._now_ms(),
                lease_seconds=self.lease_seconds,
            )
            if not updated:
                return
