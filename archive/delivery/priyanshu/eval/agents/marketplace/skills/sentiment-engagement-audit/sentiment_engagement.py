#!/usr/bin/env python3
"""Skill: sentiment-engagement-audit

Responsibility (and nothing else): **what the writing does to a reader.**

``engagement-audit`` answers a structural question - are there navigation
landmarks and internal links, can a visitor continue.  It says nothing about
whether the copy on the page is clear, consistent or trustworthy, and it is not
the place to start: navigation is measured from the DOM, tone is read from
prose, and mixing the two would make both harder to justify.  So this is a
separate specialist.

It emits *observations*, never findings.  An observation is a proposal with
citations attached; the orchestrator applies the promotion policy and decides
whether any of them are strong enough to be reported as a defect.  In
``semantic_shadow`` mode nothing is ever promoted, which is the default posture
for a new detector.

The skill is completely inert unless the LLM feature layer is switched on: with
``LLM_MODE=off`` it does no work, makes no call, and returns an empty result, so
the deterministic report is byte-for-byte what it was before this skill existed.
"""

from __future__ import annotations

import time
from typing import Any

from lib.contracts import SiteSnapshot, SkillResult, recommendation
from lib.llm.config import CAP_SEMANTIC
from lib.llm.evidence import build_evidence_pack

SKILL_ID = "sentiment-engagement-audit"


def run(context: dict[str, Any]) -> SkillResult:
    started = time.monotonic()
    result = SkillResult(skill=SKILL_ID)

    snapshot: SiteSnapshot | None = context.get("artifacts", {}).get("snapshot")
    if snapshot is None:
        result.error = "no snapshot in context"
        result.runtime_seconds = time.monotonic() - started
        return result

    engine = context.get("llm_engine")
    if engine is None or not engine.can(CAP_SEMANTIC):
        # Not an error: this is the shipped default.  The check is recorded so a
        # reader can tell "semantic analysis was off" from "semantic analysis
        # found nothing", which are very different statements about a site.
        result.checks = [{"check": "semantic_analysis_performed", "passed": True, "value": "disabled"}]
        result.runtime_seconds = time.monotonic() - started
        return result

    if not snapshot.ok_pages:
        result.error = "no retrievable pages in snapshot"
        result.runtime_seconds = time.monotonic() - started
        return result

    budget = getattr(engine.config, "crawl", None)
    try:
        pack = build_evidence_pack(snapshot, budget)
    except Exception as exc:  # noqa: BLE001
        # Normalising a site's markup is best-effort by nature. If it fails the
        # audit loses its semantic layer, which is an audit limitation - it is
        # never a defect of the site, and it never stops the report.
        result.error = f"evidence pack could not be built: {type(exc).__name__}: {exc}"
        result.checks = [{"check": "semantic_analysis_performed", "passed": False,
                          "value": "evidence pack failed"}]
        result.runtime_seconds = time.monotonic() - started
        return result
    result.artifacts["evidence_pack"] = pack
    if pack.notes.get("pages_skipped"):
        result.checks.append({"check": "pages_skipped_by_normaliser",
                              "value": pack.notes["pages_skipped"]})

    if pack.section_count == 0:
        result.checks = [{"check": "semantic_analysis_performed", "passed": True,
                          "value": "no quotable copy survived boilerplate removal"}]
        result.recommendations.append(recommendation(
            "Publish the page's substance as body copy",
            "After navigation, scripts and repeated site chrome were removed, no quotable prose "
            "remained on the crawled pages. An answer engine has nothing to extract from a page "
            "whose only text is chrome, and a visitor has nothing to read.",
            "engagement", "high",
        ))
        result.runtime_seconds = time.monotonic() - started
        return result

    deterministic_findings = context.get("deterministic_findings", [])
    coverage = context.get("deterministic_coverage", {})

    observations, analysed = engine.analyse_semantics(
        pack, deterministic_findings=deterministic_findings, coverage=coverage,
    )

    result.artifacts["semantic_observations"] = observations
    result.artifacts["semantic_pages_analyzed"] = [page.page_id for page in analysed]
    result.checks = [
        {"check": "semantic_analysis_performed", "passed": True, "value": len(analysed)},
        {"check": "evidence_sections_offered", "value": pack.section_count},
        {"check": "observations_validated", "value": len(observations)},
        {"check": "aspects_covered",
         "value": sorted({observation["aspect"] for observation in observations})},
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
