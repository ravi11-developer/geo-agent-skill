#!/usr/bin/env python3
"""Saturation-parameter sweep: how patient should the early-stop be?

Unlike ksweep.py (which pins soft_page_target == hard_page_limit to isolate the
raw page budget), this leaves saturation live and varies the parameters that
govern it, to see how many pages real sites reach before the early-stop gives
up - and whether loosening those parameters lets them reach more of a raised
hard_page_limit before saturating.

    python bench/runners/saturation_sweep.py --agent B-coverage \
        --hard-limit 80 \
        --combos "16/3/6,40/3/6,40/5/10,60/5/10"

Each combo is "soft_page_target/per_template_samples/saturation_window".
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


def run_once(agent: str, url: str, hard_limit: int, soft: int, per_template: int,
             window: int, timeout: int) -> dict:
    env = dict(os.environ)
    env.update({
        "AUDIT_CRAWL_PROFILE": "extended",
        "AUDIT_HARD_PAGE_LIMIT": str(hard_limit),
        "AUDIT_SOFT_PAGE_TARGET": str(soft),
        "AUDIT_PER_TEMPLATE_SAMPLES": str(per_template),
        "AUDIT_SATURATION_WINDOW": str(window),
        "LLM_ENABLED": "false",
    })
    out = os.path.join(RESULTS_DIR, f"_satsweep_tmp_{soft}_{per_template}_{window}.json")
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
    telemetry = report.get("telemetry", {})
    return {
        "runtime": elapsed,
        "pages": telemetry.get("pages_fetched", 0),
        "templates_sampled": telemetry.get("templates_sampled", 0),
        "stopped_because": telemetry.get("crawl_stopped_because"),
        "findings": len(report.get("findings", [])),
        "categories": sorted({f["category"] for f in report.get("findings", [])}),
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="B-coverage")
    parser.add_argument("--hard-limit", type=int, default=80,
                        help="hard_page_limit backstop shared by every combo")
    parser.add_argument("--combos", default="16/3/6,40/3/6,40/5/10,60/5/10",
                        help="comma-separated soft/per_template/window triples")
    parser.add_argument("--sites", default=None, help="file with one URL per line")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    combos = []
    for combo in args.combos.split(","):
        soft, per_template, window = (int(x) for x in combo.split("/"))
        combos.append((soft, per_template, window))

    sites = DEFAULT_SITES
    if args.sites:
        with open(args.sites, encoding="utf-8") as fh:
            sites = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for url in sites:
        for soft, per_template, window in combos:
            try:
                res = run_once(args.agent, url, args.hard_limit, soft, per_template,
                               window, args.timeout)
            except subprocess.TimeoutExpired:
                res = {"error": "timeout", "runtime": args.timeout}
            row = {"site": url, "hard_limit": args.hard_limit, "soft": soft,
                   "per_template": per_template, "window": window, **res}
            row["categories"] = "|".join(res.get("categories") or [])
            rows.append(row)
            print(f"{url:<42} soft={soft:<4} pt={per_template:<3} win={window:<3} "
                  f"pages={res.get('pages','-'):<4} templates={res.get('templates_sampled','-'):<3} "
                  f"stop={res.get('stopped_because')} t={res.get('runtime')}s "
                  f"{res.get('error') or ''}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"satsweep_{args.agent}_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["site", "hard_limit", "soft", "per_template",
                                                "window", "pages", "templates_sampled",
                                                "stopped_because", "findings", "categories",
                                                "runtime", "error"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
