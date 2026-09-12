#!/usr/bin/env python3
"""Wrapper to run Vishal's crawler in the Priyanshu evaluation suite."""

import json
import subprocess
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

VISHAL_ORCHESTRATOR_PATH = "/home/vishal-patel/Downloads/Adobe/geo-agent-skill/Vishal/brand-ai-readiness-audit/skills/audit-orchestrator/scripts/orchestrator.py"

def run_audit(url: str) -> dict[str, Any]:
    """Run Vishal's orchestrator and map its output to the evaluation suite format."""
    
    try:
        result = subprocess.run(
            ["python3", VISHAL_ORCHESTRATOR_PATH, url],
            capture_output=True,
            text=True,
            check=True
        )
        orchestrator_output = json.loads(result.stdout)
        vishal_findings = orchestrator_output.get("findings", [])
    except subprocess.CalledProcessError as e:
        print(f"Error running Vishal orchestrator: {e.stderr}")
        vishal_findings = []
    except json.JSONDecodeError:
        print(f"Error decoding Vishal orchestrator output: {result.stdout}")
        vishal_findings = []

    # Map findings
    mapped_findings = []
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    
    for f in vishal_findings:
        sev = f.get("severity", "low")
        counts[sev] = counts.get(sev, 0) + 1
        
        title = f.get("title", "")
        fid = f.get("id", "")
        
        category = "on_page_seo"
        
        # Strict mapping logic aligned with adjudicator's heuristics
        if fid in ("SEO-001", "CRAWL-001b", "CRAWL-014"):
            category = "crawlability"
        elif fid in ("DATA-001", "DATA-002", "SEMANTIC-003", "SEMANTIC-004"):
            category = "structured_data"
        elif fid == "REND-001":
            category = "rendering"
        elif fid == "CONTENT-001":
            category = "content_extraction"
        elif fid == "CONTENT-009":
            category = "non_text_facts"
        elif fid == "FRESH-001":
            category = "freshness"
        elif fid == "BRAND-001":
            category = "entity_identity"
        elif fid == "NAV-001":
            category = "engagement"
        else:
            category = "on_page_seo"

        mapped_findings.append({
            "id": fid,
            "title": title,
            "severity": sev,
            "evidence": f.get("evidence", ""),
            "suggested_action": f.get("suggested_action", {}),
            "_category": category,
            "_source": "vishal_orchestrator"
        })
        
    counts["total_findings"] = sum(counts.values())
    
    domain = urlparse(url).netloc
    return {
        "site": domain,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": counts,
        "findings": mapped_findings,
    }
