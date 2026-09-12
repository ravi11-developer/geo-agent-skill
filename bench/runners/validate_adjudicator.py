#!/usr/bin/env python3
"""Measure the adjudicator's own accuracy, so real-web numbers can be defended.

The real-web leaderboard is scored by `eval/scoring/adjudicate.py`, which derives
labels mechanically because real sites have no gold. That raises the obvious
question: how accurate is the adjudicator? This script answers it by pushing the
16 *labelled* synthetic sites through the identical pipeline - capture, replay,
adjudicate - and comparing its labels with the hand-written gold.

    python eval/runners/validate_adjudicator.py

Quote the agreement figure it prints alongside any real-web result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from bench.runners.serve_synthetic import start_server
from bench.scoring.adjudicate import adjudicate_site, validate_against_gold

SYNTHETIC_DIR = os.path.join(PROJECT_ROOT, "bench", "sites", "synthetic")
GOLD_DIR = os.path.join(PROJECT_ROOT, "bench", "gold")


def main() -> None:
    workdir = tempfile.mkdtemp(prefix="adjudicator-validation-")
    sites_file = os.path.join(workdir, "sites.json")
    corpus = os.path.join(workdir, "corpus")

    slugs = sorted(d for d in os.listdir(SYNTHETIC_DIR)
                   if os.path.isdir(os.path.join(SYNTHETIC_DIR, d)))
    with open(sites_file, "w", encoding="utf-8") as fh:
        json.dump({"name": "adjudicator validation",
                   "selection_method": "the labelled synthetic sites, replayed through the real-web pipeline",
                   "sites": [{"slug": s, "url": f"http://localhost:9500/{s}/",
                              "category": "synthetic_validation", "why_hard": ""} for s in slugs]}, fh)

    start_server(9500, background=True)
    subprocess.run([sys.executable, os.path.join(PROJECT_ROOT, "bench", "runners", "capture_corpus.py"),
                    "--sites", sites_file, "--out", corpus, "--pages", "4",
                    "--delay", "0.02", "--workers", "4"],
                   check=True, capture_output=True, text=True)

    with open(os.path.join(corpus, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    adjudications = {}
    for site in manifest["sites"]:
        if site["status"] != "ok":
            continue
        adjudications[site["slug"]] = adjudicate_site(os.path.join(corpus, site["slug"]), site)

    report = validate_against_gold(GOLD_DIR, adjudications)
    print(f"\nAdjudicator vs hand-written gold on {len(adjudications)} labelled sites")
    print(f"  agreement : {report['agreement']:.3f}  ({report['agree']} agree, {report['disagree']} disagree)")
    print(f"  abstained : {report['abstained']} category decisions the adjudicator declined to call")
    if report["mistakes"]:
        print("  disagreements:")
        for mistake in report["mistakes"]:
            print(f"    {mistake['site']:<30} {mistake['category']:<20} "
                  f"adjudicator={mistake['adjudicator']:<8} gold={mistake['gold']}")
    print(f"\n(corpus kept at {corpus})")


if __name__ == "__main__":
    main()
