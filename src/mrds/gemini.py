"""Async Gemini client.

DESIGN DECISION -- we deliberately do NOT use structured output.

The google-genai SDK can enforce a response schema server-side (`response_schema`), which
would make the model physically incapable of returning malformed JSON. That would also make
this entire project pointless: contract violations are the primary signal we exist to
detect, so forcing the schema would silence the hard gate. We ask for JSON in the prompt
and validate what comes back, leaving every violation type reachable:
UNPARSEABLE, MISSING_FIELD, WRONG_TYPE, OUT_OF_RANGE, BAD_ENUM, EXTRA_FIELD.

Retries cover transport failures only -- timeouts, rate limits, 5xx. A response that
arrives intact but violates the contract is never retried; that is a finding, not an error.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

from google import genai
from google.genai import types

from .contracts import Usage

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_ATTEMPTS = 3
BASE_BACKOFF_S = 1.5

# Retried: transient transport conditions. Anything else fails fast.
_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "deadline",
    "timeout",
    "unavailable",
    "resource_exhausted",
    "rate limit",
)


class GeminiError(RuntimeError):
    """A transport-level failure that survived all retries."""


def _is_retryable(exc: Exception) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _RETRYABLE_MARKERS)


class GeminiClient:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise GeminiError(
                "No API key. Set GEMINI_API_KEY in your environment or .env file."
            )
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        self._client = genai.Client(api_key=key)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> tuple[str, Usage]:
        """Return the model's raw text plus usage. Raw text is returned unparsed and
        unrepaired -- interpreting it is the validator's job, not ours."""
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )

        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                resp = await self._client.aio.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - SDK raises a wide range of types
                last_exc = exc
                if attempt == MAX_ATTEMPTS or not _is_retryable(exc):
                    break
                sleep_s = BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 0.4)
                await asyncio.sleep(sleep_s)
                continue

            latency_ms = (time.perf_counter() - started) * 1000
            meta = getattr(resp, "usage_metadata", None)
            usage = Usage(
                latency_ms=round(latency_ms, 1),
                input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            )
            return (resp.text or ""), usage

        raise GeminiError(
            f"{self.model} failed after {MAX_ATTEMPTS} attempt(s): "
            f"{type(last_exc).__name__}: {last_exc}"
        )
