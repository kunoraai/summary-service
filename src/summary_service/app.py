from __future__ import annotations

from fastapi import FastAPI

from summary_service.api import build_router
from summary_service.metrics import ServiceMetrics
from summary_service.repository import Repository
from summary_service.settings import Settings


def create_app(
    *,
    settings: Settings,
    repository: Repository,
    metrics: ServiceMetrics | None = None,
) -> FastAPI:
    app = FastAPI(title="Summary Service", version="1.0.0")
    app.include_router(build_router(settings, repository, metrics or ServiceMetrics()))
    return app
