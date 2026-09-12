#!/usr/bin/env python3
"""Overall scoring and leaderboard evaluation module.

Combines detection, evidence, action, severity, schema validity, and runtime
into weighted overall benchmark scores.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Default benchmark weights
DEFAULT_WEIGHTS = {
    "detection_f1": 0.20,
    "false_positive": 0.10,
    "evidence": 0.15,
    "actions": 0.20,
    "severity": 0.10,
    "proactive": 0.10,
    "schema": 0.05,
    "runtime": 0.05,
    "generalization": 0.05,
}


def calculate_overall_score(metrics: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Calculate weighted overall score (0 - 100) from aggregated metrics dictionary."""
    w = weights or DEFAULT_WEIGHTS

    det = metrics.get("detection", {})
    f1 = det.get("f1", 0.0)
    fp_score = metrics.get("false_positive_score", 0.0)
    evidence_score = metrics.get("evidence_score", 0.0)
    action_score = metrics.get("action_score", 0.0)
    severity_score = metrics.get("severity_score", 0.0)
    proactive_score = metrics.get("proactive_score", 0.5)
    schema_score = metrics.get("schema_score", 1.0)
    runtime_score = metrics.get("runtime_score", 1.0)
    generalization_score = metrics.get("generalization_score", 0.5)

    overall = (
        w["detection_f1"] * f1 +
        w["false_positive"] * fp_score +
        w["evidence"] * evidence_score +
        w["actions"] * action_score +
        w["severity"] * severity_score +
        w["proactive"] * proactive_score +
        w["schema"] * schema_score +
        w["runtime"] * runtime_score +
        w["generalization"] * generalization_score
    )
    return round(overall * 100, 2)


def main():
    parser = argparse.ArgumentParser(description="Calculate or inspect benchmark leaderboard scores")
    parser.add_argument("--results", nargs="+", required=True, help="Path(s) to benchmark JSON result files")
    args = parser.parse_args()

    for path in args.results:
        if not os.path.exists(path):
            print(f"File not found: {path}", file=sys.stderr)
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"\nResults from: {path}")
        leaderboard = data.get("leaderboard", {})
        for agent, scores in sorted(leaderboard.items(), key=lambda x: x[1].get("overall", 0), reverse=True):
            det = scores.get("detection", {})
            print(f"  {agent:<16} Overall: {scores.get('overall', 0):>5.1f} | F1: {det.get('f1', 0):.3f} | FP: {scores.get('false_positive_score', 0):.3f} | Evidence: {scores.get('evidence_score', 0):.3f} | Actions: {scores.get('action_score', 0):.3f}")


if __name__ == "__main__":
    main()
