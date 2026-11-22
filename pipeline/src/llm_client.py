"""Async client for the host MLX OpenAI-compatible server.

Includes a concurrency semaphore (mlx_lm.server is effectively single-stream),
exponential backoff, and tolerant JSON extraction from model output.
"""
from __future__ import annotations

import asyncio
import json
import re

from openai import AsyncOpenAI

from . import config

_client = AsyncOpenAI(base_url=config.MLX_BASE_URL, api_key=config.MLX_API_KEY)
_semaphore = asyncio.Semaphore(config.LLM_CONCURRENCY)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class JsonModeUnsupported(RuntimeError):
    """The server rejected `response_format`. Spec 069: extraction never degrades silently to
    free text — the run stops so the profile is fixed, not papered over."""


async def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = config.LLM_MAX_TOKENS,
    json_mode: bool = True,
    retries: int = 3,
) -> str:
    """Single chat completion with backoff. Returns the raw text content."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kwargs = dict(
        model=config.MLX_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=config.LLM_TEMPERATURE,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            async with _semaphore:
                resp = await asyncio.wait_for(
                    _client.chat.completions.create(**kwargs),
                    timeout=config.LLM_TIMEOUT_S,
                )
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            last_err = e
            if json_mode and "response_format" in str(e):
                raise JsonModeUnsupported(f"el servidor MLX rechaza el modo JSON: {e}") from e
            await asyncio.sleep(min(2**attempt, 8))
    raise RuntimeError(f"LLM completion failed after {retries} attempts: {last_err}")


def parse_json(text: str) -> dict | None:
    """Best-effort parse of a JSON object from model output."""
    text = text.strip()
    # strip ```json fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


async def health() -> bool:
    try:
        await asyncio.wait_for(_client.models.list(), timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


async def aclose():
    """Close the underlying httpx connections so the event loop can shut down."""
    try:
        await _client.close()
    except Exception:  # noqa: BLE001
        pass
