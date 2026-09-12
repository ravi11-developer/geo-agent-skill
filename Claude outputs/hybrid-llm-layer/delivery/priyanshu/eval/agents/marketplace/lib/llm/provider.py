#!/usr/bin/env python3
"""Provider-neutral LLM client.

The rest of the marketplace never imports a vendor SDK and never sees a model
identifier.  It asks this module for a client, gets back something that answers
:meth:`LLMClient.complete_json`, and handles exactly three outcomes: a parsed
object, a typed :class:`LLMError`, or ``None`` because the layer is disabled.

Adapters shipped here:

``DisabledClient``  the no-op used whenever the feature mode is ``off`` or the
                    provider cannot be constructed.  It never raises.
``FakeClient``      deterministic, scripted responses for tests and for the
                    ablation benchmark, so the whole hybrid path is exercised
                    with no network and no cost.
``AnthropicClient`` a thin adapter over the Anthropic Messages API, imported
                    lazily so the package has no hard dependency on the SDK.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .config import LLMConfig
from .redaction import contains_secret, scrub_exception

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

ERROR_KINDS = (
    "timeout",
    "rate_limited",
    "server_error",
    "invalid_credentials",
    "provider_unavailable",
    "payload_too_large",
    "invalid_response",
    "budget_exhausted",
    "disabled",
)


class LLMError(Exception):
    """A normalised provider failure.

    Provider-specific exception types are collapsed into :attr:`kind` here so
    that the recovery policy can reason about them without importing any SDK.
    """

    def __init__(self, kind: str, message: str, *, retryable: bool = False,
                 status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "provider_unavailable"
        self.message = scrub_exception(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "retryable": self.retryable,
            "status_code": self.status_code,
        }


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMRequest:
    """One structured-output request.

    ``system`` carries the untrusted-content contract; ``user`` carries the
    serialised evidence.  ``task`` names the prompt template, which becomes part
    of the cache key and the telemetry record.
    """

    task: str
    system: str
    user: str
    max_output_tokens: int = 4000
    temperature: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def approx_input_tokens(self) -> int:
        """Cheap, provider-neutral size estimate (~4 characters per token)."""
        return (len(self.system) + len(self.user)) // 4 + 8


@dataclass
class LLMResponse:
    """What a client returns on success."""

    task: str
    data: Any                       # parsed JSON payload
    raw_text: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1

    def as_usage(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model or None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "attempts": self.attempts,
        }


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.S | re.I)


def extract_json(text: str) -> Any:
    """Parse a JSON object out of a model response.

    Models wrap JSON in prose or fences often enough that a bare
    ``json.loads`` is not a usable contract; this recovers the object when it is
    recoverable and raises :class:`LLMError` (``invalid_response``) when it is
    not, which is what triggers the single repair attempt upstream.
    """
    if text is None:
        raise LLMError("invalid_response", "empty response body")
    candidate = text.strip()
    if not candidate:
        raise LLMError("invalid_response", "empty response body")

    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group("body").strip()

    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Fall back to the outermost balanced {...} or [...] block.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except Exception:
                continue
    raise LLMError("invalid_response", "response body is not valid JSON")


# ---------------------------------------------------------------------------
# Base client
# ---------------------------------------------------------------------------

class LLMClient:
    """Interface every adapter implements."""

    name = "base"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.calls = 0

    @property
    def available(self) -> bool:
        return False

    def complete_json(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError

    # -- shared helpers --------------------------------------------------
    def _guard(self, request: LLMRequest) -> None:
        """Bounds that hold for every provider."""
        if self.calls >= self.config.max_calls:
            raise LLMError("budget_exhausted",
                           f"call budget of {self.config.max_calls} requests exhausted")
        if request.approx_input_tokens() > self.config.max_input_tokens:
            raise LLMError(
                "payload_too_large",
                f"request is ~{request.approx_input_tokens()} tokens, over the "
                f"{self.config.max_input_tokens} token limit",
            )
        if contains_secret(request.user) or contains_secret(request.system):
            # Redaction runs before this point; reaching here is a bug, and
            # sending the payload anyway would leak a credential.
            raise LLMError("invalid_response", "payload failed the pre-send secret scan")


class DisabledClient(LLMClient):
    """The client used when the feature is off.  Every call is refused cheaply."""

    name = "disabled"

    @property
    def available(self) -> bool:
        return False

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        raise LLMError("disabled", "LLM layer is disabled")


# ---------------------------------------------------------------------------
# Fake client (tests, ablation, offline development)
# ---------------------------------------------------------------------------

Responder = Callable[[LLMRequest], Any]


class FakeClient(LLMClient):
    """Scripted client.

    Responses are supplied either as a per-task mapping (``{"semantic": {...}}``)
    or as a callable that receives the request.  A response may be:

    * a JSON-serialisable object  -> returned as parsed data;
    * a ``str``                   -> returned as raw text and parsed, which is
      how malformed-JSON and repair paths are tested;
    * an :class:`LLMError`        -> raised, which is how error paths are tested;
    * a ``list``                  -> consumed one entry per call, so a failure
      followed by a successful repair can be scripted.
    """

    name = "fake"

    def __init__(self, config: LLMConfig,
                 responses: Mapping[str, Any] | Responder | None = None,
                 *, latency_ms: int = 0):
        super().__init__(config)
        self._responses = responses or {}
        self._latency_ms = latency_ms
        self.requests: list[LLMRequest] = []

    @property
    def available(self) -> bool:
        return True

    def _resolve(self, request: LLMRequest) -> Any:
        if callable(self._responses):
            return self._responses(request)
        if request.task in self._responses:
            entry = self._responses[request.task]
        elif "*" in self._responses:
            entry = self._responses["*"]
        else:
            raise LLMError("provider_unavailable", f"fake client has no response for task {request.task!r}")
        if isinstance(entry, list):
            if not entry:
                raise LLMError("provider_unavailable", f"fake client exhausted responses for {request.task!r}")
            return entry.pop(0)
        return entry

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self._guard(request)
        self.calls += 1
        self.requests.append(request)
        entry = self._resolve(request)
        if isinstance(entry, LLMError):
            raise entry
        if isinstance(entry, BaseException):
            raise LLMError("provider_unavailable", str(entry))
        if isinstance(entry, str):
            raw = entry
            data = extract_json(raw)
        else:
            data = entry
            raw = json.dumps(entry)
        return LLMResponse(
            task=request.task, data=data, raw_text=raw, provider=self.name,
            model=self.config.model or "fake-model",
            input_tokens=request.approx_input_tokens(),
            output_tokens=max(1, len(raw) // 4),
            latency_ms=self._latency_ms,
        )


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    """Thin adapter over the Anthropic Messages API.

    The SDK is imported lazily inside :meth:`_ensure_sdk` so that neither the
    marketplace package nor its tests depend on it, and the model identifier
    always comes from configuration - nothing in this file names a model.
    """

    name = "anthropic"

    def __init__(self, config: LLMConfig, client: Any = None):
        super().__init__(config)
        self._client = client
        self._unavailable: str | None = None
        if client is None:
            if not config.api_key_present:
                self._unavailable = "ANTHROPIC_API_KEY is not set"
            elif not config.model:
                self._unavailable = "LLM_MODEL is not configured"

    def _ensure_sdk(self) -> Any:
        if self._client is not None:
            return self._client
        if self._unavailable:
            raise LLMError("invalid_credentials", self._unavailable)
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            self._unavailable = "anthropic SDK is not installed"
            raise LLMError("provider_unavailable", f"anthropic SDK is not installed: {exc}") from None
        self._client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            timeout=self.config.timeout_seconds,
            max_retries=0,   # retries are this module's job, and they are bounded
        )
        return self._client

    @property
    def available(self) -> bool:
        return self._unavailable is None

    @staticmethod
    def _normalise_error(exc: BaseException) -> LLMError:
        name = type(exc).__name__.lower()
        text = str(exc)
        status = getattr(exc, "status_code", None)
        retry_after = None
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers:
            try:
                retry_after = float(headers.get("retry-after"))
            except (TypeError, ValueError):
                retry_after = None
        if "timeout" in name or "timed out" in text.lower():
            return LLMError("timeout", text, retryable=True)
        if status == 429 or "ratelimit" in name:
            return LLMError("rate_limited", text, retryable=True, status_code=429, retry_after=retry_after)
        if status in (401, 403) or "authentication" in name or "permission" in name:
            return LLMError("invalid_credentials", text, retryable=False, status_code=status)
        if status == 413 or "too large" in text.lower():
            return LLMError("payload_too_large", text, retryable=False, status_code=status)
        if status is not None and 500 <= int(status) < 600:
            return LLMError("server_error", text, retryable=True, status_code=status)
        if "connection" in name or "apiconnection" in name:
            return LLMError("provider_unavailable", text, retryable=True)
        return LLMError("provider_unavailable", text, retryable=False, status_code=status)

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self._guard(request)
        client = self._ensure_sdk()
        attempts = 0
        last: LLMError | None = None
        started = time.monotonic()

        while attempts <= self.config.max_retries:
            attempts += 1
            self.calls += 1
            try:
                message = client.messages.create(
                    model=self.config.model,
                    max_tokens=min(request.max_output_tokens, self.config.max_output_tokens),
                    temperature=request.temperature,
                    system=request.system,
                    messages=[{"role": "user", "content": request.user}],
                )
                text = "".join(
                    getattr(block, "text", "") for block in getattr(message, "content", []) or []
                )
                usage = getattr(message, "usage", None)
                return LLMResponse(
                    task=request.task,
                    data=extract_json(text),
                    raw_text=text,
                    provider=self.name,
                    model=self.config.model,
                    input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempts,
                )
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempts > self.config.max_retries:
                    raise
            except Exception as exc:  # noqa: BLE001 - normalised, never leaked raw
                last = self._normalise_error(exc)
                if not last.retryable or attempts > self.config.max_retries:
                    raise last from None
            # Bounded backoff; honour Retry-After when the provider sent one.
            delay = last.retry_after if (last and last.retry_after) else 0.5 * attempts
            time.sleep(min(float(delay), 5.0))

        raise last or LLMError("provider_unavailable", "no response")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_client(config: LLMConfig, *, fake_responses: Any = None,
                 injected: LLMClient | None = None) -> LLMClient:
    """Return the client for this configuration.

    Never raises: an unusable provider degrades to :class:`DisabledClient`, and
    the caller reports that as an audit limitation rather than a site defect.
    """
    if injected is not None:
        return injected
    if config.is_off:
        return DisabledClient(config)

    provider = config.provider.strip().lower()
    if provider in ("fake", "mock", "test"):
        return FakeClient(config, fake_responses)
    if provider in ("none", "disabled", "off"):
        return DisabledClient(config)
    if provider == "anthropic":
        client = AnthropicClient(config)
        return client if client.available else DisabledClient(config)
    return DisabledClient(config)


def unavailable_reason(config: LLMConfig) -> str | None:
    """Human-readable reason the configured provider cannot be used, if any."""
    if config.is_off:
        return None
    provider = config.provider.strip().lower()
    if provider in ("fake", "mock", "test"):
        return None
    if provider != "anthropic":
        return f"unknown provider {provider!r}"
    if not config.api_key_present:
        return "ANTHROPIC_API_KEY is not set"
    if not config.model:
        return "LLM_MODEL is not configured"
    return None
