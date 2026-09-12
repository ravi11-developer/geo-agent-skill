#!/usr/bin/env python3
"""Complete benchmark runner — runs all agents on all sites and produces a leaderboard.

Usage:
    python eval/runners/run_suite.py                    # Run all agents on all synthetic sites
    python eval/runners/run_suite.py --agent deterministic  # Run single agent
    python eval/runners/run_suite.py --site site-003-js-only-facts  # Run all agents on one site
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from bench import registry
from bench.scoring.normalize import normalize_report, normalize_finding
from bench.scoring.detection import match_findings, score_false_positive_rate, score_per_category
from bench.scoring.evidence import score_evidence, score_action, score_severity_accuracy, score_schema_validity
from bench.scoring.proactive import score_proactive


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNTHETIC_SITES_DIR = os.path.join(PROJECT_ROOT, "bench", "sites", "synthetic")
GOLD_DIR = os.path.join(PROJECT_ROOT, "bench", "gold")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")
AGENTS = sorted(registry.discover())
PORT = 9500

# Weights for overall score
WEIGHTS = {
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


# ---------------------------------------------------------------------------
# Synthetic server
# ---------------------------------------------------------------------------

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SYNTHETIC_SITES_DIR, **kwargs)
    def log_message(self, *args):
        pass
    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html><html><body><h1>Synthetic Server</h1></body></html>")
            return
        super().do_GET()


def start_server():
    """Start synthetic site server in background."""
    server = http.server.HTTPServer(("127.0.0.1", PORT), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    return server


# ---------------------------------------------------------------------------
# Load gold standards
# ---------------------------------------------------------------------------

def load_gold_standards() -> dict[str, dict]:
    """Load all gold standard files."""
    golds = {}
    for fname in os.listdir(GOLD_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(GOLD_DIR, fname), encoding="utf-8") as f:
                data = json.load(f)
                golds[data["site_id"]] = data
    return golds


def get_synthetic_sites() -> list[str]:
    """Get list of synthetic site IDs."""
    return sorted(d for d in os.listdir(SYNTHETIC_SITES_DIR)
                  if os.path.isdir(os.path.join(SYNTHETIC_SITES_DIR, d)))


# ---------------------------------------------------------------------------
# Run a single agent on a single site
# ---------------------------------------------------------------------------

def run_single(agent_name: str, site_id: str, gold: dict) -> dict[str, Any]:
    """Run one agent on one site and score it."""
    url = f"http://localhost:{PORT}/{site_id}/"

    # Import and run agent
    result = {
        "agent": agent_name,
        "site": site_id,
        "url": url,
        "report": None,
        "runtime_seconds": 0,
        "error": None,
        "scores": {},
    }

    # Agents run out-of-process: each is a self-contained marketplace with its
    # own `lib` package, so two of them cannot share one interpreter.
    run = registry.load(agent_name).run(url)
    result["report"] = run["report"]
    result["runtime_seconds"] = run["runtime_seconds"]
    if run["error"]:
        result["error"] = run["error"]
        return result
    report = result["report"]

    # Normalize findings
    report["_agent"] = agent_name
    normalized = normalize_report(report)
    agent_findings = normalized["findings"]

    # Gold expected findings
    gold_findings = []
    for ef in gold.get("expected_findings", []):
        gold_findings.append({
            "category": ef["category"],
            "description": ef.get("description", ""),
            "severity": ef.get("severity", "medium"),
        })

    # Detection scoring
    detection = match_findings(agent_findings, gold_findings)
    result["scores"]["detection"] = detection

    # False positive scoring
    fp_score = score_false_positive_rate(agent_findings, gold)
    result["scores"]["false_positive"] = fp_score

    # Evidence scoring (for true positives)
    evidence_scores = []
    for match in detection.get("matches", []):
        agent_f = match.get("agent", {})
        es = score_evidence(agent_f)
        evidence_scores.append(es)
    avg_evidence = sum(evidence_scores) / len(evidence_scores) if evidence_scores else 0
    result["scores"]["evidence"] = {
        "scores": evidence_scores,
        "average": round(avg_evidence, 2),
        "normalized": round(avg_evidence / 4.0, 4),
    }

    # Action scoring
    action_scores = []
    for match in detection.get("matches", []):
        agent_f = match.get("agent", {})
        as_ = score_action(agent_f)
        action_scores.append(as_)
    avg_action = sum(action_scores) / len(action_scores) if action_scores else 0
    result["scores"]["actions"] = {
        "scores": action_scores,
        "average": round(avg_action, 2),
        "normalized": round(avg_action / 4.0, 4),
    }

    # Severity scoring
    severity_scores = []
    for match in detection.get("matches", []):
        agent_f = match.get("agent", {})
        gold_f = match.get("gold", {})
        if gold_f.get("severity") and agent_f.get("severity"):
            ss = score_severity_accuracy(agent_f["severity"], gold_f["severity"])
            severity_scores.append(ss)
    avg_severity = sum(severity_scores) / len(severity_scores) if severity_scores else 0.5
    result["scores"]["severity"] = {
        "scores": severity_scores,
        "average": round(avg_severity, 4),
    }

    # Proactive recommendation quality (graded against gold expected_proactive;
    # None when the gold sets no expectation, so the site is excluded)
    result["scores"]["proactive"] = score_proactive(report, gold)
    result["holdout"] = bool(gold.get("holdout", False))

    # Schema validity
    schema_score = score_schema_validity(report)
    result["scores"]["schema"] = schema_score

    # Runtime score (1.0 if under 30s, decreasing after)
    runtime = result["runtime_seconds"]
    if runtime <= 30:
        runtime_score = 1.0
    elif runtime <= 120:
        runtime_score = 1.0 - (runtime - 30) / 180
    else:
        runtime_score = max(0.0, 1.0 - runtime / 300)
    result["scores"]["runtime"] = round(runtime_score, 4)

    return result


# ---------------------------------------------------------------------------
# Aggregate scores across sites
# ---------------------------------------------------------------------------

def _detection_totals(results: list[dict]) -> tuple[float, float, float]:
    """Micro-averaged precision / recall / F1 over a set of site results."""
    tp = sum(r["scores"]["detection"]["tp"] for r in results)
    fp = sum(r["scores"]["detection"]["fp"] for r in results)
    fn = sum(r["scores"]["detection"]["fn"] for r in results)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def aggregate_scores(site_results: list[dict]) -> dict[str, Any]:
    """Aggregate scores across all sites for one agent.

    Scoring v2 changes three things versus the original harness, applied
    identically to every agent:

    1. **Evidence, action and severity quality are averaged over the sites where
       the agent actually matched a gold finding.** Previously a healthy site (no
       gold findings, therefore no true positives) contributed a hard 0 to the
       evidence and action averages and a hard 0.5 to severity, which capped a
       perfect agent at 0.70 / 0.85 and measured the dataset rather than the agent.
    2. **proactive_score is graded** against each gold's ``expected_proactive``
       list instead of being hard-coded to 0.5.
    3. **generalization_score is measured** on the held-out sites (gold flag
       ``holdout``), which no agent was developed against, instead of being
       hard-coded to 0.5. Headline detection metrics come from the development
       sites, so the two numbers stay independent.

    ``overall_legacy`` reproduces the original formula on the development sites,
    so historical leaderboard numbers remain comparable.
    """
    valid = [r for r in site_results if r.get("report") is not None]
    if not valid:
        return {"overall": 0, "overall_legacy": 0, "error": "All sites failed"}

    dev = [r for r in valid if not r.get("holdout")]
    holdout = [r for r in valid if r.get("holdout")]
    dev = dev or valid

    precision, recall, f1 = _detection_totals(dev)
    _, _, holdout_f1 = _detection_totals(holdout) if holdout else (0.0, 0.0, 0.5)

    total_tp = sum(r["scores"]["detection"]["tp"] for r in dev)
    total_fp = sum(r["scores"]["detection"]["fp"] for r in dev)
    total_fn = sum(r["scores"]["detection"]["fn"] for r in dev)

    scored = [r for r in valid if r["scores"]["evidence"]["scores"]]
    avg_evidence = (sum(r["scores"]["evidence"]["normalized"] for r in scored) / len(scored)) if scored else 0.0
    avg_actions = (sum(r["scores"]["actions"]["normalized"] for r in scored) / len(scored)) if scored else 0.0

    avg_fp = sum(r["scores"]["false_positive"] for r in valid) / len(valid)
    # Severity accuracy is only defined where a gold finding was matched; sites
    # with no true positives previously contributed a hard 0.5 default.
    sev_sites = [r for r in valid if r["scores"]["severity"]["scores"]]
    avg_severity = (sum(r["scores"]["severity"]["average"] for r in sev_sites) / len(sev_sites)) if sev_sites else 0.5
    avg_schema = sum(r["scores"]["schema"] for r in valid) / len(valid)
    avg_runtime_score = sum(r["scores"]["runtime"] for r in valid) / len(valid)
    avg_runtime_sec = sum(r["runtime_seconds"] for r in valid) / len(valid)

    graded_proactive = [r["scores"].get("proactive") for r in valid if r["scores"].get("proactive") is not None]
    proactive_score = sum(graded_proactive) / len(graded_proactive) if graded_proactive else 0.5
    generalization_score = holdout_f1 if holdout else 0.5

    overall = (
        WEIGHTS["detection_f1"] * f1 +
        WEIGHTS["false_positive"] * avg_fp +
        WEIGHTS["evidence"] * avg_evidence +
        WEIGHTS["actions"] * avg_actions +
        WEIGHTS["severity"] * avg_severity +
        WEIGHTS["proactive"] * proactive_score +
        WEIGHTS["schema"] * avg_schema +
        WEIGHTS["runtime"] * avg_runtime_score +
        WEIGHTS["generalization"] * generalization_score
    )

    # Original formula, development sites only, for continuity with earlier runs.
    legacy_evidence = sum(r["scores"]["evidence"]["normalized"] for r in dev) / len(dev)
    legacy_actions = sum(r["scores"]["actions"]["normalized"] for r in dev) / len(dev)
    legacy_fp = sum(r["scores"]["false_positive"] for r in dev) / len(dev)
    legacy_severity = sum(r["scores"]["severity"]["average"] for r in dev) / len(dev)
    legacy_schema = sum(r["scores"]["schema"] for r in dev) / len(dev)
    legacy_runtime = sum(r["scores"]["runtime"] for r in dev) / len(dev)
    overall_legacy = (
        WEIGHTS["detection_f1"] * f1 +
        WEIGHTS["false_positive"] * legacy_fp +
        WEIGHTS["evidence"] * legacy_evidence +
        WEIGHTS["actions"] * legacy_actions +
        WEIGHTS["severity"] * legacy_severity +
        WEIGHTS["proactive"] * 0.5 +
        WEIGHTS["schema"] * legacy_schema +
        WEIGHTS["runtime"] * legacy_runtime +
        WEIGHTS["generalization"] * 0.5
    )

    return {
        "sites_tested": len(valid),
        "sites_failed": len(site_results) - len(valid),
        "dev_sites": len(dev),
        "holdout_sites": len(holdout),
        "detection": {
            "tp": total_tp, "fp": total_fp, "fn": total_fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "holdout_detection_f1": round(holdout_f1, 4),
        "false_positive_score": round(avg_fp, 4),
        "evidence_score": round(avg_evidence, 4),
        "action_score": round(avg_actions, 4),
        "severity_score": round(avg_severity, 4),
        "proactive_score": round(proactive_score, 4),
        "generalization_score": round(generalization_score, 4),
        "schema_score": round(avg_schema, 4),
        "runtime_score": round(avg_runtime_score, 4),
        "avg_runtime_seconds": round(avg_runtime_sec, 2),
        "overall": round(overall * 100, 1),
        "overall_legacy": round(overall_legacy * 100, 1),
    }


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def print_leaderboard(results: dict[str, dict]):
    """Print a formatted leaderboard."""
    print("\n" + "=" * 110)
    print("AGENT BENCHMARKING LEADERBOARD")
    print("=" * 110)

    header = (f"{'Agent':<16} {'F1':>6} {'Prec':>6} {'Rec':>6} {'FP':>6} {'Evid':>6} {'Act':>6} "
              f"{'Sev':>6} {'Proact':>7} {'Genrl':>7} {'Runtime':>8} {'Overall':>8} {'Legacy':>7}")
    print(header)
    print("-" * 110)

    # Sort by overall score
    sorted_agents = sorted(results.items(), key=lambda x: x[1].get("overall", 0), reverse=True)

    for agent, scores in sorted_agents:
        det = scores.get("detection", {})
        line = (
            f"{agent:<16} "
            f"{det.get('f1', 0):>6.3f} "
            f"{det.get('precision', 0):>6.3f} "
            f"{det.get('recall', 0):>6.3f} "
            f"{scores.get('false_positive_score', 0):>6.3f} "
            f"{scores.get('evidence_score', 0):>6.3f} "
            f"{scores.get('action_score', 0):>6.3f} "
            f"{scores.get('severity_score', 0):>6.3f} "
            f"{scores.get('proactive_score', 0):>7.3f} "
            f"{scores.get('generalization_score', 0):>7.3f} "
            f"{scores.get('avg_runtime_seconds', 0):>7.1f}s "
            f"{scores.get('overall', 0):>8.1f} "
            f"{scores.get('overall_legacy', 0):>7.1f}"
        )
        print(line)

    print("=" * 110)

    # Winner
    if sorted_agents:
        winner = sorted_agents[0]
        print(f"\nWINNER: {winner[0]} (Overall: {winner[1].get('overall', 0):.1f}/100)")
    print()


def print_error_analysis(all_results: dict[str, list[dict]]):
    """Print detailed error analysis per agent."""
    print("\n" + "=" * 80)
    print("ERROR ANALYSIS")
    print("=" * 80)

    for agent, site_results in all_results.items():
        print(f"\n{'-' * 60}")
        print(f"Agent: {agent}")
        print(f"{'-' * 60}")

        fps_by_cat = {}
        fns_by_cat = {}
        errors = []

        for r in site_results:
            if r.get("error"):
                errors.append(f"  {r['site']}: {r['error'][:80]}")
                continue

            detection = r.get("scores", {}).get("detection", {})
            for fp in detection.get("false_positives", []):
                cat = fp.get("category", "?")
                fps_by_cat.setdefault(cat, []).append(r["site"])
            for fn in detection.get("missed", []):
                cat = fn.get("category", "?")
                fns_by_cat.setdefault(cat, []).append(r["site"])

        if fps_by_cat:
            print("  FALSE POSITIVES:")
            for cat, sites in sorted(fps_by_cat.items()):
                print(f"    {cat}: on {', '.join(sites)}")
        else:
            print("  FALSE POSITIVES: none")

        if fns_by_cat:
            print("  FALSE NEGATIVES:")
            for cat, sites in sorted(fns_by_cat.items()):
                print(f"    {cat}: missed on {', '.join(sites)}")
        else:
            print("  FALSE NEGATIVES: none")

        if errors:
            print("  ERRORS:")
            for e in errors:
                print(e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run benchmark suite")
    parser.add_argument("--agent", help="Run only this agent")
    parser.add_argument("--site", help="Run only this site")
    parser.add_argument("--output", default=None, help="Save results JSON to file")
    args = parser.parse_args()

    # Start server
    print("Starting synthetic site server...")
    server = start_server()

    # Load gold standards
    golds = load_gold_standards()
    sites = get_synthetic_sites()

    if args.site:
        sites = [s for s in sites if s == args.site]

    agents = AGENTS
    if args.agent:
        agents = [args.agent]

    print(f"Running {len(agents)} agents on {len(sites)} sites")
    print(f"Gold standards loaded: {len(golds)}")
    print()

    # Run all combinations
    all_results: dict[str, list[dict]] = {}
    aggregated: dict[str, dict] = {}

    for agent in agents:
        print(f"{'=' * 50}")
        print(f"Running agent: {agent}")
        print(f"{'=' * 50}")

        site_results = []
        for site_id in sites:
            gold = golds.get(site_id, {"expected_findings": [], "max_acceptable_findings": 5})

            print(f"  {site_id}...", end=" ", flush=True)
            result = run_single(agent, site_id, gold)

            if result.get("error"):
                print(f"ERROR: {result['error'][:60]}")
            else:
                det = result["scores"]["detection"]
                print(f"TP={det['tp']} FP={det['fp']} FN={det['fn']} "
                      f"F1={det['f1']:.2f} "
                      f"evidence={result['scores']['evidence']['average']:.1f} "
                      f"runtime={result['runtime_seconds']:.1f}s")

            site_results.append(result)

        all_results[agent] = site_results
        aggregated[agent] = aggregate_scores(site_results)

    # Print results
    print_leaderboard(aggregated)
    print_error_analysis(all_results)

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_file = args.output or os.path.join(RESULTS_DIR, f"benchmark_{timestamp}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": agents,
            "sites": sites,
            "leaderboard": aggregated,
            "detailed_results": {
                agent: [
                    {k: v for k, v in r.items() if k != "report"}
                    for r in results
                ]
                for agent, results in all_results.items()
            },
        }, f, indent=2, default=str)
    print(f"Results saved to: {output_file}")

    # Also save leaderboard CSV
    csv_file = os.path.join(RESULTS_DIR, f"leaderboard_{timestamp}.csv")
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("Agent,Detection_F1,Precision,Recall,FP_Score,Evidence,Actions,Severity,Proactive,Generalization,Avg_Runtime,Overall,Overall_Legacy\n")
        for agent in sorted(aggregated.keys(), key=lambda a: aggregated[a].get("overall", 0), reverse=True):
            s = aggregated[agent]
            det = s.get("detection", {})
            f.write(f"{agent},{det.get('f1',0):.4f},{det.get('precision',0):.4f},{det.get('recall',0):.4f},"
                    f"{s.get('false_positive_score',0):.4f},{s.get('evidence_score',0):.4f},"
                    f"{s.get('action_score',0):.4f},{s.get('severity_score',0):.4f},"
                    f"{s.get('proactive_score',0):.4f},{s.get('generalization_score',0):.4f},"
                    f"{s.get('avg_runtime_seconds',0):.1f},{s.get('overall',0):.1f},{s.get('overall_legacy',0):.1f}\n")
    print(f"Leaderboard CSV: {csv_file}")


if __name__ == "__main__":
    main()
