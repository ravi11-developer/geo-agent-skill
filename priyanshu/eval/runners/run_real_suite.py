#!/usr/bin/env python3
"""Benchmark every agent against the captured real-web corpus.

Real sites have no gold labels, so this runner scores three things that do not
need them:

1. **Adjudicated detection** - ``eval/scoring/adjudicate.py`` re-derives each
   category from the stored bytes and may answer yes / no / abstain. Contradicting
   a ``no`` is a false positive, missing a ``yes`` is a false negative, and
   findings that land on an ``abstain`` are reported as *unverified* rather than
   silently counted either way.
2. **Report quality** - evidence and suggested-action scores over every finding
   (not only matched ones), plus output-schema validity.
3. **Operational behaviour** - crash rate, runtime, pages fetched. On the real web
   this separates agents far more sharply than any synthetic site does.

Each site is served from its own local replay server rooted at that site, so
root-relative links, robots.txt and stored Content-Type headers behave exactly as
captured, and every agent replays byte-identical input.

    python eval/runners/capture_corpus.py            # once
    python eval/runners/run_real_suite.py            # all agents, all sites
    python eval/runners/run_real_suite.py --agent marketplace --limit 10
"""

from __future__ import annotations

import argparse
import csv
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from eval.scoring.normalize import normalize_finding
from eval.scoring.evidence import score_evidence, score_action, score_schema_validity
from eval.scoring.adjudicate import CATEGORIES, adjudicate_site, score_report, validate_against_gold

CORPUS_DIR = os.path.join(PROJECT_ROOT, "eval", "sites", "real", "corpus")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "eval", "results")
GOLD_DIR = os.path.join(PROJECT_ROOT, "eval", "gold")
AGENTS = ["baseline", "deterministic", "reasoning", "specialist", "hybrid", "marketplace"]
PORT = 9600


# ---------------------------------------------------------------------------
# Replay server: one site at a time, served at the server root
# ---------------------------------------------------------------------------

def make_handler(site_dir: str, content_types: dict[str, str]):
    class ReplayHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=site_dir, **kwargs)

        def log_message(self, *args):  # silent
            pass

        def guess_type(self, path):
            rel = os.path.relpath(path, site_dir).replace(os.sep, "/")
            stored = content_types.get(rel)
            if stored:
                return stored
            return super().guess_type(path)

    return ReplayHandler


class QuietServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(site_dir: str, content_types: dict[str, str], port: int) -> QuietServer:
    server = QuietServer(("127.0.0.1", port), make_handler(site_dir, content_types))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# Running one agent on one served site
# ---------------------------------------------------------------------------

def run_agent_on(agent: str, url: str) -> dict[str, Any]:
    result = {"agent": agent, "report": None, "runtime_seconds": 0.0, "error": None}
    try:
        module = __import__(f"eval.agents.{agent}.run", fromlist=["run_audit"])
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"import failed: {type(exc).__name__}: {exc}"
        return result
    start = time.monotonic()
    try:
        result["report"] = module.run_audit(url)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["runtime_seconds"] = round(time.monotonic() - start, 3)
    return result


