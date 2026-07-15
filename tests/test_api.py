from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from summary_service.app import create_app
from summary_service.repository import Repository
from summary_service.security import sha256_hex
from summary_service.settings import Settings

API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
async def api(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        dashscope_api_key="dashscope-test-key",
        api_keys=f"client-a:{sha256_hex(API_KEY)}",
        idempotency_secret="x" * 32,
    )
    repository = Repository(settings.database_path)
    await repository.migrate()
    app = create_app(settings=settings, repository=repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, repository, settings


@pytest.mark.asyncio
async def test_create_returns_202_and_query_returns_queued(api) -> None:
    client, _, _ = api
    created = await client.post("/v1/summaries", json={"text": "正文"}, headers=AUTH)

    assert created.status_code == 202
    payload = created.json()
    assert payload["status"] == "queued"
    assert payload["token"]

    queried = await client.get(f"/v1/summaries/{payload['token']}", headers=AUTH)
    assert queried.status_code == 200
    assert queried.json()["status"] == "queued"

    metrics = await client.get("/metrics")
    assert "summary_jobs_created_total 1.0" in metrics.text
    assert "summary_queue_depth 1.0" in metrics.text


@pytest.mark.asyncio
async def test_authentication_hides_jobs_from_other_clients(api) -> None:
    client, _, _ = api
    assert (await client.post("/v1/summaries", json={"text": "正文"})).status_code == 401
    created = await client.post("/v1/summaries", json={"text": "正文"}, headers=AUTH)

    wrong = await client.get(
        f"/v1/summaries/{created.json()['token']}",
        headers={"X-API-Key": "wrong"},
    )
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_utf8_payload_over_limit_returns_413(api) -> None:
    client, _, _ = api
    response = await client.post(
        "/v1/summaries",
        json={"text": "界" * 87_382},
        headers=AUTH,
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_empty_text_returns_422(api) -> None:
    client, _, _ = api
    response = await client.post("/v1/summaries", json={"text": "  \n"}, headers=AUTH)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_key_returns_same_token(api) -> None:
    client, _, _ = api
    headers = {**AUTH, "Idempotency-Key": "request-1"}
    first = await client.post("/v1/summaries", json={"text": "正文"}, headers=headers)
    repeated = await client.post("/v1/summaries", json={"text": "不同正文"}, headers=headers)

    assert first.status_code == repeated.status_code == 202
    assert first.json()["token"] == repeated.json()["token"]


@pytest.mark.asyncio
async def test_queue_full_returns_429_with_retry_after(api) -> None:
    client, _, settings = api
    for index in range(settings.max_active_jobs):
        response = await client.post(
            "/v1/summaries",
            json={"text": f"job-{index}"},
            headers=AUTH,
        )
        assert response.status_code == 202

    full = await client.post("/v1/summaries", json={"text": "overflow"}, headers=AUTH)
    assert full.status_code == 429
    assert full.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_succeeded_and_expired_response_contract(api) -> None:
    client, repository, _ = api
    created = await client.post("/v1/summaries", json={"text": "正文"}, headers=AUTH)
    token = created.json()["token"]
    claimed = await repository.claim_next("worker", now_ms=1_000, lease_seconds=180)
    assert claimed
    await repository.complete_success(
        claimed.id,
        "worker",
        "摘要",
        completed_at_ms=2_000,
        expires_at_ms=3_000,
    )

    succeeded = await client.get(f"/v1/summaries/{token}", headers=AUTH)
    assert succeeded.status_code == 200
    assert succeeded.json()["summary"] == "摘要"

    await repository.expire_terminal_jobs(now_ms=3_000, delete_at_ms=4_000)
    expired = await client.get(f"/v1/summaries/{token}", headers=AUTH)
    assert expired.status_code == 410
    assert "summary" not in expired.json()
