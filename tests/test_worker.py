from __future__ import annotations

import asyncio

import pytest

from summary_service.llm import SummaryOutput
from summary_service.models import JobStatus, NewJob
from summary_service.repository import Repository
from summary_service.worker import WorkerPool


class RecordingGenerator:
    def __init__(self, *, fail: bool = False, concurrency_barrier: int | None = None) -> None:
        self.active = 0
        self.maximum_active = 0
        self.calls = 0
        self.fail = fail
        self.concurrency_barrier = concurrency_barrier
        self.barrier = asyncio.Event()

    async def summarize(self, text: str) -> SummaryOutput:
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.concurrency_barrier and self.active >= self.concurrency_barrier:
            self.barrier.set()
        if self.concurrency_barrier:
            await asyncio.wait_for(self.barrier.wait(), timeout=1)
        await asyncio.sleep(0.02)
        self.active -= 1
        if self.fail:
            raise ValueError("invalid model response")
        return SummaryOutput(summary=f"摘要:{text}")


@pytest.fixture
async def repository(tmp_path):
    instance = Repository(tmp_path / "worker.db")
    await instance.migrate()
    return instance


async def enqueue(repository: Repository, count: int) -> list[int]:
    ids = []
    for index in range(count):
        job, _ = await repository.create_job(
            NewJob(
                token_hash=f"{index + 1:064x}",
                client_id="client-a",
                idempotency_key=None,
                input_text=f"text-{index}",
                text_bytes=6,
                prompt_version="p" * 64,
            )
        )
        ids.append(job.id)
    return ids


@pytest.mark.asyncio
async def test_pool_processes_with_exactly_five_concurrent_workers(repository: Repository) -> None:
    job_ids = await enqueue(repository, 6)
    generator = RecordingGenerator(concurrency_barrier=5)
    pool = WorkerPool(
        repository,
        generator,
        concurrency=5,
        heartbeat_seconds=1,
        lease_seconds=180,
        success_retention_seconds=7200,
        failure_retention_seconds=86400,
    )

    await pool.process_available()

    assert pool.concurrency == 5
    assert generator.maximum_active == 5
    stored_jobs = await asyncio.gather(*(repository.get_by_id(job_id) for job_id in job_ids))
    assert all(job and job.status == JobStatus.SUCCEEDED for job in stored_jobs)


@pytest.mark.asyncio
async def test_permanent_failure_marks_job_failed_once(repository: Repository) -> None:
    job_id = (await enqueue(repository, 1))[0]
    generator = RecordingGenerator(fail=True)
    pool = WorkerPool(
        repository,
        generator,
        concurrency=5,
        heartbeat_seconds=1,
        lease_seconds=180,
        success_retention_seconds=7200,
        failure_retention_seconds=86400,
    )

    await pool.process_available()

    stored = await repository.get_by_id(job_id)
    assert stored and stored.status == JobStatus.FAILED
    assert stored.error_code == "llm_permanent_failure"
    assert stored.attempts == 1


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_while_llm_runs(repository: Repository) -> None:
    job_id = (await enqueue(repository, 1))[0]
    generator = RecordingGenerator()
    pool = WorkerPool(
        repository,
        generator,
        concurrency=5,
        heartbeat_seconds=0.005,
        lease_seconds=1,
        success_retention_seconds=7200,
        failure_retention_seconds=86400,
    )

    await pool.process_available()

    stored = await repository.get_by_id(job_id)
    assert stored and stored.status == JobStatus.SUCCEEDED
