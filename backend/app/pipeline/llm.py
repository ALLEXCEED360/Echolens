"""LLM provider interface.

Deliberately thin. Everything that makes Phase 7 correct — evidence assembly,
citation parsing, rejection of fabricated references — lives in `app/answer.py`
and is provider-agnostic, so swapping backends cannot weaken the guarantees.

The only thing a provider does is turn a prompt into text.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Protocol

from app.extras import missing

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """No provider is configured, or its credentials are missing."""


class LLMQuotaExceeded(LLMError):
    """The provider refused on quota.

    `per_day` is the distinction that decides what to do about it. A per-minute
    rate limit clears by itself and the payload says when; a daily allowance
    does not clear until tomorrow, and retrying into it only spends what is
    left. Collapsing the two loses the only fact that makes the error
    actionable.
    """

    def __init__(
        self, message: str, *, per_day: bool = False, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.per_day = per_day
        self.retry_after_s = retry_after_s


def _quota_error(raw: str) -> LLMQuotaExceeded:
    """Turn a provider quota error into something actionable.

    The raw payload buries the three facts that matter — which limit was hit,
    whether it is a daily one, and how long to wait — in a wall of JSON.
    """
    limit = re.search(r"limit:?\s*(\d+)", raw)
    wait = re.search(r"[Pp]lease retry in ([\d.]+)s", raw) or re.search(
        r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", raw
    )
    per_day = "PerDay" in raw or "per day" in raw.lower()
    retry_after_s = float(wait.group(1)) if wait else None

    parts = ["Gemini quota exceeded"]
    if limit:
        window = "per day" if per_day else "per minute"
        parts.append(f"limit {limit.group(1)} requests {window}")
    if retry_after_s is not None:
        parts.append(f"retry in {retry_after_s:.0f}s")
    parts.append(
        "wait for the window to reset"
        if not per_day
        else "switch models with ECHOLENS_LLM_MODEL, or enable billing"
    )
    return LLMQuotaExceeded(
        " — ".join(parts), per_day=per_day, retry_after_s=retry_after_s
    )


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    # The response hit `max_tokens` and stops mid-sentence. Worth surfacing
    # rather than inferring: on a thinking model the *reasoning* tokens are
    # charged against the same output budget, so a limit that is generous for
    # the visible answer can still truncate it, and the result looks like a
    # complete response that simply ends early.
    truncated: bool = False


class LLMProvider(Protocol):
    name: str

    async def complete(
        self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> Completion: ...


class GeminiProvider:
    """Google Gemini via `google-genai`.

    Temperature defaults to 0: the job is to restate retrieved evidence with
    exact citation markers, and sampling variety is nothing but risk here.
    """

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_attempts: int = 3,
        retry_base_s: float = 0.75,
        # A minute-window limit cannot need longer than the window itself, so
        # anything beyond this is a different quota wearing a retry hint.
        max_quota_wait_s: float = 65.0,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.retry_base_s = retry_base_s
        self.max_quota_wait_s = max_quota_wait_s
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ModuleNotFoundError as exc:
                raise missing("google-genai", extra="llm") from exc

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # Worth trying again: the *service* is busy. Nothing about the request is
    # wrong and a retry costs only time.
    _RETRYABLE = ("503", "UNAVAILABLE", "INTERNAL")

    # A 429 is a quota decision, and what to do about it depends entirely on
    # *which* quota. Gemini's free tier enforces both a per-minute rate limit
    # (5 requests) and a daily allowance (as low as 20 requests on some
    # models). The minute limit clears on its own and the payload states the
    # wait, so honouring it is correct. The daily one does not clear, and
    # retrying into it burns what is left, delays the error, and leaves the
    # user strictly worse off — that one is surfaced immediately.
    _QUOTA = ("429", "RESOURCE_EXHAUSTED")

    async def complete(
        self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> Completion:
        """Generate, retrying transient provider failures.

        Measured on this key: roughly one call in six returns
        `503 UNAVAILABLE — this model is currently experiencing high demand`.
        That is the provider being busy rather than anything wrong with the
        request, and a short backoff clears it. Bad requests are not retried —
        repeating them just wastes quota and delays the error.
        """
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await self._attempt(system, user, temperature, max_tokens)
            except LLMError as exc:
                last = exc
                message = str(exc)

                if any(token in message for token in self._QUOTA):
                    quota = _quota_error(message)
                    waitable = (
                        not quota.per_day
                        and quota.retry_after_s is not None
                        and quota.retry_after_s <= self.max_quota_wait_s
                        and attempt < self.max_attempts - 1
                    )
                    if not waitable:
                        raise quota from exc
                    # Pad the provider's own figure: coming back a moment early
                    # spends another request to be told the same thing.
                    delay = (quota.retry_after_s or 0.0) + 2.0
                    logger.warning(
                        "Gemini rate limit (attempt %d/%d), waiting %.0fs: %s",
                        attempt + 1, self.max_attempts, delay, quota,
                    )
                    await asyncio.sleep(delay)
                    continue

                retryable = any(token in message for token in self._RETRYABLE)
                if not retryable or attempt == self.max_attempts - 1:
                    raise
                delay = self.retry_base_s * (2**attempt)
                logger.warning(
                    "Gemini transient failure (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, self.max_attempts, delay, str(exc)[:120],
                )
                await asyncio.sleep(delay)

        raise last if last else LLMError("Gemini request failed")

    async def _attempt(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> Completion:
        from google.genai import types

        client = self._get_client()
        try:
            # Latency is the provider's, not ours, and it is not always
            # well-behaved: an otherwise ordinary request has been observed
            # taking 52 s. A bounded wait turns that into a clean error instead
            # of a request that never returns.
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                        # Nothing here uses tools; leaving AFC on only produces
                        # a warning on every call.
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                ),
                timeout=self.timeout_s,
            )
        except TimeoutError as exc:
            raise LLMError(f"Gemini did not respond within {self.timeout_s:.0f}s") from exc
        except Exception as exc:  # noqa: BLE001 — surface provider failures uniformly
            raise LLMError(f"Gemini request failed: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)

        candidates = getattr(response, "candidates", None) or []
        finish = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
        truncated = "MAX_TOKENS" in finish.upper()
        if truncated:
            thoughts = getattr(usage, "thoughts_token_count", None)
            logger.warning(
                "Gemini response truncated at max_tokens=%d (thinking used %s tokens)",
                max_tokens,
                thoughts if thoughts is not None else "an unreported number of",
            )

        return Completion(
            text=(response.text or "").strip(),
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            truncated=truncated,
        )


class StubProvider:
    """Echoes a canned response. Used by tests so citation handling can be
    verified without a network call or an API key."""

    name = "stub"

    def __init__(self, response: str = "No answer configured.") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> Completion:
        self.calls.append((system, user))
        return Completion(text=self.response, model="stub")


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """The configured provider, constructed once."""
    global _provider
    if _provider is not None:
        return _provider

    from app.config import get_settings

    settings = get_settings()
    if settings.llm_provider == "gemini":
        _provider = GeminiProvider(
            settings.llm_model,
            timeout_s=settings.llm_timeout_s,
            max_attempts=settings.llm_max_attempts,
        )
    elif settings.llm_provider == "stub":
        _provider = StubProvider()
    else:
        raise LLMUnavailable(f"Unknown LLM provider: {settings.llm_provider!r}")

    logger.info("LLM provider: %s (%s)", _provider.name, settings.llm_model)
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Override the provider. For tests."""
    global _provider
    _provider = provider