def flagged_categories(report: dict[str, Any]) -> set[str]:
    flagged = set()
    for finding in report.get("findings", []) or []:
        category = normalize_finding(finding).get("category")
        if category in CATEGORIES:
            flagged.add(category)
    return flagged


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Run all agents against the captured real-web corpus")
    ap.add_argument("--corpus", default=CORPUS_DIR)
    ap.add_argument("--agent", action="append", help="restrict to these agents (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of sites")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N sites (for chunked runs)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--out", default=None, help="results JSON path")
    args = ap.parse_args()

    manifest_path = os.path.join(args.corpus, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"No corpus at {args.corpus}. Run capture_corpus.py first.", file=sys.stderr)
        raise SystemExit(2)
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    sites = [s for s in manifest["sites"] if s["status"] == "ok"]
    if args.offset:
        sites = sites[args.offset:]
    if args.limit:
        sites = sites[:args.limit]
    agents = args.agent or AGENTS

    print(f"Corpus: {len(sites)} sites, {sum(len(s['pages']) for s in sites)} pages "
          f"(captured {manifest.get('captured_at', '?')})")
    print(f"Agents: {', '.join(agents)}\n")

    per_agent: dict[str, list[dict]] = {agent: [] for agent in agents}
    adjudications: dict[str, dict] = {}
    finding_rows: list[dict] = []

    for index, site in enumerate(sites, start=1):
        slug = site["slug"]
        site_dir = os.path.join(args.corpus, slug)
        content_types = {p["file"]: p.get("content_type", "") for p in site["pages"]}
        adjudication = adjudicate_site(site_dir, site)
        adjudications[slug] = adjudication
        labels = adjudication["labels"]

        entry = next((p for p in site["pages"] if p.get("is_entry")), site["pages"][0])
        server = serve(site_dir, content_types, args.port)
        url = f"http://127.0.0.1:{args.port}{entry.get('path', '/')}"
        try:
            line = f"  [{index:>3}/{len(sites)}] {slug:<26}"
            for agent in agents:
                run = run_agent_on(agent, url)
                report = run["report"]
                flagged = flagged_categories(report) if report else set()
                scored = score_report(flagged, labels)
                findings = (report or {}).get("findings", []) or []
                normalized = [normalize_finding(f) for f in findings]
                row = {
                    "site": slug, "agent": agent, "url": site["url"],
                    "error": run["error"], "runtime_seconds": run["runtime_seconds"],
                    "flagged": sorted(flagged), "detection": scored,
                    "findings": len(findings),
                    "evidence": [score_evidence(f) for f in normalized],
                    "actions": [score_action(f) for f in normalized],
                    "schema": score_schema_validity(report) if report else 0.0,
                    "recommendations": len((report or {}).get("recommendations", []) or []),
                }
                per_agent[agent].append(row)
                for finding, norm in zip(findings, normalized):
                    finding_rows.append({
                        "site": slug, "site_url": site["url"], "agent": agent,
                        "category": norm.get("category"), "severity": norm.get("severity"),
                        "adjudicator": labels.get(norm.get("category"), "abstain"),
                        "title": (finding.get("title") or "")[:120],
                        "evidence": (norm.get("evidence") or "")[:300],
                        "human_verdict": "",
                    })
                line += f" {agent[:4]}:{len(findings)}{'!' if run['error'] else ''}"
            print(line)
        finally:
            server.shutdown()
            server.server_close()

    # ---------------- aggregate ----------------
    leaderboard: dict[str, dict] = {}
    for agent, rows in per_agent.items():
        ok_rows = [r for r in rows if not r["error"]]
        tp = sum(r["detection"]["tp"] for r in ok_rows)
        fp = sum(r["detection"]["fp"] for r in ok_rows)
        fn = sum(r["detection"]["fn"] for r in ok_rows)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        evidence = [s for r in ok_rows for s in r["evidence"]]
        actions = [s for r in ok_rows for s in r["actions"]]
        unverified = sum(len(r["detection"]["unverified"]) for r in ok_rows)
        total_findings = sum(r["findings"] for r in ok_rows)
        leaderboard[agent] = {
            "sites": len(rows),
            "crashes": len(rows) - len(ok_rows),
            "crash_rate": round((len(rows) - len(ok_rows)) / len(rows), 4) if rows else 0,
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "unverified_findings": unverified,
            "unverified_rate": round(unverified / total_findings, 4) if total_findings else 0.0,
            "findings_total": total_findings,
            "findings_per_site": round(total_findings / len(ok_rows), 2) if ok_rows else 0,
            "evidence_score": round(sum(evidence) / len(evidence) / 4, 4) if evidence else 0.0,
            "action_score": round(sum(actions) / len(actions) / 4, 4) if actions else 0.0,
            "schema_score": round(sum(r["schema"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else 0.0,
            "recommendations_per_site": round(sum(r["recommendations"] for r in ok_rows) / len(ok_rows), 2) if ok_rows else 0,
            "avg_runtime_seconds": round(sum(r["runtime_seconds"] for r in rows) / len(rows), 2) if rows else 0,
        }

    # If the corpus slugs match labelled synthetic sites, report how accurate the
    # adjudicator itself is. On a real-web corpus this is empty, and the honest
    # figure to quote is the one from the labelled validation run.
    validation = validate_against_gold(GOLD_DIR, adjudications)

    print("\n" + "=" * 118)
    print("REAL-WEB LEADERBOARD (adjudicated, gold-free)")
    print("=" * 118)
    print(f"{'Agent':<16}{'F1':>7}{'Prec':>7}{'Rec':>7}{'TP':>5}{'FP':>5}{'FN':>5}"
          f"{'Unver':>7}{'Find/site':>10}{'Evid':>7}{'Act':>7}{'Schema':>8}{'Recs':>6}{'Crash':>7}{'Runtime':>9}")
    print("-" * 118)
    for agent, s in sorted(leaderboard.items(), key=lambda kv: kv[1]["f1"], reverse=True):
        print(f"{agent:<16}{s['f1']:>7.3f}{s['precision']:>7.3f}{s['recall']:>7.3f}"
              f"{s['tp']:>5}{s['fp']:>5}{s['fn']:>5}{s['unverified_rate']:>7.2f}"
              f"{s['findings_per_site']:>10.2f}{s['evidence_score']:>7.3f}{s['action_score']:>7.3f}"
              f"{s['schema_score']:>8.3f}{s['recommendations_per_site']:>6.1f}"
              f"{s['crash_rate']:>7.2f}{s['avg_runtime_seconds']:>8.2f}s")
    print("=" * 118)

    if validation.get("agreement") is not None:
        print(f"\nAdjudicator self-validation on labelled sites: "
              f"{validation['agreement']:.3f} agreement "
              f"({validation['agree']} agree, {validation['disagree']} disagree, "
              f"{validation['abstained']} abstained)")
        for mistake in validation["mistakes"][:8]:
            print(f"    {mistake['site']:<30} {mistake['category']:<20} "
                  f"adjudicator={mistake['adjudicator']} gold={mistake['gold']}")

    label_counts = {c: {"yes": 0, "no": 0, "abstain": 0} for c in CATEGORIES}
    for adjudication in adjudications.values():
        for category, label in adjudication["labels"].items():
            label_counts[category][label] += 1
    print("\nAdjudicated corpus profile (how the real web actually looks):")
    for category, counts in label_counts.items():
        print(f"  {category:<20} defect on {counts['yes']:>3} sites | clean {counts['no']:>3} | ambiguous {counts['abstain']:>3}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or os.path.join(RESULTS_DIR, f"realweb_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "corpus": {"dir": args.corpus, "sites": len(sites),
                       "captured_at": manifest.get("captured_at"),
                       "selection_method": manifest.get("meta", {}).get("selection_method", "")},
            "agents": agents,
            "leaderboard": leaderboard,
            "adjudications": adjudications,
            "per_site": per_agent,
            "label_counts": label_counts,
            "adjudicator_validation": validation,
        }, fh, indent=2, default=str)

    csv_path = os.path.join(RESULTS_DIR, f"realweb_leaderboard_{stamp}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["agent", "f1", "precision", "recall", "tp", "fp", "fn", "unverified_rate",
                         "findings_per_site", "evidence", "actions", "schema", "recs_per_site",
                         "crash_rate", "avg_runtime_s"])
        for agent, s in sorted(leaderboard.items(), key=lambda kv: kv[1]["f1"], reverse=True):
            writer.writerow([agent, s["f1"], s["precision"], s["recall"], s["tp"], s["fp"], s["fn"],
                             s["unverified_rate"], s["findings_per_site"], s["evidence_score"],
                             s["action_score"], s["schema_score"], s["recommendations_per_site"],
                             s["crash_rate"], s["avg_runtime_seconds"]])

    review_path = os.path.join(RESULTS_DIR, f"realweb_review_sample_{stamp}.csv")
    sample = [r for r in finding_rows if r["adjudicator"] in ("no", "abstain")][:300]
    with open(review_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(finding_rows[0].keys()) if finding_rows else
                                ["site", "site_url", "agent", "category", "severity", "adjudicator",
                                 "title", "evidence", "human_verdict"])
        writer.writeheader()
        writer.writerows(sample)

    print(f"\nResults:        {out_path}")
    print(f"Leaderboard:    {csv_path}")
    print(f"Review sample:  {review_path}  <- hand-label 'human_verdict' to validate the adjudicator")


if __name__ == "__main__":
    main()
