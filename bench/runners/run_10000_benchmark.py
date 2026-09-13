#!/usr/bin/env python3
"""Convert combined-websites-10000.md (one URL per line) into the sites JSON format
required by capture_corpus.py, then kick off capture + benchmark.

Usage:
    python bench/runners/run_10000_benchmark.py --convert           # Step 1: build sites_10000.json
    python bench/runners/run_10000_benchmark.py --capture           # Step 2: capture the corpus
    python bench/runners/run_10000_benchmark.py --benchmark         # Step 3: run all 6 agents
    python bench/runners/run_10000_benchmark.py --all               # All three steps
    python bench/runners/run_10000_benchmark.py --limit 100 --all   # Quick trial run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# The source file lives in a folder with a space in the name — build the path explicitly
MD_FILE     = os.path.join(PROJECT_ROOT, "testing site", "combined-websites-10000.md")
SITES_JSON  = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "sites_10000.json")
CORPUS_DIR  = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "corpus_10000")


def slug_for(url: str) -> str:
    """Turn a URL into a safe directory name."""
    p = urlparse(url)
    host = re.sub(r"^www\.", "", p.netloc or "unknown")
    host = re.sub(r"[^\w\-]", "-", host).strip("-")
    return host[:60] or "unknown"


def convert(limit: int = 0) -> None:
    print(f"Reading URLs from: {MD_FILE}")
    with open(MD_FILE, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh if l.strip() and l.strip().startswith("http")]

    # Deduplicate preserving order
    seen: set[str] = set()
    urls: list[str] = []
    for u in lines:
        if u not in seen:
            seen.add(u)
            urls.append(u)

    if limit:
        urls = urls[:limit]

    print(f"Unique URLs: {len(urls)}")

    # Build slug-unique list
    slug_count: dict[str, int] = {}
    sites = []
    for url in urls:
        base_slug = slug_for(url)
        slug_count[base_slug] = slug_count.get(base_slug, 0) + 1
        suffix = f"-{slug_count[base_slug]}" if slug_count[base_slug] > 1 else ""
        sites.append({"url": url, "slug": base_slug + suffix, "category": "combined"})

    payload = {"sites": sites, "meta": {"source": "combined-websites-10000.md", "count": len(sites)}}
    os.makedirs(os.path.dirname(SITES_JSON), exist_ok=True)
    with open(SITES_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Written {len(sites)} sites -> {SITES_JSON}")


def run_capture(workers: int = 128, limit: int = 0) -> None:
    cmd = [
        sys.executable, "bench/runners/capture_corpus.py",
        "--urls", MD_FILE,       # plain-text URL file — capture_corpus reads it directly
        "--out", CORPUS_DIR,
        "--workers", str(workers),
        "--pages", "2",          # 2 pages per site: 33% fewer HTTP requests vs 3
        "--delay", "0.5",        # 0.5s per-host delay (down from 1.0s default)
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    print(f"\n[CAPTURE]  ({workers} workers): {' '.join(cmd)}\n")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def run_benchmark(workers: int = 16, limit: int = 0, agents: list[str] | None = None) -> None:
    cmd = [
        sys.executable, "bench/runners/run_real_suite.py",
        "--corpus", CORPUS_DIR,
        "--workers", str(workers),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    for agent in (agents or []):
        cmd += ["--agent", agent]
    print(f"\n[BENCHMARK] ({workers} workers): {' '.join(cmd)}\n")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="10,000-website benchmark pipeline")
    ap.add_argument("--convert",   action="store_true", help="Step 1: convert MD -> sites JSON")
    ap.add_argument("--capture",   action="store_true", help="Step 2: capture corpus from the web")
    ap.add_argument("--benchmark", action="store_true", help="Step 3: run all agents")
    ap.add_argument("--all",       action="store_true", help="Run all three steps")
    ap.add_argument("--limit",     type=int, default=0,  help="Cap URLs (useful for quick trials)")
    ap.add_argument("--capture-workers",   type=int, default=128, help="Parallel workers for capture (network I/O bound)")
    ap.add_argument("--benchmark-workers", type=int, default=16,  help="Parallel workers for benchmark (CPU bound, match core count)")
    ap.add_argument("--agent",     action="append",      help="Restrict benchmark to these agents (repeatable)")
    args = ap.parse_args()

    if not any([args.convert, args.capture, args.benchmark, args.all]):
        ap.print_help()
        raise SystemExit(0)

    if args.convert or args.all:
        convert(limit=args.limit)

    if args.capture or args.all:
        run_capture(workers=args.capture_workers, limit=args.limit)

    if args.benchmark or args.all:
        run_benchmark(workers=args.benchmark_workers, limit=args.limit, agents=args.agent)


if __name__ == "__main__":
    main()
