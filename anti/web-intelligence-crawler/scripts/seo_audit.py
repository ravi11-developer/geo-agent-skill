#!/usr/bin/env python3
"""
SEO Audit — Main Entry Point
=============================
Orchestrates: crawl → analyze → output structured JSON report.

Usage:
    python seo_audit.py https://example.com [--max-pages 50] [--max-depth 2] [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from crawler import crawl, DEFAULT_MAX_PAGES, DEFAULT_MAX_DEPTH, DEFAULT_DELAY, DEFAULT_USER_AGENT
from analyzers import PER_PAGE_CHECKS, SITE_WIDE_CHECKS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("seo-audit")

# ---------------------------------------------------------------------------
# Audit orchestration
# ---------------------------------------------------------------------------


def run_audit(
    pages: list[dict[str, Any]],
    site_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run all SEO checks and return a flat list of findings."""

    findings: list[dict[str, Any]] = []

    # --- Per-page checks ---
    for page in pages:
        if page.get("html_raw") is None:
            continue  # skip pages that couldn't be fetched
        for check_fn in PER_PAGE_CHECKS:
            try:
                results = check_fn(page)
                if results:
                    findings.extend(results)
            except Exception as exc:
                log.warning(
                    "Check %s failed on %s: %s",
                    check_fn.__name__, page.get("url", "?"), exc,
                )

    # --- Site-wide checks ---
    for check_fn in SITE_WIDE_CHECKS:
        try:
            results = check_fn(pages, site_data)
            if results:
                findings.extend(results)
        except Exception as exc:
            log.warning("Site-wide check %s failed: %s", check_fn.__name__, exc)

    return findings


def assign_finding_ids(findings: list[dict[str, Any]]) -> None:
    """Assign sequential F-001, F-002, … IDs to each finding (in-place)."""
    for i, finding in enumerate(findings, start=1):
        finding["id"] = f"F-{i:03d}"


def build_severity_summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count findings by severity level."""
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f.get("severity", "low")
        if sev in counts:
            counts[sev] += 1
    counts["total_findings"] = sum(counts.values())
    return counts


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge findings with the same title and category.

    When multiple pages trigger the same check, merge them into a single
    finding with a combined affected_urls list and aggregated evidence.
    """
    merged: dict[str, dict[str, Any]] = {}

    for f in findings:
        key = (f["category"], f["title"])
        if key not in merged:
            merged[key] = {
                "category": f["category"],
                "title": f["title"],
                "severity": f["severity"],
                "affected_urls": list(f.get("affected_urls", [])),
                "evidence": f["evidence"],
                "suggested_action": f["suggested_action"],
            }
        else:
            existing = merged[key]
            # Merge affected URLs
            for url in f.get("affected_urls", []):
                if url not in existing["affected_urls"]:
                    existing["affected_urls"].append(url)
            # Keep highest severity
            severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if severity_rank.get(f["severity"], 0) > severity_rank.get(existing["severity"], 0):
                existing["severity"] = f["severity"]
                existing["suggested_action"] = f["suggested_action"]

    deduped = list(merged.values())

    # Update evidence with merged URL counts
    for f in deduped:
        count = len(f["affected_urls"])
        if count > 1:
            f["evidence"] = f"{f['evidence']} (affects {count} pages)"

    return deduped


def build_report(
    start_url: str,
    pages: list[dict[str, Any]],
    site_data: dict[str, Any],
    findings: list[dict[str, Any]],
    crawl_depth: int,
) -> dict[str, Any]:
    """Build the final JSON report envelope."""

    domain = urlparse(start_url).netloc
    summary = build_severity_summary(findings)

    # Site-wide metadata
    site_info: dict[str, Any] = {
        "has_sitemap": len(site_data.get("sitemap_urls", [])) > 0,
        "sitemap_url_count": len(site_data.get("sitemap_urls", [])),
        "has_robots_txt": site_data.get("robots_txt_content") is not None,
        "uses_https": urlparse(start_url).scheme == "https",
    }

    return {
        "site": domain,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_crawled": len(pages),
        "crawl_depth": crawl_depth,
        "site_info": site_info,
        "summary": summary,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEO Audit Crawler — crawl a website and produce a structured SEO audit report.",
    )
    parser.add_argument("url", help="Starting URL to audit (must be http or https)")
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"Maximum pages to crawl (default: {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
        help=f"Maximum crawl depth (default: {DEFAULT_MAX_DEPTH})",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY,
        help=f"Seconds between requests (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT,
        help="Custom User-Agent string",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write JSON to this file instead of stdout",
    )

    args = parser.parse_args()

    # --- Crawl ---
    try:
        log.info("Starting SEO audit of %s", args.url)
        pages, site_data = crawl(
            args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            delay=args.delay,
            user_agent=args.user_agent,
        )
    except ValueError as exc:
        log.error("Invalid URL: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        log.warning("Audit interrupted by user")
        sys.exit(130)

    # --- Analyze ---
    log.info("Running %d per-page + %d site-wide SEO checks…",
             len(PER_PAGE_CHECKS), len(SITE_WIDE_CHECKS))
    findings = run_audit(pages, site_data)

    # --- Deduplicate & assign IDs ---
    findings = deduplicate_findings(findings)

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 4))

    assign_finding_ids(findings)

    # --- Build report ---
    report = build_report(args.url, pages, site_data, findings, args.max_depth)

    # --- Output ---
    json_output = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(json_output)
        log.info("SEO audit report written to %s", args.output)
        log.info("Summary: %s", json.dumps(report["summary"]))
    else:
        print(json_output)

    log.info("Audit complete — %d findings across %d pages",
             report["summary"]["total_findings"], len(pages))


if __name__ == "__main__":
    main()
