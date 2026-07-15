from __future__ import annotations

import json
import logging

import pytest

from summary_service.cleanup import CleanupService
from summary_service.logging_config import JsonFormatter
from summary_service.metrics import ServiceMetrics
from summary_service.models import JobStatus, NewJob
from summary_service.repository import Repository


@pytest.mark.asyncio
async def test_cleanup_recovers_expires_and_deletes(tmp_path) -> None:
    repository = Repository(tmp_path / "cleanup.db")
    await repository.migrate()
    running, _ = await repository.create_job(
        NewJob("1" * 64, "client", None, "text", 4, "p" * 64)
    )
    await repository.claim_next("worker", now_ms=1_000, lease_seconds=1)
    terminal, _ = await repository.create_job(
        NewJob("2" * 64, "client", None, "text", 4, "p" * 64)
    )
    await repository.claim_next("worker", now_ms=1_000, lease_seconds=1)
    await repository.complete_success(
        terminal.id,
        "worker",
        "summary",
        completed_at_ms=1_500,
        expires_at_ms=2_000,
    )
    cleanup = CleanupService(repository, tombstone_retention_seconds=1)

    result = await cleanup.run_once(now_ms=2_001)

    assert result.recovered == 1
    assert result.expired == 1
    assert (await repository.get_by_id(running.id)).status == JobStatus.QUEUED
    assert (await repository.get_by_id(terminal.id)).status == JobStatus.EXPIRED
    assert (await cleanup.run_once(now_ms=3_001)).deleted == 1


def test_metrics_render_queue_and_completion_counters() -> None:
    metrics = ServiceMetrics()
    metrics.jobs_created.inc()
    metrics.jobs_completed.labels(status="succeeded").inc()
    metrics.queue_depth.set(3)

    rendered = metrics.render().decode()

    assert "summary_jobs_created_total 1.0" in rendered
    assert 'summary_jobs_completed_total{status="succeeded"} 1.0' in rendered
    assert "summary_queue_depth 3.0" in rendered


def test_json_formatter_emits_only_allowlisted_fields() -> None:
    record = logging.LogRecord("service", logging.INFO, __file__, 1, "processed", (), None)
    record.job_id = 7
    record.client_id = "client-a"
    record.input_text = "sensitive body"
    record.token = "sensitive token"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "processed"
    assert payload["job_id"] == 7
    assert "input_text" not in payload
    assert "token" not in payload
    assert "sensitive" not in json.dumps(payload)
