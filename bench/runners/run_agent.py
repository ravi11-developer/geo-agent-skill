#!/usr/bin/env python3
"""Run a single agent on a given URL and output/save results.

Usage:
    python bench/runners/run_agent.py --agent B-coverage --url http://localhost:9500/site-001-healthy/
    python bench/runners/run_agent.py --agent A-precision --url https://example.com --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from bench import registry
from bench.scoring.normalize import normalize_report
from bench.scoring.evidence import score_schema_validity


def run_agent(agent_name: str, url: str, timeout: int = 300) -> dict[str, Any]:
    """Execute an agent by name on a target URL."""
    agent = registry.load(agent_name)   # raises ValueError naming the known agents
    timestamp = datetime.now(timezone.utc).isoformat()

    run = agent.run(url, timeout=timeout)
    report = run["report"]
    elapsed = run["runtime_seconds"]
    error = run["error"]
    exit_code = 1 if error else 0

    result: dict[str, Any] = {
        "agent": agent_name,
        "url": url,
        "timestamp": timestamp,
        "runtime_seconds": round(elapsed, 3),
        "exit_code": exit_code,
        "error": error,
        "raw_report": report,
    }

    if report:
        report["_agent"] = agent_name
        normalized = normalize_report(report)
        result["normalized_report"] = normalized
        result["schema_validity_score"] = score_schema_validity(report)

    return result


def main():
    parser = argparse.ArgumentParser(description="Run a single agent on a URL")
    parser.add_argument("--agent", required=True,
                        help="Agent id as discovered under agents/ (see: ./geo list)")
    parser.add_argument("--url", required=True, help="Target URL to audit")
    parser.add_argument("--output", "-o", help="Filepath to write JSON report")
    parser.add_argument("--timeout", type=int, default=300, help="Execution timeout in seconds")
    args = parser.parse_args()

    print(f"Running agent '{args.agent}' against '{args.url}'...")
    res = run_agent(args.agent, args.url, timeout=args.timeout)

    print(f"Completed in {res['runtime_seconds']}s (exit code {res['exit_code']})")
    if res.get("error"):
        print(f"Error: {res['error']}", file=sys.stderr)
        sys.exit(1)

    norm = res.get("normalized_report", {})
    findings = norm.get("findings", [])
    print(f"Total findings: {len(findings)} (Schema validity: {res.get('schema_validity_score', 0):.2f})")
    for f in findings[:5]:
        print(f"  [{f.get('severity', '?').upper()}] {f.get('category')}: {f.get('title')}")
    if len(findings) > 5:
        print(f"  ... and {len(findings) - 5} more")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"Saved result to {args.output}")
    else:
        # If no output specified, print raw JSON report to stdout
        pass


if __name__ == "__main__":
    main()
