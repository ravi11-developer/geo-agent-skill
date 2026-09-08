#!/usr/bin/env python3
"""Baseline agent wrapper — runs the existing anti/web-intelligence-crawler/scripts/seo_audit.py
through the common benchmark interface.

This wrapper imports the existing implementation and adapts its output to the
standard report schema.
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# Add the existing implementation to the path
_ANTI_SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "Ravi", "anti", "web-intelligence-crawler", "scripts")
)
if _ANTI_SCRIPTS not in sys.path:
    sys.path.insert(0, _ANTI_SCRIPTS)

# Monkey-patch the crawler's IP validation to allow localhost for synthetic sites
import crawler as _crawler

_original_is_private = _crawler._is_private_ip

def _patched_is_private(hostname: str) -> bool:
    """Allow localhost for benchmark synthetic sites."""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return False
    return _original_is_private(hostname)

_crawler._is_private_ip = _patched_is_private

_original_validate = _crawler.validate_url

def _patched_validate(raw: str) -> str:
    """Allow localhost URLs for benchmark."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise ValueError("URL has no hostname")
    return raw

_crawler.validate_url = _patched_validate

_original_extract_links = _crawler.extract_links

def _patched_extract_links(html: str, page_url: str, base_domain: str):
    internal, external = _original_extract_links(html, page_url, base_domain)
    parsed_page = urlparse(page_url)
    path_parts = [p for p in parsed_page.path.strip("/").split("/") if p]
    if path_parts:
        site_root = "/" + path_parts[0] + "/"
        scoped_internal = []
        for link in internal:
            lp = urlparse(link).path
            if lp == site_root.rstrip("/") or lp.startswith(site_root):
                scoped_internal.append(link)
            else:
                external.append(link)
        return scoped_internal, external
    return internal, external

_crawler.extract_links = _patched_extract_links

from crawler import crawl
from analyzers import PER_PAGE_CHECKS, SITE_WIDE_CHECKS


def run_audit(url: str) -> dict[str, Any]:
    """Run the baseline SEO audit on the given URL."""
    # Crawl
    pages, site_data = crawl(
        url,
        max_pages=20,
        max_depth=1,
        delay=0.1,  # Fast for benchmarking
    )

    # Analyze
    findings: list[dict[str, Any]] = []
    for page in pages:
        if page.get("html_raw") is None:
            continue
        for check_fn in PER_PAGE_CHECKS:
            try:
                results = check_fn(page)
                if results:
                    findings.extend(results)
            except Exception:
                pass

    for check_fn in SITE_WIDE_CHECKS:
        try:
            results = check_fn(pages, site_data)
            if results:
                findings.extend(results)
        except Exception:
            pass

    # Deduplicate
    merged: dict[tuple, dict] = {}
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
            for u in f.get("affected_urls", []):
                if u not in existing["affected_urls"]:
                    existing["affected_urls"].append(u)
            severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if severity_rank.get(f["severity"], 0) > severity_rank.get(existing["severity"], 0):
                existing["severity"] = f["severity"]

    deduped = list(merged.values())

    # Sort and assign IDs
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    deduped.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 4))
    for i, f in enumerate(deduped, 1):
        f["id"] = f"F-{i:03d}"

    # Build summary
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in deduped:
        sev = f.get("severity", "low")
        if sev in counts:
            counts[sev] += 1
    counts["total_findings"] = sum(counts.values())

    domain = urlparse(url).netloc
    return {
        "site": domain,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": counts,
        "findings": deduped,
    }
