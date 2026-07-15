from __future__ import annotations

import hashlib
import json
import re
from functools import cached_property
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUMMARY_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    database_path: str = "/data/summary.db"
    dashscope_api_key: str = Field(min_length=1)
    api_keys: str = Field(min_length=1)
    idempotency_secret: str = Field(min_length=32)
    system_prompt: str = "你是一名专业文档摘要助手，只输出准确、简洁的中文摘要。"
    task_prompt: str = "请为以下文档生成不超过400字的中文摘要：\n\n{{ text }}"
    model_name: str = "qwen3.7-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_timeout_seconds: int = Field(default=120, ge=1, le=600)
    worker_concurrency: Literal[5] = 5
    max_active_jobs: Literal[100] = 100
    max_input_bytes: Literal[262144] = 262_144
    max_summary_characters: Literal[400] = 400
    llm_max_attempts: Literal[3] = 3
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=100, le=60_000)
    lease_seconds: int = Field(default=180, ge=30)
    heartbeat_seconds: int = Field(default=15, ge=1)
    success_retention_seconds: int = 2 * 60 * 60
    failure_retention_seconds: int = 24 * 60 * 60
    tombstone_retention_seconds: int = 24 * 60 * 60

    @field_validator("task_prompt")
    @classmethod
    def validate_task_prompt(cls, value: str) -> str:
        if value.count("{{ text }}") != 1:
            raise ValueError("task prompt must contain {{ text }} exactly once")
        return value

    @field_validator("api_keys")
    @classmethod
    def validate_api_keys(cls, value: str) -> str:
        entries = [entry.strip() for entry in value.split(",") if entry.strip()]
        if not entries:
            raise ValueError("at least one API key SHA-256 hash is required")
        for entry in entries:
            client_id, separator, digest = entry.partition(":")
            if not separator or not client_id or not SHA256_PATTERN.fullmatch(digest):
                raise ValueError("API keys must use client_id:SHA-256 format")
        return ",".join(entries)

    @cached_property
    def api_key_hashes(self) -> dict[str, str]:
        return dict(entry.split(":", 1) for entry in self.api_keys.split(","))

    @cached_property
    def prompt_version(self) -> str:
        encoded = json.dumps(
            {"system": self.system_prompt, "task": self.task_prompt},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()
