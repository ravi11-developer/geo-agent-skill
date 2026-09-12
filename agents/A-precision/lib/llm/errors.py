#!/usr/bin/env python3
"""Error intelligence and audit health.

Python owns the ``try``/``except``, the timeouts, the retry counters and the
execution of any recovery.  This module gives those failures a shape
(:class:`ErrorEvent`), classifies the ones we recognise without asking anybody,
and constrains what an optional LLM diagnosis is allowed to change.

The rule that matters most here: **a failure of our tooling is not a defect of
the website.**  A timeout, a parser exception or a provider outage becomes an
``auditor_limitation`` and is reported in ``audit_health``; it never becomes a
finding about the site.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .redaction import scrub_exception

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ERROR_CATEGORIES: tuple[str, ...] = (
    "website_defect",
    "transient_network",
    "auditor_limitation",
    "configuration_error",
    "provider_failure",
    "unknown",
)

# The complete set of recoveries an LLM may *recommend*.  Deterministic code
# validates the parameters and decides whether to execute; nothing outside this
# tuple is executable, so a hallucinated action cannot become a side effect.
RECOVERY_ALLOWLIST: tuple[str, ...] = (
    "RETRY_TRANSIENT",
    "SWITCH_WAIT_STRATEGY",
    "USE_CACHED_SNAPSHOT",
    "FALLBACK_TO_STATIC_HTML",
    "REDUCE_CONCURRENCY",
    "SPLIT_LLM_PAYLOAD",
    "SKIP_PAGE_AND_CONTINUE",
    "USE_DETERMINISTIC_ONLY",
    "ABORT_AUDIT",
)

# Recoveries that are only ever valid for a given category.
CATEGORY_RECOVERIES: dict[str, frozenset[str]] = {
    "transient_network": frozenset({"RETRY_TRANSIENT", "SWITCH_WAIT_STRATEGY", "REDUCE_CONCURRENCY",
                                    "SKIP_PAGE_AND_CONTINUE", "USE_CACHED_SNAPSHOT"}),
    "website_defect": frozenset({"FALLBACK_TO_STATIC_HTML", "SKIP_PAGE_AND_CONTINUE"}),
    "auditor_limitation": frozenset({"FALLBACK_TO_STATIC_HTML", "SPLIT_LLM_PAYLOAD",
                                     "SKIP_PAGE_AND_CONTINUE", "USE_DETERMINISTIC_ONLY",
                                     "SWITCH_WAIT_STRATEGY", "USE_CACHED_SNAPSHOT"}),
    "configuration_error": frozenset({"USE_DETERMINISTIC_ONLY", "ABORT_AUDIT"}),
    "provider_failure": frozenset({"USE_DETERMINISTIC_ONLY", "SPLIT_LLM_PAYLOAD", "RETRY_TRANSIENT"}),
    "unknown": frozenset({"SKIP_PAGE_AND_CONTINUE", "USE_DETERMINISTIC_ONLY"}),
}

AUDIT_STATUSES: tuple[str, ...] = ("complete", "partial", "failed")

# Hard ceilings.  These are policy, not suggestions: the LLM cannot raise them.
MAX_GENERAL_RETRIES = 1
MAX_FALLBACKS = 1

# Failures that must never be retried, whatever anything recommends.
NON_RETRYABLE_KINDS = frozenset({"invalid_credentials", "configuration_error"})


# ---------------------------------------------------------------------------
# ErrorEvent
# ---------------------------------------------------------------------------

@dataclass
class ErrorEvent:
    """One normalised failure.

    ``message_sanitized`` is the only text that survives into a report: stack
    traces, absolute paths and anything credential-shaped are stripped at
    construction time rather than on the way out.
    """

    event_id: str
    phase: str                      # crawl | render | parse | analyse | llm | report
    operation: str
    url: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    exception_class: str = ""
    message_sanitized: str = ""
    http_status: int | None = None
    elapsed_ms: int = 0
    retryable: bool = False
    category: str = "unknown"
    classified_by: str = "deterministic"
    recovery_code: str | None = None
    recovery_result: str | None = None
    hypothesis: str | None = None
    limitation_summary: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "event_id": self.event_id,
            "phase": self.phase,
            "operation": self.operation,
            "url": self.url,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "exception_class": self.exception_class,
            "message_sanitized": self.message_sanitized,
            "http_status": self.http_status,
            "elapsed_ms": self.elapsed_ms,
            "retryable": self.retryable,
            "category": self.category,
            "classified_by": self.classified_by,
        }
        for key in ("recovery_code", "recovery_result", "hypothesis", "limitation_summary"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


class ErrorLog:
    """Collects error events for one audit and assigns their ids."""

    def __init__(self) -> None:
        self.events: list[ErrorEvent] = []
        self._retries = 0
        self._fallbacks = 0

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)

    def record(self, *, phase: str, operation: str, exc: BaseException | None = None,
               message: str = "", url: str | None = None, attempt: int = 1,
               max_attempts: int = 1, http_status: int | None = None,
               elapsed_ms: int = 0) -> ErrorEvent:
        event = ErrorEvent(
            event_id=f"ERR-{len(self.events) + 1:03d}",
            phase=phase,
            operation=operation,
            url=url,
            attempt=attempt,
            max_attempts=max_attempts,
            exception_class=type(exc).__name__ if exc is not None else "",
            message_sanitized=scrub_exception(message or (str(exc) if exc is not None else "")),
            http_status=http_status,
            elapsed_ms=elapsed_ms,
        )
        classify(event)
        self.events.append(event)
        return event

    # -- bounded recovery budget ----------------------------------------
    def may_retry(self) -> bool:
        return self._retries < MAX_GENERAL_RETRIES

    def note_retry(self) -> None:
        self._retries += 1

    def may_fall_back(self) -> bool:
        return self._fallbacks < MAX_FALLBACKS

    def note_fallback(self) -> None:
        self._fallbacks += 1

    def as_list(self) -> list[dict[str, Any]]:
        return [event.as_dict() for event in self.events]

    def by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.category] = counts.get(event.category, 0) + 1
        return counts

    def ambiguous(self) -> list[ErrorEvent]:
        """Events a deterministic rule could not confidently classify.

        These are the only ones worth spending an LLM call on.
        """
        return [event for event in self.events if event.category == "unknown"]


# ---------------------------------------------------------------------------
# Deterministic classification
# ---------------------------------------------------------------------------

_NETWORK_EXCEPTIONS = (
    "timeout", "timeouterror", "connectionerror", "connectionreseterror",
    "connectionrefusederror", "readtimeout", "connecttimeout", "sslerror",
    "socketerror", "oserror", "remotedisconnected", "chunkedencodingerror",
    "toomanyredirects", "gaierror", "urlerror",
)
_PARSER_EXCEPTIONS = (
    "unicodedecodeerror", "valueerror", "jsondecodeerror", "parsererror",
    "recursionerror", "attributeerror", "typeerror", "keyerror", "indexerror",
    "memoryerror", "xmlsyntaxerror",
)


def classify(event: ErrorEvent) -> str:
    """Assign ``event.category`` from what we already know.

    Runs before any model is consulted.  Only what this cannot place is left as
    ``unknown``, and only ``unknown`` events are ever sent for AI diagnosis.
    """
    name = (event.exception_class or "").lower()
    message = (event.message_sanitized or "").lower()
    status = event.http_status

    if event.phase == "llm":
        if "credential" in message or "api key" in message or "not configured" in message:
            event.category = "configuration_error"
            event.retryable = False
        else:
            event.category = "provider_failure"
            event.retryable = "timeout" in message or "rate" in message or "5" == str(status or "")[:1]
        return event.category

    if status is not None:
        if status == 429:
            event.category, event.retryable = "transient_network", True
            return event.category
        if 500 <= int(status) < 600:
            # The server is failing.  That is the site's behaviour, but a single
            # 5xx during one crawl is weak evidence, so it stays a transient
            # network condition unless the crawler saw it repeatedly.
            event.category = "transient_network" if event.attempt <= 1 else "website_defect"
            event.retryable = True
            return event.category
        if status in (401, 403):
            # Authenticated or bot-walled: a real retrieval barrier for a
            # machine, and the crawlability detector reports it with evidence.
            event.category, event.retryable = "website_defect", False
            return event.category
        if 400 <= int(status) < 500:
            event.category, event.retryable = "website_defect", False
            return event.category

    if any(token in name for token in _NETWORK_EXCEPTIONS) or "timed out" in message:
        event.category, event.retryable = "transient_network", True
        return event.category
    if any(token in name for token in _PARSER_EXCEPTIONS):
        # Our parser broke on their markup.  Until something else proves the
        # markup is at fault, this is our limitation, not their defect.
        event.category, event.retryable = "auditor_limitation", False
        return event.category

    event.category, event.retryable = "unknown", False
    return event.category


def recovery_is_allowed(category: str, code: str) -> bool:
    """Whether a recovery code may be executed for a category."""
    if code not in RECOVERY_ALLOWLIST:
        return False
    return code in CATEGORY_RECOVERIES.get(category, frozenset())


def apply_diagnosis(event: ErrorEvent, diagnosis: Mapping[str, Any], log: ErrorLog) -> str:
    """Merge a validated LLM diagnosis into an event, under deterministic policy.

    The model may explain and may *recommend*; the decision to act stays here.
    A recommendation is refused when the category does not permit it, when the
    underlying failure is non-retryable, or when the run's recovery budget is
    already spent.
    """
    category = str(diagnosis.get("category", "unknown"))
    if category not in ERROR_CATEGORIES:
        category = "unknown"

    # Never let a diagnosis turn one of our own failures into a site defect
    # unless deterministic classification had already reached that conclusion.
    if category == "website_defect" and event.category != "website_defect":
        category = "auditor_limitation"

    event.category = category
    event.classified_by = "llm"
    event.hypothesis = str(diagnosis.get("hypothesis") or "") or None
    event.limitation_summary = str(diagnosis.get("limitation_summary") or "") or None

    code = str(diagnosis.get("recovery_code") or "").upper()
    if not recovery_is_allowed(category, code):
        event.recovery_code = None
        event.recovery_result = f"refused: {code or 'none'} is not permitted for {category}"
        return event.recovery_result

    if code == "RETRY_TRANSIENT":
        if not event.retryable or event.exception_class.lower() in NON_RETRYABLE_KINDS:
            event.recovery_result = "refused: failure is not retryable"
            return event.recovery_result
        if not log.may_retry():
            event.recovery_result = "refused: retry budget exhausted"
            return event.recovery_result

    event.recovery_code = code
    event.recovery_result = "accepted"
    return event.recovery_result


# ---------------------------------------------------------------------------
# Audit health
# ---------------------------------------------------------------------------

@dataclass
class AuditHealth:
    """The optional, backward-compatible health block of the report.

    A partial crawl is never presented as complete: the status is derived from
    what actually happened, not from whether the run threw.
    """

    pages_discovered: int = 0
    pages_requested: int = 0
    pages_analyzed: int = 0
    semantic_pages_analyzed: int = 0
    llm_mode: str = "off"
    llm_status: str = "disabled"
    fallbacks_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    skills_failed: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def status(self) -> str:
        """complete / partial / failed, derived from what actually happened.

        "partial" means *we* could not see something we should have seen: a
        skill failed, or a fetch/render/parse step did not complete for a reason
        that is our limitation or a transient network condition.

        A 4xx or 5xx on a page of the site is deliberately NOT partial. That is
        a measurement *about the site* - the crawlability detector reports it
        with evidence and it appears in `broken_links` - and calling the audit
        incomplete because the site has a broken link would mislabel a finding
        as a gap. Likewise, crawling fewer pages than the budget allows is not
        partial: the budget is a ceiling, not a target, and most sites are
        smaller than it.
        """
        if self.pages_analyzed == 0:
            return "failed"
        blocking = [event for event in self.errors
                    if event.get("category") in ("auditor_limitation", "transient_network")
                    and event.get("phase") in ("crawl", "render", "parse")]
        if self.skills_failed or blocking:
            return "partial"
        return "complete"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "pages_discovered": self.pages_discovered,
            "pages_requested": self.pages_requested,
            "pages_analyzed": self.pages_analyzed,
            "semantic_pages_analyzed": self.semantic_pages_analyzed,
            "llm_mode": self.llm_mode,
            "llm_status": self.llm_status,
            "fallbacks_used": list(self.fallbacks_used),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
        }


def limitation_notes(events: Iterable[ErrorEvent]) -> list[str]:
    """Reader-facing sentences describing what the audit could not see."""
    notes: list[str] = []
    for event in events:
        if event.category == "website_defect":
            continue
        if event.limitation_summary:
            notes.append(event.limitation_summary)
        elif event.url:
            notes.append(
                f"{event.operation} did not complete for {event.url} "
                f"({event.exception_class or 'error'}); that page is missing from the analysis."
            )
        else:
            notes.append(
                f"{event.phase}/{event.operation} did not complete "
                f"({event.exception_class or 'error'}); results may be incomplete."
            )
    return list(dict.fromkeys(notes))
