#!/usr/bin/env python3
"""Detection scoring — compares normalized findings to gold standard.

Computes TP, FP, FN, precision, recall, F1 overall and per-category.
"""

from __future__ import annotations

import json
from typing import Any


def match_findings(
    agent_findings: list[dict],
    gold_findings: list[dict],
) -> dict[str, Any]:
    """Match agent findings against gold standard.

    Uses category-based matching: an agent finding matches a gold finding
    if they share the same category.

    Returns: {tp, fp, fn, precision, recall, f1, matches, false_positives, missed}
    """
    # Gold categories expected
    gold_categories = set()
    gold_by_cat = {}
    for gf in gold_findings:
        cat = gf.get("category", "other")
        gold_categories.add(cat)
        gold_by_cat[cat] = gf

    # Agent categories found
    agent_categories = set()
    agent_by_cat = {}
    for af in agent_findings:
        cat = af.get("category", "other")
        agent_categories.add(cat)
        agent_by_cat[cat] = af

    # Compute TP/FP/FN
    tp_cats = gold_categories & agent_categories
    fp_cats = agent_categories - gold_categories
    fn_cats = gold_categories - agent_categories

    # Filter out "other" from FP (don't penalize for extra SEO findings heavily)
    fp_cats_real = {c for c in fp_cats if c != "other"}

    tp = len(tp_cats)
    fp = len(fp_cats_real)
    fn = len(fn_cats)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    matches = []
    for cat in tp_cats:
        matches.append({
            "category": cat,
            "gold": gold_by_cat.get(cat, {}),
            "agent": agent_by_cat.get(cat, {}),
        })

    false_positives = []
    for cat in fp_cats_real:
        false_positives.append({
            "category": cat,
            "agent_finding": agent_by_cat.get(cat, {}),
        })

    missed = []
    for cat in fn_cats:
        missed.append({
            "category": cat,
            "gold_finding": gold_by_cat.get(cat, {}),
        })

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp_categories": sorted(tp_cats),
        "fp_categories": sorted(fp_cats_real),
        "fn_categories": sorted(fn_cats),
        "matches": matches,
        "false_positives": false_positives,
        "missed": missed,
    }


def score_false_positive_rate(
    agent_findings: list[dict],
    gold: dict,
) -> float:
    """Score false-positive performance (0-1, higher is better).

    For healthy sites (no expected findings), any finding is a false positive.
    For sites with expected findings, extra findings beyond expected are penalized.
    """
    max_acceptable = gold.get("max_acceptable_findings", 5)
    expected_count = len(gold.get("expected_findings", []))
    actual_count = len(agent_findings)

    # Don't count "other" category findings
    relevant_findings = [f for f in agent_findings if f.get("category") != "other"]
    relevant_count = len(relevant_findings)

    if expected_count == 0:
        # Healthy site: penalize any relevant finding
        if relevant_count == 0:
            return 1.0
        elif relevant_count <= 1:
            return 0.7
        elif relevant_count <= 2:
            return 0.4
        else:
            return max(0.0, 1.0 - relevant_count * 0.15)
    else:
        # Site with problems: penalize excess findings
        excess = max(0, relevant_count - max_acceptable)
        if excess == 0:
            return 1.0
        return max(0.0, 1.0 - excess * 0.1)


def score_per_category(
    agent_findings: list[dict],
    gold_findings: list[dict],
    categories: list[str],
) -> dict[str, dict]:
    """Compute detection metrics per category."""
    results = {}
    for cat in categories:
        gold_has = any(gf.get("category") == cat for gf in gold_findings)
        agent_has = any(af.get("category") == cat for af in agent_findings)

        if gold_has and agent_has:
            results[cat] = {"tp": 1, "fp": 0, "fn": 0, "status": "TP"}
        elif not gold_has and agent_has:
            results[cat] = {"tp": 0, "fp": 1, "fn": 0, "status": "FP"}
        elif gold_has and not agent_has:
            results[cat] = {"tp": 0, "fp": 0, "fn": 1, "status": "FN"}
        else:
            results[cat] = {"tp": 0, "fp": 0, "fn": 0, "status": "TN"}

    return results
