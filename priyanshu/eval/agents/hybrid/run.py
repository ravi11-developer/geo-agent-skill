#!/usr/bin/env python3
"""Agent E: Hybrid Auditor

Combines the high-precision deterministic rules with the
high-recall semantic reasoning model.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Ensure eval is in path so we can import from other agents
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from eval.agents.deterministic.run import (
    crawl_site,
    check_crawlability,
    check_structured_data,
    check_rendering_gaps,
    check_freshness,
)
from eval.agents.reasoning.run import (
    extract_brand_model,
    reason_about_discoverability,
)

def run_audit(url: str, site_prefix: str = None) -> dict[str, Any]:
    """Run the hybrid audit."""
    # 1. Crawl (using the robust deterministic crawler)
    pages, site_data = crawl_site(url, max_pages=15)
    
    # 2. Run high-precision deterministic checks
    findings = []
    findings.extend(check_crawlability(url, pages, site_data))
    findings.extend(check_structured_data(pages))
    findings.extend(check_rendering_gaps(pages))
    findings.extend(check_freshness(pages))
    
    # 3. Build semantic model and run reasoning checks
    model = extract_brand_model(pages)
    reasoning_findings = reason_about_discoverability(model)
    
    # 4. Filter reasoning findings to only those where it excels
    # We want: entity_identity, non_text_facts, engagement
    # We discard: rendering, freshness, structured_data (since deterministic handles them better)
    allowed_reasoning_categories = {"entity_identity", "non_text_facts", "engagement"}
    for f in reasoning_findings:
        if f.get("_category") in allowed_reasoning_categories:
            findings.append(f)
            
    # 5. Finalize
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 4))
    for i, f in enumerate(findings, 1):
        f["id"] = f"F-{i:03d}"

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f.get("severity", "low")
        if sev in counts:
            counts[sev] += 1
    counts["total_findings"] = sum(counts.values())

    domain = urlparse(url).netloc
    return {
        "site": domain,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": counts,
        "findings": findings,
    }

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(json.dumps(run_audit(sys.argv[1]), indent=2))
