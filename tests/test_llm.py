from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from summary_service.llm import (
    LLMExhausted,
    SummaryOutput,
    classify_exception,
    generate_with_retries,
    render_task_prompt,
)


def test_summary_rejects_more_than_400_unicode_characters() -> None:
    with pytest.raises(ValidationError):
        SummaryOutput(summary="摘" * 401)


def test_task_prompt_replaces_only_configured_placeholder() -> None:
    assert render_task_prompt("文档：{{ text }}", "正文") == "文档：正文"


def test_timeout_is_transient_and_validation_is_permanent() -> None:
    request = httpx.Request("POST", "https://example.test")
    assert classify_exception(httpx.ReadTimeout("timeout", request=request)) == "transient"
    with pytest.raises(ValidationError) as captured:
        SummaryOutput(summary="摘" * 401)
    assert classify_exception(captured.value) == "permanent"


@pytest.mark.asyncio
async def test_transient_failure_uses_at_most_three_total_attempts() -> None:
    calls = 0
    sleeps: list[float] = []

    async def failing_call() -> SummaryOutput:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://example.test")
        raise httpx.ReadTimeout("timeout", request=request)

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(LLMExhausted) as captured:
        await generate_with_retries(
            failing_call,
            max_attempts=3,
            sleep=record_sleep,
            jitter=lambda: 0.0,
        )

    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert captured.value.attempts == 3


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried() -> None:
    calls = 0

    async def invalid_call() -> SummaryOutput:
        nonlocal calls
        calls += 1
        return SummaryOutput(summary="摘" * 401)

    with pytest.raises(LLMExhausted) as captured:
        await generate_with_retries(invalid_call, sleep=asyncio.sleep)

    assert calls == 1
    assert captured.value.attempts == 1
