from __future__ import annotations

import pytest

from summary_service.settings import Settings


def valid_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": "/tmp/summary-test.db",
        "dashscope_api_key": "dashscope-test-key",
        "api_keys": "client-a:" + "a" * 64,
        "idempotency_secret": "i" * 32,
        "system_prompt": "你是一名专业文档摘要助手。",
        "task_prompt": "请生成摘要：{{ text }}",
    }
    values.update(overrides)
    return Settings(**values)


def test_task_prompt_requires_exactly_one_text_placeholder() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        valid_settings(task_prompt="无占位符")

    with pytest.raises(ValueError, match="exactly once"):
        valid_settings(task_prompt="{{ text }} / {{ text }}")


def test_prompt_version_changes_when_prompt_changes() -> None:
    first = valid_settings()
    second = valid_settings(system_prompt="不同系统提示词")

    assert len(first.prompt_version) == 64
    assert first.prompt_version != second.prompt_version


def test_api_keys_must_be_hashes() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        valid_settings(api_keys="client-a:plaintext")


def test_operational_limits_are_fixed() -> None:
    settings = valid_settings()

    assert settings.worker_concurrency == 5
    assert settings.max_active_jobs == 100
    assert settings.max_input_bytes == 262_144
    assert settings.max_summary_characters == 400
    assert settings.llm_max_attempts == 3
