#!/usr/bin/env python3
"""Proactive-recommendation scoring.

The Round 3 rubric rewards "useful recommendations beyond explicitly detected
defects", and every gold file already carries an ``expected_proactive`` list, but
``run_suite.py`` previously hard-coded ``proactive_score = 0.5`` for every agent.
This module grades that dimension for real.

It is deliberately generous about *where* advice lives, so agents that never
adopted a ``recommendations`` field are not punished for formatting: advice is
collected from any recommendation-shaped field, and also from low-severity
findings, which is how several agents express "nice to have" suggestions.
"""

from __future__ import annotations

import re
from typing import Any

RECOMMENDATION_FIELDS = (
    "recommendations", "proactive_recommendations", "proactive", "suggestions",
    "opportunities", "advice", "next_steps",
)
STOPWORDS = {
    "add", "the", "a", "an", "to", "for", "of", "and", "or", "in", "on", "with", "your",
    "that", "this", "it", "is", "are", "be", "as", "if", "so", "use", "using", "make",
    "consider", "should", "could", "page", "pages", "site", "website", "content", "data",
}


def _text_of(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts = [str(item.get(key, "")) for key in
                 ("title", "summary", "detail", "description", "recommendation", "action", "text")]
        return " ".join(p for p in parts if p)
    return ""


def collect_recommendations(report: dict[str, Any]) -> list[str]:
    """Every piece of proactive advice in a report, whatever field it lives in."""
    out: list[str] = []
    for field in RECOMMENDATION_FIELDS:
        value = report.get(field)
        if isinstance(value, list):
            out.extend(t for t in (_text_of(item) for item in value) if t.strip())
        elif isinstance(value, str) and value.strip():
            out.append(value)

    # Low-severity findings are advice in practice; count them too.
    for finding in report.get("findings", []) or []:
        if str(finding.get("severity", "")).lower() in ("low", "info", "informational", "suggestion"):
            action = finding.get("suggested_action", {})
            action_text = action.get("summary", "") if isinstance(action, dict) else str(action)
            out.append(f"{finding.get('title', '')} {action_text}")
    return [t for t in out if t.strip()]


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9\-]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def collect_finding_actions(report: dict[str, Any]) -> list[str]:
    """Remediation advice attached to detected defects (not 'beyond' them)."""
    out = []
    for finding in report.get("findings", []) or []:
        action = finding.get("suggested_action", {})
        text = action.get("summary", "") if isinstance(action, dict) else str(action)
        if text.strip():
            out.append(f"{finding.get('title', '')} {text}")
    return out


def score_proactive(report: dict[str, Any], gold: dict[str, Any]) -> float | None:
    """Fraction of the gold's expected proactive items the report covers.

    Standalone recommendations earn full credit; the same advice delivered only
    inside a detected defect's ``suggested_action`` earns half, because the rubric
    asks for recommendations *beyond* the defects the agent found. Returns
    ``None`` when the gold sets no expectation, so the site is excluded from the
    average rather than scored arbitrarily.
    """
    expected = gold.get("expected_proactive") or []
    if not expected:
        return None

    rec_keywords = [_keywords(text) for text in collect_recommendations(report)]
    action_keywords = [_keywords(text) for text in collect_finding_actions(report)]
    if not rec_keywords and not action_keywords:
        return 0.0

    def best_overlap(wanted: set[str], pool: list[set[str]]) -> float:
        return max((len(wanted & got) / len(wanted) for got in pool), default=0.0)

    covered = 0.0
    for item in expected:
        wanted = _keywords(item)
        if not wanted:
            continue
        rec_hit = best_overlap(wanted, rec_keywords)
        act_hit = best_overlap(wanted, action_keywords)
        if rec_hit >= 0.5:
            covered += 1.0
        elif act_hit >= 0.5:
            covered += 0.5
        elif rec_hit >= 0.34:
            covered += 0.5
        elif act_hit >= 0.34:
            covered += 0.25
    return round(min(1.0, covered / len(expected)), 4)
