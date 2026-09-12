#!/usr/bin/env python3
"""Safe metadata recorder for the LLM layer.

What is recorded is deliberately narrow: identity of the run, sizes, timings,
outcomes.  Raw HTML, request bodies and anything that could carry a credential
are never stored here, which is what lets the record be embedded in the final
report without a second sanitising pass.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import LLMConfig
from .provider import LLMError, LLMResponse


@dataclass
class CallRecord:
    task: str
    status: str                       # ok | cached | error
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1
    error_kind: str | None = None
    error_message: str | None = None
    validation: str | None = None     # accepted | repaired | rejected | abstained

    def as_dict(self) -> dict[str, Any]:
        out = {
            "task": self.task, "status": self.status, "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "attempts": self.attempts,
        }
        if self.error_kind:
            out["error_kind"] = self.error_kind
            out["error_message"] = self.error_message
        if self.validation:
            out["validation"] = self.validation
        return out


@dataclass
class LLMTelemetry:
    """Per-audit accounting for the LLM layer."""

    config: LLMConfig
    snapshot_id: str = ""
    calls: list[CallRecord] = field(default_factory=list)
    repairs: int = 0
    rejections: int = 0
    abstentions: int = 0
    promotions: int = 0
    enrichments: int = 0
    fallbacks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    cache_stats: dict[str, Any] = field(default_factory=dict)

    # -- recording -------------------------------------------------------
    def record_response(self, response: LLMResponse, validation: str | None = None) -> None:
        self.calls.append(CallRecord(
            task=response.task,
            status="cached" if response.cached else "ok",
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            attempts=response.attempts,
            validation=validation,
        ))

    def record_error(self, task: str, error: LLMError) -> None:
        self.calls.append(CallRecord(
            task=task, status="error", error_kind=error.kind,
            error_message=error.message, attempts=1,
        ))

    def note_fallback(self, reason: str) -> None:
        if reason not in self.fallbacks:
            self.fallbacks.append(reason)

    def note_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- rollup ----------------------------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def error_count(self) -> int:
        return sum(1 for call in self.calls if call.status == "error")

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    def status(self) -> str:
        if self.config.is_off:
            return "disabled"
        if not self.calls:
            return "skipped"
        if self.error_count == self.call_count:
            return "failed"
        if self.error_count or self.fallbacks:
            return "degraded"
        return "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.config.effective_mode,
            "status": self.status(),
            "provider": self.config.provider,
            "model": self.config.model or None,
            "prompt_version": self.config.prompt_version,
            "schema_version": self.config.schema_version,
            "taxonomy_version": self.config.taxonomy_version,
            "snapshot_id": self.snapshot_id or None,
            "calls": self.call_count,
            "errors": self.error_count,
            "repairs": self.repairs,
            "rejections": self.rejections,
            "abstentions": self.abstentions,
            "promotions": self.promotions,
            "enrichments": self.enrichments,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "cache": self.cache_stats or None,
            "fallbacks_used": list(self.fallbacks),
            "warnings": list(self.warnings),
            "call_log": [call.as_dict() for call in self.calls],
        }
