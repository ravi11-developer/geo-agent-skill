#!/usr/bin/env python3
"""Automated 2,600-Website Benchmark Pipeline.

Usage:
    python eval/runners/run_2600_benchmark.py --capture          # Run Phase 1: Capture 2600 sites
    python eval/runners/run_2600_benchmark.py --benchmark        # Run Phase 2: Benchmark all 6 agents
    python eval/runners/run_2600_benchmark.py --all              # Run both Phase 1 and Phase 2
    python eval/runners/run_2600_benchmark.py --limit 50 --all   # Quick 50-site trial
"""

import os
import sys
import argparse
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

SITES_JSON = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "sites_2600.json")
CORPUS_DIR = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "corpus_2600")


def run_capture(limit: int = 0, pages: int = 2, workers: int = 20):
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "bench", "runners", "capture_corpus.py"),
        "--sites", SITES_JSON,
        "--out", CORPUS_DIR,
        "--pages", str(pages),
        "--workers", str(workers),
    ]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    print("\n" + "=" * 80)
    print("PHASE 1: CAPTURING 2,600-WEBSITE CORPUS")
    print("=" * 80)
    print(f"Command: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)


def run_benchmark(limit: int = 0, workers: int = 32, agents: list[str] = None):
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "bench", "runners", "run_real_suite.py"),
        "--corpus", CORPUS_DIR,
        "--workers", str(workers),
    ]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    if agents:
        for a in agents:
            cmd.extend(["--agent", a])
    print("\n" + "=" * 80)
    print("PHASE 2: BENCHMARKING AGENTS ON CORPUS")
    print("=" * 80)
    print(f"Command: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="2,600 Website Benchmark Pipeline")
    parser.add_argument("--capture", action="store_true", help="Run capture step")
    parser.add_argument("--benchmark", action="store_true", help="Run benchmark step")
    parser.add_argument("--all", action="store_true", help="Run both capture and benchmark")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of sites")
    parser.add_argument("--pages", type=int, default=2, help="Pages per site (default: 2)")
    parser.add_argument("--workers", type=int, default=32, help="Parallel workers (default: 32)")
    parser.add_argument("--agent", action="append", help="Specific agents to test (default: all)")
    args = parser.parse_args()

    if not (args.capture or args.benchmark or args.all):
        parser.print_help()
        print("\nTip: Run with --all to execute the full capture + benchmark flow:")
        print("     python eval/runners/run_2600_benchmark.py --all")
        return

    if args.capture or args.all:
        run_capture(limit=args.limit, pages=args.pages, workers=args.workers)

    if args.benchmark or args.all:
        run_benchmark(limit=args.limit, workers=args.workers, agents=args.agent)


if __name__ == "__main__":
    main()

