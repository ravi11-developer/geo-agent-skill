#!/usr/bin/env python3
"""geo - drive the two candidate audit marketplaces and the benchmark.

    ./geo list                       what agents exist and how they differ
    ./geo run <agent> <url> [-o f]   run one agent against one site
    ./geo bench [--agent <id>]       score every agent on the 16 gold sites
    ./geo package <agent>            build the submission zip for one agent
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

from bench import registry  # noqa: E402


def cmd_list(_args: argparse.Namespace) -> int:
    agents = registry.discover()
    if not agents:
        print("no agents found under agents/", file=sys.stderr)
        return 1
    for agent in agents.values():
        meta = agent.metadata
        print(f"\n{agent.id}  -  {agent.name}  (v{meta.get('version', '?')})")
        print(f"  strategy    {meta.get('strategy', '?')}")
        print(f"  llm         {meta.get('uses_llm')}   api key required: {meta.get('requires_api_key')}")
        print(f"  optimises   {', '.join(meta.get('optimises_for', []))}")
        print(f"  skills ({len(agent.skills)})  {', '.join(agent.skills)}")
        print(f"  {meta.get('summary', '')}")
        for other, how in (meta.get("differs_from") or {}).items():
            print(f"  vs {other}: {how}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    agent = registry.load(args.agent)
    result = agent.run(args.url, timeout=args.timeout)
    if result["error"]:
        print(f"{agent.id} failed: {result['error']}", file=sys.stderr)
        return 1
    report = result["report"]
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"saved {args.output}")
    summary = report.get("summary", {})
    print(f"\n{report.get('site')}   health={summary.get('health_score')}   "
          f"findings={summary.get('total_findings')}   {result['runtime_seconds']}s")
    for finding in report.get("findings", []):
        print(f"  [{finding['severity'].upper():<8}] {finding['category']:<19} {finding['title']}")
    for rec in report.get("recommendations", []):
        print(f"  (proactive) {rec['title']}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    cmd = [sys.executable, os.path.join(REPO_ROOT, "bench", "runners", "run_suite.py")]
    if args.agent:
        cmd += ["--agent", args.agent]
    return subprocess.call(cmd, cwd=REPO_ROOT)


def cmd_package(args: argparse.Namespace) -> int:
    agent = registry.load(args.agent)
    out = args.output or os.path.join(REPO_ROOT, f"{agent.id}-submission.zip")
    skip_dirs = {"__pycache__", ".git", "venv", ".venv", "node_modules", ".llm-cache"}
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(agent.root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for name in files:
                if name.endswith((".pyc", ".pyo")) or name == "agent.json":
                    continue          # agent.json is bench metadata, not part of the marketplace
                path = os.path.join(root, name)
                zf.write(path, os.path.relpath(path, agent.root))
                total += os.path.getsize(path)
    size_mb = os.path.getsize(out) / (1024 * 1024)
    print(f"{out}  ({size_mb:.2f} MB zipped, {total / (1024*1024):.2f} MB raw)")
    if size_mb > 50:
        print("WARNING: exceeds the 50 MB submission limit", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="geo", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every agent and how they differ").set_defaults(fn=cmd_list)

    p_run = sub.add_parser("run", help="run one agent against one URL")
    p_run.add_argument("agent")
    p_run.add_argument("url")
    p_run.add_argument("-o", "--output")
    p_run.add_argument("--timeout", type=int, default=300)
    p_run.set_defaults(fn=cmd_run)

    p_bench = sub.add_parser("bench", help="score agents on the gold sites")
    p_bench.add_argument("--agent")
    p_bench.set_defaults(fn=cmd_bench)

    p_pkg = sub.add_parser("package", help="build an agent's submission zip")
    p_pkg.add_argument("agent")
    p_pkg.add_argument("-o", "--output")
    p_pkg.set_defaults(fn=cmd_package)

    args = parser.parse_args()
    try:
        return args.fn(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
