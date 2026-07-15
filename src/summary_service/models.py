from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class NewJob:
    token_hash: str
    client_id: str
    idempotency_key: str | None
    input_text: str
    text_bytes: int
    prompt_version: str


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    token_hash: str
    client_id: str
    idempotency_key: str | None
    status: JobStatus
    input_text: str | None
    text_bytes: int
    summary: str | None
    error_code: str | None
    error_message: str | None
    attempts: int
    prompt_version: str
    created_at_ms: int
    started_at_ms: int | None
    completed_at_ms: int | None
    expires_at_ms: int | None
    delete_at_ms: int | None
    lease_owner: str | None
    lease_expires_at_ms: int | None
    heartbeat_at_ms: int | None
