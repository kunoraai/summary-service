from __future__ import annotations

import argparse
import asyncio
import signal

import uvicorn

from summary_service.app import create_app
from summary_service.cleanup import CleanupService
from summary_service.llm import PydanticSummaryAgent
from summary_service.logging_config import configure_logging
from summary_service.repository import Repository
from summary_service.settings import Settings
from summary_service.worker import WorkerPool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="summary-service")
    parser.add_argument("command", choices=("migrate", "api", "worker"))
    return parser


def _components() -> tuple[Settings, Repository]:
    settings = Settings()
    return settings, Repository(
        settings.database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )


async def migrate() -> None:
    _, repository = _components()
    await repository.migrate()


async def run_worker() -> None:
    settings, repository = _components()
    await repository.migrate()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    agent = PydanticSummaryAgent(settings)
    pool = WorkerPool(
        repository,
        agent,
        concurrency=settings.worker_concurrency,
        heartbeat_seconds=settings.heartbeat_seconds,
        lease_seconds=settings.lease_seconds,
        success_retention_seconds=settings.success_retention_seconds,
        failure_retention_seconds=settings.failure_retention_seconds,
        max_attempts=settings.llm_max_attempts,
    )
    cleanup = CleanupService(
        repository,
        tombstone_retention_seconds=settings.tombstone_retention_seconds,
    )
    await asyncio.gather(pool.run_forever(stop_event), cleanup.run_forever(stop_event))


def main() -> None:
    configure_logging()
    command = build_parser().parse_args().command
    if command == "migrate":
        asyncio.run(migrate())
    elif command == "worker":
        asyncio.run(run_worker())
    else:
        settings, repository = _components()
        asyncio.run(repository.migrate())
        uvicorn.run(
            create_app(settings=settings, repository=repository),
            host="0.0.0.0",
            port=8080,
            proxy_headers=True,
            access_log=False,
        )


if __name__ == "__main__":
    main()
