#!/usr/bin/env python3
"""Finding normalizer — maps agent findings to benchmark categories."""

from __future__ import annotations

import re
from typing import Any

# Canonical benchmark categories
CATEGORIES = {
    "crawlability", "rendering", "content_extraction", "structured_data",
    "non_text_facts", "freshness", "corroboration", "entity_identity",
    "engagement", "orientation", "other",
}

# Mapping from agent-used terms to benchmark categories
CATEGORY_ALIASES = {
    # crawlability
    "crawlability": "crawlability",
    "crawl": "crawlability",
    "http": "crawlability",
    "accessibility": "crawlability",
    "technical-seo": "crawlability",
    "technical_seo": "crawlability",
    # rendering
    "rendering": "rendering",
    "javascript": "rendering",
    "js": "rendering",
    "client-side": "rendering",
    "spa": "rendering",
    # content extraction
    "content_extraction": "content_extraction",
    "content-extraction": "content_extraction",
    "content-quality": "content_extraction",
    "content_quality": "content_extraction",
    "extraction": "content_extraction",
    # structured data
    "structured_data": "structured_data",
    "structured-data": "structured_data",
    "schema": "structured_data",
    "json-ld": "structured_data",
    "jsonld": "structured_data",
    # non-text facts
    "non_text_facts": "non_text_facts",
    "non-text-facts": "non_text_facts",
    "image": "non_text_facts",
    "non_text": "non_text_facts",
    # freshness
    "freshness": "freshness",
    "stale": "freshness",
    "outdated": "freshness",
    "date": "freshness",
    # corroboration
    "corroboration": "corroboration",
    "trust": "corroboration",
    # entity identity
    "entity_identity": "entity_identity",
    "entity-identity": "entity_identity",
    "entity": "entity_identity",
    "brand": "entity_identity",
    "identity": "entity_identity",
    # engagement
    "engagement": "engagement",
    "navigation": "engagement",
    "orientation": "engagement",
    "on-page-seo": "other",
    "on_page_seo": "other",
    "performance": "other",
    "mobile": "other",
    "security": "other",
    "link-health": "other",
    "link_health": "other",
    "international-seo": "other",
    "international_seo": "other",
}

# Keywords that help classify findings by title/evidence
KEYWORD_CLASSIFIERS = [
    (["javascript", "js", "client-side", "noscript", "render", "spa", "server-side"],
     "rendering"),
    (["json-ld", "json ld", "structured data", "schema.org", "schema", "machine-readable"],
     "structured_data"),
    (["image", "img", "alt text", "non-text", "infographic", "visual only"],
     "non_text_facts"),
    (["entity", "brand name", "company name", "inconsistent name", "ambiguity", "conflicting name"],
     "entity_identity"),
    (["stale", "outdated", "freshness", "old date", "copyright year", "last updated"],
     "freshness"),
    (["navigation", "nav", "breadcrumb", "internal link", "menu", "orientation", "engagement"],
     "engagement"),
    (["crawl", "robots", "sitemap", "http error", "404", "500", "unreachable"],
     "crawlability"),
]


def classify_finding(finding: dict[str, Any]) -> str:
    """Classify a finding into a benchmark category."""
    # First try the explicit category
    raw_category = finding.get("_category") or finding.get("category", "")
    normalized = raw_category.lower().strip().replace(" ", "_").replace("-", "_")

    if normalized in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[normalized]

    # Try partial matching
    for alias, cat in CATEGORY_ALIASES.items():
        if alias in normalized:
            return cat

    # Fall back to keyword classification from title and evidence
    title = (finding.get("title") or "").lower()
    evidence = (finding.get("evidence") or "").lower()
    combined = f"{title} {evidence}"

    for keywords, category in KEYWORD_CLASSIFIERS:
        if any(kw in combined for kw in keywords):
            return category

    return "other"


def normalize_severity(severity: str) -> str:
    """Normalize severity to one of: critical, high, medium, low."""
    s = severity.lower().strip()
    if s in ("critical", "high", "medium", "low"):
        return s
    if s in ("severe", "blocker"):
        return "critical"
    if s in ("important", "major", "warning"):
        return "high"
    if s in ("moderate", "minor"):
        return "medium"
    if s in ("info", "informational", "trivial", "suggestion"):
        return "low"
    return "medium"  # default


def normalize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single finding to the benchmark schema."""
    return {
        "id": finding.get("id", ""),
        "title": finding.get("title", ""),
        "category": classify_finding(finding),
        "original_category": finding.get("_category") or finding.get("category", ""),
        "severity": normalize_severity(finding.get("severity", "medium")),
        "evidence": finding.get("evidence", ""),
        "suggested_action": finding.get("suggested_action", {}),
    }


def normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize an entire agent report."""
    findings = report.get("findings", [])
    normalized = [normalize_finding(f) for f in findings]

    # Filter out duplicates by category (keep first/highest severity)
    seen = {}
    deduped = []
    for f in normalized:
        cat = f["category"]
        if cat not in seen:
            seen[cat] = f
            deduped.append(f)
        else:
            # Keep the higher severity one
            sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if sev_rank.get(f["severity"], 0) > sev_rank.get(seen[cat]["severity"], 0):
                deduped.remove(seen[cat])
                seen[cat] = f
                deduped.append(f)

    return {
        "site": report.get("site", ""),
        "agent": report.get("_agent", ""),
        "audited_at": report.get("audited_at", ""),
        "original_finding_count": len(findings),
        "normalized_finding_count": len(deduped),
        "findings": deduped,
    }
