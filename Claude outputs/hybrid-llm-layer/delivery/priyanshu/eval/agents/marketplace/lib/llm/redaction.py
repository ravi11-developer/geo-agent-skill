#!/usr/bin/env python3
"""Redaction of secrets and personal data before anything leaves the process.

Two rules shape this module:

* **Nothing that could be a credential is ever sent to a provider or written to
  a log.**  Redaction runs on the way *into* the LLM layer, not as a cleanup
  pass afterwards, so an un-redacted value never exists in a request body.
* **Redaction is visible.**  Every removal leaves a typed placeholder such as
  ``[REDACTED:api_key]`` so a reviewer can see that something was removed and
  the model can still reason about the shape of the page.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Header names that must never be forwarded.
FORBIDDEN_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "x-auth-token", "x-csrf-token", "x-xsrf-token",
    "authentication", "www-authenticate", "x-amz-security-token",
})

# Ordered: the most specific pattern must win, because the first match consumes
# the text.  Each entry is (label, compiled pattern).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}", re.I)),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("api_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("api_key", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.I)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("session_id", re.compile(r"\b(?:sessionid|session_id|sid|phpsessid|jsessionid)\s*[=:]\s*[A-Za-z0-9._\-]{8,}", re.I)),
    ("credential", re.compile(r"\b(?:api[_\-]?key|secret|password|passwd|token|access[_\-]?key)\s*[=:]\s*[\"']?[A-Za-z0-9._\-]{8,}[\"']?", re.I)),
    ("card_number", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<![\w.])(?:\+\d{1,3}[ \-]?)?(?:\(\d{2,4}\)[ \-]?)?\d{3,5}[ \-]\d{3,5}(?:[ \-]\d{2,5})?(?![\w.])")),
)

# Keys whose *values* are always dropped from any mapping we serialise.
SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey", "access_key",
    "authorization", "cookie", "cookies", "session", "sessionid", "session_id",
    "credit_card", "card_number", "cvv", "ssn", "otp", "pin",
})

_LUHN_MIN_DIGITS = 13


def _looks_like_card(text: str) -> bool:
    digits = [int(ch) for ch in text if ch.isdigit()]
    if not (_LUHN_MIN_DIGITS <= len(digits) <= 19):
        return False
    checksum, parity = 0, len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def redact_text(text: str, *, keep_emails: bool = False) -> tuple[str, list[str]]:
    """Return ``(redacted_text, labels_removed)``.

    ``keep_emails`` exists because a contact page's email address is sometimes
    the very fact under audit (an entity signal).  It defaults to *off*: an
    address is removed unless a caller states that it needs it.
    """
    if not text:
        return "", []
    removed: list[str] = []
    out = text
    for label, pattern in _PATTERNS:
        if label == "email" and keep_emails:
            continue

        def _sub(match: re.Match[str], _label: str = label) -> str:
            if _label == "card_number" and not _looks_like_card(match.group(0)):
                return match.group(0)
            removed.append(_label)
            return f"[REDACTED:{_label}]"

        out = pattern.sub(_sub, out)
    return out, removed


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Drop credential headers entirely; redact the values of what remains."""
    if not headers:
        return {}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if str(key).lower() in FORBIDDEN_HEADERS:
            continue
        safe[str(key).lower()] = redact_text(str(value))[0]
    return safe


def redact_mapping(data: Any, *, keep_emails: bool = False) -> Any:
    """Recursively redact a JSON-serialisable structure."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if str(key).lower() in SENSITIVE_KEYS:
                out[key] = "[REDACTED:sensitive_key]"
            else:
                out[key] = redact_mapping(value, keep_emails=keep_emails)
        return out
    if isinstance(data, (list, tuple)):
        return [redact_mapping(item, keep_emails=keep_emails) for item in data]
    if isinstance(data, str):
        return redact_text(data, keep_emails=keep_emails)[0]
    return data


def contains_secret(text: str) -> bool:
    """True when ``text`` still holds something that looks like a credential.

    Used as an assertion in tests and as a last-chance guard before a payload
    is handed to a provider adapter.
    """
    if not text:
        return False
    for label, pattern in _PATTERNS:
        if label in ("email", "phone", "card_number"):
            continue
        if pattern.search(text):
            return True
    return False


def scrub_exception(message: str, limit: int = 300) -> str:
    """Sanitise an exception message for storage in an ErrorEvent or report.

    Stack traces, absolute paths and credentials never reach the final report;
    what survives is the short, quotable reason a step failed.
    """
    if not message:
        return ""
    single_line = " ".join(str(message).split())
    single_line = re.sub(r"(?:[A-Za-z]:\\|/)(?:[\w .\-]+[\\/])+[\w .\-]+", "[path]", single_line)
    cleaned, _ = redact_text(single_line)
    return cleaned[:limit]


def summarise_removals(labels: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts
