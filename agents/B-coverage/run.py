#!/usr/bin/env python3
"""Marketplace entrypoint.

Everything below this module is the marketplace: ``marketplace.json`` declares
the installed skills, and the ``audit-orchestrator`` skill loads and composes
them at run time.

    python run.py https://example.com [report.json]

or from Python:

    from run import run_audit
    report = run_audit("https://example.com")
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

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


def default_output_path(url: str) -> str:
    """``https://example.com`` -> ``audit_example.json``.

    The host is the report's identity, so the default filename is derived from
    it: ``www.`` and the public suffix are dropped, remaining labels joined with
    ``_``, and anything outside ``[A-Za-z0-9_-]`` replaced so the name is safe
    on every filesystem.
    """
    host = urlparse(url if "://" in url else f"http://{url}").hostname or ""
    host = host[4:] if host.startswith("www.") else host
    labels = [label for label in host.split(".") if label]
    if len(labels) > 1:
        labels = labels[:-1]          # drop the TLD: example.com -> example
    name = re.sub(r"[^A-Za-z0-9_-]", "-", "_".join(labels)) or "site"
    return f"audit_{name}.json"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python run.py <url> [output.json]   "
              "(default: audit_<host>.json)", file=sys.stderr)
        raise SystemExit(2)
    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else default_output_path(url)
    report = run_audit(url)
    with open(output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"saved {output}")
    summary = report["summary"]
    print(f"{report['site']}  health={summary['health_score']}  findings={summary['total_findings']}")
    for finding in report["findings"]:
        print(f"  [{finding['severity'].upper():<8}] {finding['category']:<19} {finding['title']}")
    for rec in report["recommendations"]:
        print(f"  (proactive) {rec['title']}")


if __name__ == "__main__":
    main()
