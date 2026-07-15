from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import httpx
import openai
from pydantic import BaseModel, StringConstraints, ValidationError
from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from summary_service.settings import Settings


class SummaryOutput(BaseModel):
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


class LLMExhausted(RuntimeError):
    def __init__(self, message: str, *, attempts: int, classification: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.classification = classification


def render_task_prompt(template: str, text: str) -> str:
    return template.replace("{{ text }}", text)


def classify_exception(error: Exception) -> Literal["transient", "permanent"]:
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError, openai.APIConnectionError)):
        return "transient"
    if isinstance(error, openai.RateLimitError):
        return "transient"
    if isinstance(error, openai.APIStatusError):
        return "transient" if error.status_code >= 500 else "permanent"
    if isinstance(error, UnexpectedModelBehavior):
        return "transient"
    if isinstance(error, ValidationError):
        return "permanent"
    return "permanent"


async def generate_with_retries(
    operation: Callable[[], Awaitable[SummaryOutput]],
    *,
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
) -> tuple[SummaryOutput, int]:
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation(), attempt
        except Exception as error:
            classification = classify_exception(error)
            if classification == "permanent" or attempt == max_attempts:
                raise LLMExhausted(
                    "LLM request failed",
                    attempts=attempt,
                    classification=classification,
                ) from error
            await sleep(min(8.0, 2.0 ** (attempt - 1)) + jitter() * 0.5)
    raise AssertionError("unreachable")


class PydanticSummaryAgent:
    def __init__(self, settings: Settings) -> None:
        provider = OpenAIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.dashscope_api_key,
        )
        model = OpenAIModel(settings.model_name, provider=provider)
        self._agent = Agent(
            model,
            output_type=SummaryOutput,
            system_prompt=settings.system_prompt,
            retries=0,
            model_settings={"timeout": settings.llm_timeout_seconds},
        )
        self._task_prompt = settings.task_prompt

    async def summarize(self, text: str) -> SummaryOutput:
        result = await self._agent.run(render_task_prompt(self._task_prompt, text))
        return result.output
