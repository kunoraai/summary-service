from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from summary_service.metrics import ServiceMetrics
from summary_service.repository import Repository


@dataclass(frozen=True, slots=True)
class CleanupResult:
    recovered: int
    expired: int
    deleted: int


class CleanupService:
    def __init__(
        self,
        repository: Repository,
        *,
        tombstone_retention_seconds: int,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        self.repository = repository
        self.tombstone_retention_seconds = tombstone_retention_seconds
        self.metrics = metrics

    async def run_once(self, *, now_ms: int | None = None) -> CleanupResult:
        timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
        recovered = await self.repository.recover_expired_leases(now_ms=timestamp)
        expired = await self.repository.expire_terminal_jobs(
            now_ms=timestamp,
            delete_at_ms=timestamp + self.tombstone_retention_seconds * 1000,
        )
        deleted = await self.repository.delete_expired_jobs(now_ms=timestamp)
        if self.metrics:
            self.metrics.leases_recovered.inc(recovered)
            self.metrics.jobs_cleaned.labels(action="expired").inc(expired)
            self.metrics.jobs_cleaned.labels(action="deleted").inc(deleted)
        return CleanupResult(recovered=recovered, expired=expired, deleted=deleted)

    async def run_forever(self, stop_event: asyncio.Event, *, interval_seconds: int = 60) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
