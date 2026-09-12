#!/usr/bin/env python3
"""Skill: audit-orchestrator (marketplace entrypoint)

Plans the audit, invokes each installed specialist skill in dependency order,
then performs the three composition passes that a multi-skill marketplace needs
and a single monolithic agent does not:

  1. **Merge**        - one finding per category; overlapping detections from
                        different skills are merged, keeping the strongest
                        evidence rather than duplicating the defect.
  2. **Prioritise**   - severity is a *relative* signal. When a site has several
                        simultaneous defects, blocking ones (retrieval,
                        rendering, extraction, identity) keep their severity and
                        secondary ones are capped at medium, so the report tells
                        the owner what to fix first.
  3. **Report**       - stable ids, coverage of every category (including the
                        clean ones, which is how a reader can tell "checked and
                        healthy" from "not checked"), and proactive advice kept
                        strictly separate from defects.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from lib.contracts import (
    BLOCKING_CATEGORIES,
    CATEGORIES,
    SECONDARY_CATEGORIES,
    SEVERITIES,
    SEVERITY_RANK,
    SkillResult,
    recommendation,
)
from lib.loader import load_manifest, load_skills

SKILL_ID = "audit-orchestrator"
SEVERITY_COST = {"critical": 34, "high": 22, "medium": 11, "low": 4}
CATEGORY_ORDER = {name: index for index, name in enumerate(CATEGORIES)}
ID_PREFIX = {
    "crawlability": "CRAWL", "rendering": "REND", "content_extraction": "EXTR",
    "structured_data": "SCHEMA", "non_text_facts": "IMG", "freshness": "FRESH",
    "entity_identity": "ENTITY", "corroboration": "TRUST", "engagement": "ENGAGE",
}


# ---------------------------------------------------------------------------
# Composition passes
# ---------------------------------------------------------------------------

def merge_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One finding per category; the strongest evidence wins, the rest is kept
    as corroboration so nothing a skill measured is thrown away."""
    by_category: dict[str, dict[str, Any]] = {}
    for finding in findings:
        category = finding["category"]
        current = by_category.get(category)
        if current is None:
            by_category[category] = finding
            continue
        stronger, weaker = (
            (finding, current)
            if (SEVERITY_RANK[finding["severity"]], len(finding["evidence"]))
            > (SEVERITY_RANK[current["severity"]], len(current["evidence"]))
            else (current, finding)
        )
        stronger.setdefault("corroborated_by", []).append(
            {"skill": weaker.get("detected_by", ""), "evidence": weaker["evidence"]}
        )
        stronger["locations"] = sorted(set(stronger["locations"]) | set(weaker["locations"]))
        by_category[category] = stronger
    return list(by_category.values())


