from __future__ import annotations

import asyncio

import pytest

from summary_service.models import JobStatus, NewJob
from summary_service.repository import QueueFullError, Repository


@pytest.fixture
async def repository(tmp_path):
    instance = Repository(tmp_path / "summary.db", busy_timeout_ms=5000)
    await instance.migrate()
    yield instance


def new_job(index: int = 1, *, idempotency_key: str | None = None) -> NewJob:
    return NewJob(
        token_hash=f"{index:064x}",
        client_id="client-a",
        idempotency_key=idempotency_key,
        input_text=f"text-{index}",
        text_bytes=6,
        prompt_version="p" * 64,
    )


@pytest.mark.asyncio
async def test_connection_enables_wal_and_busy_timeout(repository: Repository) -> None:
    assert await repository.scalar("PRAGMA journal_mode") == "wal"
    assert await repository.scalar("PRAGMA busy_timeout") == 5000


@pytest.mark.asyncio
async def test_create_enforces_capacity_in_transaction(repository: Repository) -> None:
    await asyncio.gather(*(repository.create_job(new_job(index), max_active=2) for index in (1, 2)))

    with pytest.raises(QueueFullError):
        await repository.create_job(new_job(3), max_active=2)


@pytest.mark.asyncio
async def test_idempotency_returns_existing_job(repository: Repository) -> None:
    first, created = await repository.create_job(new_job(1, idempotency_key="same"))
    repeated, repeated_created = await repository.create_job(
        new_job(2, idempotency_key="same")
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert repeated.input_text == "text-1"


@pytest.mark.asyncio
async def test_claim_is_atomic_under_concurrency(repository: Repository) -> None:
    first, _ = await repository.create_job(new_job(1))
    second, _ = await repository.create_job(new_job(2))

    claims = await asyncio.gather(
        repository.claim_next("worker-1", now_ms=10_000, lease_seconds=180),
        repository.claim_next("worker-2", now_ms=10_000, lease_seconds=180),
    )

    assert {claim.id for claim in claims if claim} == {first.id, second.id}
    assert all(claim.status == JobStatus.RUNNING for claim in claims if claim)


@pytest.mark.asyncio
async def test_claim_selects_oldest_job_first(repository: Repository) -> None:
    first, _ = await repository.create_job(new_job(1))
    await repository.create_job(new_job(2))

    claimed = await repository.claim_next("worker", now_ms=10_000, lease_seconds=180)

    assert claimed and claimed.id == first.id


@pytest.mark.asyncio
async def test_old_lease_owner_cannot_complete_recovered_job(repository: Repository) -> None:
    job, _ = await repository.create_job(new_job(1))
    claimed = await repository.claim_next("old-owner", now_ms=1_000, lease_seconds=1)
    assert claimed and claimed.id == job.id

    assert await repository.recover_expired_leases(now_ms=2_001) == 1
    reclaimed = await repository.claim_next("new-owner", now_ms=2_002, lease_seconds=180)
    assert reclaimed and reclaimed.id == job.id

    updated = await repository.complete_success(
        job.id,
        "old-owner",
        "stale result",
        completed_at_ms=3_000,
        expires_at_ms=4_000,
    )
    assert updated is False


@pytest.mark.asyncio
async def test_cleanup_redacts_then_deletes(repository: Repository) -> None:
    job, _ = await repository.create_job(new_job(1))
    claimed = await repository.claim_next("worker", now_ms=1_000, lease_seconds=180)
    assert claimed
    assert await repository.complete_success(
        job.id,
        "worker",
        "summary",
        completed_at_ms=2_000,
        expires_at_ms=3_000,
    )

    expired = await repository.expire_terminal_jobs(now_ms=3_000, delete_at_ms=4_000)
    stored = await repository.get_by_id(job.id)
    assert expired == 1
    assert stored and stored.status == JobStatus.EXPIRED
    assert stored.input_text is None and stored.summary is None

    assert await repository.delete_expired_jobs(now_ms=4_000) == 1
    assert await repository.get_by_id(job.id) is None
