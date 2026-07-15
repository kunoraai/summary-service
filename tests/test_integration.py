from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from summary_service.app import create_app
from summary_service.llm import SummaryOutput
from summary_service.repository import Repository
from summary_service.security import sha256_hex
from summary_service.settings import Settings
from summary_service.worker import WorkerPool


class FakeAgent:
    async def summarize(self, text: str) -> SummaryOutput:
        return SummaryOutput(summary=f"测试摘要：{text[:10]}")


async def test_submit_worker_poll_round_trip(tmp_path) -> None:
    api_key = "integration-key"
    settings = Settings(
        database_path=str(tmp_path / "integration.db"),
        dashscope_api_key="dashscope-test-key",
        api_keys=f"integration:{sha256_hex(api_key)}",
        idempotency_secret="x" * 32,
    )
    repository = Repository(settings.database_path)
    await repository.migrate()
    app = create_app(settings=settings, repository=repository)
    headers = {"X-API-Key": api_key}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/v1/summaries",
            json={"text": "这是一段长文正文"},
            headers=headers,
        )
        token = created.json()["token"]

        pool = WorkerPool(
            repository,
            FakeAgent(),
            concurrency=settings.worker_concurrency,
            heartbeat_seconds=settings.heartbeat_seconds,
            lease_seconds=settings.lease_seconds,
            success_retention_seconds=settings.success_retention_seconds,
            failure_retention_seconds=settings.failure_retention_seconds,
        )
        await pool.process_available()

        result = await client.get(f"/v1/summaries/{token}", headers=headers)

    assert created.status_code == 202
    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"
    assert result.json()["summary"].startswith("测试摘要")