def prioritise(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank findings and express prioritisation through severity."""
    categories = {f["category"] for f in findings}
    multi_fault = len(findings) >= 4 and bool(categories & BLOCKING_CATEGORIES)

    for finding in findings:
        if multi_fault and finding["category"] in SECONDARY_CATEGORIES and finding["severity"] == "high":
            finding["severity"] = "medium"
            finding["suggested_action"]["priority"] = "medium"
            finding["severity_note"] = (
                "Down-ranked to medium: this site has blocking machine-access defects that must be fixed first; "
                "the underlying measurement is unchanged."
            )

    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], CATEGORY_ORDER[f["category"]]))
    for index, finding in enumerate(findings, start=1):
        finding["id"] = f"MKT-{ID_PREFIX[finding['category']]}-{index:02d}"
        finding["rank"] = index
    return findings


def dedupe_recommendations(recommendations: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique = []
    for rec in recommendations:
        key = rec["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda r: order.get(r.get("effort", "medium"), 1))
    return unique[:limit]


def health_score(findings: list[dict[str, Any]]) -> int:
    score = 100 - sum(SEVERITY_COST[f["severity"]] for f in findings)
    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_audit(url: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    config = config or {}
    manifest = load_manifest()
    skills = load_skills(manifest, kinds=("audit",))

    context: dict[str, Any] = {
        "url": url,
        "artifacts": {},
        "config": config,
        "max_pages": config.get("max_pages", manifest.get("safety", {}).get("max_pages_per_run", 12)),
        "budget_seconds": config.get("budget_seconds",
                                     manifest.get("safety", {}).get("max_runtime_seconds", 60)),
    }

    raw_findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    telemetry: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    for skill in skills:
        skill_started = time.monotonic()
        try:
            result: SkillResult = skill.run(context)
            context["artifacts"].update(result.artifacts)
            raw_findings.extend(result.findings)
            recommendations.extend(result.recommendations)
            checks.extend({"skill": skill.id, **check} for check in result.checks)
            telemetry.append({
                "skill": skill.id,
                "version": skill.version,
                "status": "error" if result.error else "ok",
                "error": result.error,
                "findings": len(result.findings),
                "recommendations": len(result.recommendations),
                "runtime_seconds": round(time.monotonic() - skill_started, 3),
                "provides": skill.provides,
            })
        except Exception as exc:  # noqa: BLE001 - one broken skill must not fail the audit
            telemetry.append({
                "skill": skill.id,
                "version": skill.version,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "findings": 0,
                "recommendations": 0,
                "runtime_seconds": round(time.monotonic() - skill_started, 3),
                "provides": skill.provides,
            })

    findings = prioritise(merge_findings(raw_findings))
    
    # Proactive Baseline: Always suggest sameAs links for strong AI entity recognition
    recommendations.append(recommendation(
        title="Add `sameAs` links to social profiles in Organization JSON-LD",
        detail=(
            "Even if your site is otherwise healthy, adding `sameAs` links to your LinkedIn, "
            "Crunchbase, and Twitter profiles inside your Organization JSON-LD explicitly binds "
            "those distinct sources together into one entity graph. AI assistants heavily rely on "
            "this to merge facts from different domains into a single confident answer."
        ),
        category="entity_identity",
        effort="low",
    ))
    
    recommendations = dedupe_recommendations(recommendations)

    flagged = {f["category"] for f in findings}
    covered_by_skill = {c for skill in skills for c in skill.provides}
    coverage = {
        category: ("flagged" if category in flagged
                   else "clean" if category in covered_by_skill
                   else "not_checked")
        for category in CATEGORIES
    }

    # The published schema requires a flat count per severity, always present
    # (zero when absent) so consumers can read summary["high"] without guarding.
    # ``by_severity`` is kept as a convenience view over the same numbers.
    by_severity = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITIES}

    snapshot = context["artifacts"].get("snapshot")
    elapsed = time.monotonic() - started

    return {
        "site": url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "agent": "marketplace",
        "marketplace": {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "entrypoint": SKILL_ID,
            "skills_invoked": [s.id for s in skills],
            "read_only": True,
        },
        "summary": {
            "total_findings": len(findings),
            "critical": by_severity["critical"],
            "high": by_severity["high"],
            "medium": by_severity["medium"],
            "low": by_severity["low"],
            "by_severity": by_severity,
            "categories_flagged": sorted(flagged),
            "categories_clean": sorted(c for c, state in coverage.items() if state == "clean"),
            "health_score": health_score(findings),
            "top_priority": findings[0]["title"] if findings else "No AI-discoverability defects detected",
            "pages_crawled": len(snapshot.pages) if snapshot else 0,
            "pages_retrievable": len(snapshot.ok_pages) if snapshot else 0,
            "recommendation_count": len(recommendations),
            "runtime_seconds": round(elapsed, 3),
        },
        "findings": findings,
        "recommendations": recommendations,
        "coverage": coverage,
        "checks_performed": checks,
        "telemetry": {
            "skills": telemetry,
            "runtime_seconds": round(elapsed, 3),
            "pages_fetched": len(snapshot.pages) if snapshot else 0,
            "broken_links": snapshot.broken_links if snapshot else [],
        },
    }


def run(context: dict[str, Any]) -> dict[str, Any]:
    """Uniform skill interface, so the orchestrator can itself be composed."""
    return run_audit(context["url"], context.get("config"))
