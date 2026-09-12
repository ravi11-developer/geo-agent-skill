#!/usr/bin/env python3
"""Evidence, action, and severity scoring."""

from __future__ import annotations

import re
from typing import Any


def score_evidence(finding: dict[str, Any]) -> int:
    """Score evidence quality 0-4.

    0 = absent / unsupported
    1 = vague assertion
    2 = somewhat specific
    3 = concrete and verifiable
    4 = directly measurable / reproducible
    """
    evidence = finding.get("evidence", "")
    if not evidence or len(evidence.strip()) < 5:
        return 0

    score = 1  # At least vague if non-empty

    # Specific indicators boost score
    specifics = [
        (r'https?://\S+', 1),           # Contains URL
        (r'HTTP \d{3}', 1),              # HTTP status code
        (r'\d+ (chars|characters|bytes|pages|images|links)', 1),  # Measurements
        (r'<\w+[^>]*>', 1),              # HTML element reference
        (r'"[^"]{10,}"', 0.5),           # Quoted text
        (r'\d+/\d+', 0.5),              # Ratios/fractions
        (r'©\s*\d{4}', 0.5),            # Specific years
        (r'\$([\d,]+)', 0.5),           # Dollar amounts
        (r'found \d+', 0.5),            # Counts
        (r'JSON-LD|json-ld|schema\.org', 0.5),  # Technical terms
    ]

    bonus = 0
    for pattern, points in specifics:
        if re.search(pattern, evidence, re.I):
            bonus += points

    score += min(3, bonus)  # Cap at 4 total
    return min(4, int(score))


def score_action(finding: dict[str, Any]) -> int:
    """Score suggested action quality 0-4.

    0 = wrong or harmful
    1 = generic
    2 = directionally correct
    3 = technically correct and actionable
    4 = technically precise, actionable, mechanism-sound, and prioritized
    """
    action = finding.get("suggested_action", {})
    if isinstance(action, str):
        summary = action
    else:
        summary = action.get("summary", "")

    if not summary or len(summary.strip()) < 5:
        return 0

    score = 1  # At least generic

    # Check for specificity indicators
    specifics = [
        (r'JSON-LD|json-ld|structured data|schema', 1),    # Technical terms
        (r'HTML|html|server-side|SSR|SSG', 0.5),            # Implementation detail
        (r'<\w+>|h1|meta|nav|header|footer', 0.5),          # Element references
        (r'machine-readable', 0.5),                          # Mechanism understanding
        (r'alt text|alt attribute', 0.5),                    # Specific attribute
        (r'name.*propert|canonical.*name', 0.5),            # Property references
        (r'breadcrumb|sitemap|navigation menu', 0.5),        # Specific components
        (r'publication date|update date|copyright', 0.5),    # Specific signals
    ]

    # Check for mechanism understanding (why this helps)
    mechanism = [
        (r'AI system|crawler|bot|search engine|assistant', 0.5),
        (r'discover|extract|trust|cite|represent', 0.5),
        (r'machine-readable|machine.readable', 0.5),
    ]

    bonus = 0
    for pattern, points in specifics:
        if re.search(pattern, summary, re.I):
            bonus += points

    mech_bonus = 0
    for pattern, points in mechanism:
        if re.search(pattern, summary, re.I):
            mech_bonus += points

    score += min(2, bonus)
    score += min(1, mech_bonus)

    # Penalize generic advice
    generic_patterns = [
        r'^improve seo\.?$',
        r'^fix the (issue|problem)\.?$',
        r'^update the (website|content)\.?$',
    ]
    for pattern in generic_patterns:
        if re.match(pattern, summary.strip(), re.I):
            return 1

    return min(4, int(score))


def score_severity_accuracy(agent_severity: str, gold_severity: str) -> float:
    """Score severity accuracy.

    1.0 = exact match
    0.5 = one level away
    0.0 = two or more levels away
    """
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    agent_rank = rank.get(agent_severity.lower(), 2)
    gold_rank = rank.get(gold_severity.lower(), 2)

    diff = abs(agent_rank - gold_rank)
    if diff == 0:
        return 1.0
    elif diff == 1:
        return 0.5
    else:
        return 0.0


def score_schema_validity(report: dict[str, Any]) -> float:
    """Score output schema validity (0-1)."""
    score = 0.0
    total_checks = 8

    # Required fields
    if report.get("site"):
        score += 1
    if report.get("audited_at"):
        score += 1
    if isinstance(report.get("summary"), dict):
        score += 1
    if isinstance(report.get("findings"), list):
        score += 1

    # Summary field validation
    summary = report.get("summary", {})
    if "total_findings" in summary:
        score += 1

    # Findings structure
    findings = report.get("findings", [])
    if findings:
        f = findings[0]
        if f.get("id"):
            score += 1
        if f.get("severity") in ("critical", "high", "medium", "low"):
            score += 1
        if isinstance(f.get("suggested_action"), dict):
            score += 1
    else:
        score += 3  # No findings is valid

    return round(score / total_checks, 4)
