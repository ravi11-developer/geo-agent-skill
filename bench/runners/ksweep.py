#!/usr/bin/env python3
"""Crawl-budget sweep: how many findings does page N buy?

Answers "why that page limit?" with a measurement instead of a round number.
Runs one agent over a list of live sites at several page budgets and records
findings, category coverage, template coverage and runtime at each.

    python bench/runners/ksweep.py --agent B-coverage --k 8,12,16,20,30,45

Saturation is disabled during the sweep (soft target pinned to the hard limit)
so the curve measures the effect of the page budget alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")

DEFAULT_SITES = [
    "https://www.mokobara.com",
    "https://www.iiit.ac.in",
    "https://www.smilefoundationindia.org",
    "https://www.tldraw.com",
    "https://beardo.in",
    "https://www.bikanervala.com",
    "https://www.assocham.org",
    "https://www.bharatforge.com",
]


def run_once(agent: str, url: str, k: int, timeout: int) -> dict:
    env = dict(os.environ)
    env.update({
        "AUDIT_CRAWL_PROFILE": "extended",
        "AUDIT_HARD_PAGE_LIMIT": str(k),
        "AUDIT_SOFT_PAGE_TARGET": str(k),   # pin: no early saturation stop
        "LLM_ENABLED": "false",
    })
    out = os.path.join(RESULTS_DIR, f"_ksweep_tmp_{k}.json")
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "agents", agent, "run.py"), url, out],
        capture_output=True, text=True, env=env, cwd=PROJECT_ROOT, timeout=timeout)
    elapsed = round(time.monotonic() - started, 2)
    if proc.returncode != 0 or not os.path.exists(out):
        return {"error": (proc.stderr or "no report")[-200:], "runtime": elapsed}
    with open(out, encoding="utf-8") as fh:
        report = json.load(fh)
    os.remove(out)
    checks = {c.get("check"): c for c in report.get("checks_performed", [])}
    pages = checks.get("pages_crawled", {}).get("value", 0)
    return {
        "runtime": elapsed,
        "pages": pages,
        "findings": len(report.get("findings", [])),
        "categories": sorted({f["category"] for f in report.get("findings", [])}),
        "recommendations": len(report.get("recommendations", [])),
        "health": report.get("summary", {}).get("health_score"),
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="B-coverage")
    parser.add_argument("--k", default="8,12,16,20,30,45")
    parser.add_argument("--sites", default=None, help="file with one URL per line")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    ks = [int(x) for x in args.k.split(",")]
    sites = DEFAULT_SITES
    if args.sites:
        with open(args.sites, encoding="utf-8") as fh:
            sites = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for url in sites:
        for k in ks:
            try:
                res = run_once(args.agent, url, k, args.timeout)
            except subprocess.TimeoutExpired:
                res = {"error": "timeout", "runtime": args.timeout}
            row = {"site": url, "k": k, **res}
            row["categories"] = "|".join(res.get("categories") or [])
            rows.append(row)
            print(f"{url:<42} k={k:<3} pages={res.get('pages','-'):<3} "
                  f"findings={res.get('findings','-'):<2} t={res.get('runtime')}s "
                  f"{res.get('error') or ''}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"ksweep_{args.agent}_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["site", "k", "pages", "findings",
                                                "categories", "recommendations",
                                                "health", "runtime", "error"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
