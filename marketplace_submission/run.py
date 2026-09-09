#!/usr/bin/env python3
"""Agent F - The Marketplace.

Benchmark entrypoint.  The suite calls :func:`run_audit`; everything below it is
the marketplace: ``marketplace.json`` declares the installed skills, and the
``audit-orchestrator`` skill loads and composes them at run time.

    from eval.agents.marketplace.run import run_audit
    report = run_audit("https://example.com")
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

MARKETPLACE_ROOT = os.path.dirname(os.path.abspath(__file__))
if MARKETPLACE_ROOT not in sys.path:
    sys.path.insert(0, MARKETPLACE_ROOT)

from lib.loader import load_manifest, load_skills  # noqa: E402


def _orchestrator():
    """Resolve the entrypoint skill declared in the manifest."""
    manifest = load_manifest()
    entry_id = manifest.get("entrypoint", "audit-orchestrator")
    for skill in load_skills(manifest, kinds=("orchestrator",)):
        if skill.id == entry_id:
            return skill
    raise RuntimeError(f"marketplace manifest declares no entrypoint skill {entry_id!r}")


def run_audit(url: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Audit a website for AI discoverability and on-site engagement.

    Read-only: HTTP GET requests only, no cookies persisted, no writes to the
    target. Returns the structured audit report described in ``marketplace.json``.
    """
    return _orchestrator().run({"url": url, "config": config or {}})


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python run.py <url> [output.json]", file=sys.stderr)
        raise SystemExit(2)
    report = run_audit(sys.argv[1])
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"saved {sys.argv[2]}")
    summary = report["summary"]
    print(f"{report['site']}  health={summary['health_score']}  findings={summary['total_findings']}")
    for finding in report["findings"]:
        print(f"  [{finding['severity'].upper():<8}] {finding['category']:<19} {finding['title']}")
    for rec in report["recommendations"]:
        print(f"  (proactive) {rec['title']}")


if __name__ == "__main__":
    main()
