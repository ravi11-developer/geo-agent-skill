#!/usr/bin/env python3
"""Agent D: Multi-Specialist Marketplace

Six specialist skills + orchestrator with genuine separation of concerns.
Each specialist produces independent findings; the orchestrator merges,
deduplicates, and prioritizes them.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = "SpecialistAuditor/1.0 (+research)"
REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Shared crawler
# ---------------------------------------------------------------------------

def crawl_site(url: str, max_pages: int = 15) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    parsed = urlparse(url)

    pages = []
    visited = set()
    queue = [url]
    visited.add(url)

    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    site_prefix = "/" + path_parts[0] + "/" if path_parts else "/"

    while queue and len(pages) < max_pages:
        current = queue.pop(0)
        entry = {
            "url": current, "html_raw": None, "status_code": None,
            "error": None, "headers": {},
        }
        try:
            resp = session.get(current, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            entry["status_code"] = resp.status_code
            entry["headers"] = dict(resp.headers)
            if "text/html" in resp.headers.get("Content-Type", "") and resp.status_code < 400:
                entry["html_raw"] = resp.text
        except Exception as exc:
            entry["error"] = str(exc)

        pages.append(entry)

        if entry["html_raw"]:
            soup = BeautifulSoup(entry["html_raw"], "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                abs_url = urljoin(current, href)
                abs_parsed = urlparse(abs_url)
                if abs_parsed.netloc == parsed.netloc and abs_url not in visited:
                    if site_prefix != "/" and not (abs_parsed.path == site_prefix.rstrip("/") or abs_parsed.path.startswith(site_prefix)):
                        continue
                    visited.add(abs_url)
                    queue.append(abs_url)

        time.sleep(0.05)

    return pages


def _make(title, severity, evidence, action, category):
    return {
        "title": title, "severity": severity, "evidence": evidence,
        "suggested_action": {"summary": action, "priority": severity},
        "_category": category, "_source": "",
    }


# ---------------------------------------------------------------------------
# Specialist 1: Crawl/Render Auditor
# ---------------------------------------------------------------------------

def skill_crawl_render(pages: list[dict]) -> list[dict]:
    """Check crawlability and rendering gaps."""
    findings = []

    for page in pages:
        if page.get("status_code") and page["status_code"] >= 400:
            findings.append(_make(
                f"HTTP {page['status_code']} Error", "critical",
                f"GET {page['url']} returned {page['status_code']}.",
                "Fix the HTTP error.", "crawlability",
            ))

        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Check noscript warnings
        for ns in soup.find_all("noscript"):
            text = ns.get_text(strip=True).lower()
            if any(kw in text for kw in ("enable javascript", "requires javascript")):
                # Measure visible text
                clean = BeautifulSoup(html, "html.parser")
                for tag in clean.find_all(["script", "style", "noscript"]):
                    tag.decompose()
                visible = clean.get_text(strip=True)
                if len(visible) < 300:
                    findings.append(_make(
                        "Content Requires JavaScript", "high",
                        f"{page['url']}: noscript warning present, only {len(visible)} chars of text in raw HTML.",
                        "Use server-side rendering for critical content.", "rendering",
                    ))
                    break

    for f in findings:
        f["_source"] = "crawl_render"
    return findings


# ---------------------------------------------------------------------------
# Specialist 2: Structured Data Auditor
# ---------------------------------------------------------------------------

def skill_structured_data(pages: list[dict]) -> list[dict]:
    """Check JSON-LD presence and quality."""
    findings = []
    has_any = False
    types_found = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        if scripts:
            has_any = True
            for s in scripts:
                try:
                    data = json.loads(s.string or "{}")
                    if isinstance(data, dict):
                        types_found.append(data.get("@type", "?"))
                except (json.JSONDecodeError, TypeError):
                    findings.append(_make(
                        "Invalid JSON-LD", "high",
                        f"Malformed JSON-LD on {page['url']}.",
                        "Fix JSON syntax in structured data.", "structured_data",
                    ))

    if not has_any and pages:
        findings.append(_make(
            "No Structured Data Found", "medium",
            f"None of {len(pages)} pages contain JSON-LD structured data.",
            "Add Organization JSON-LD to homepage and Product JSON-LD to product pages.",
            "structured_data",
        ))

    for f in findings:
        f["_source"] = "structured_data"
    return findings


# ---------------------------------------------------------------------------
# Specialist 3: Content/Fact Extraction Auditor
# ---------------------------------------------------------------------------

def skill_content_extraction(pages: list[dict]) -> list[dict]:
    """Check for facts locked in non-text content."""
    findings = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Check for info-bearing images without text equivalents
        images = soup.find_all("img")
        info_imgs = []
        for img in images:
            src = (img.get("src") or "").lower()
            alt = (img.get("alt") or "").lower()
            if any(kw in src for kw in ("product", "pricing", "feature", "info",
                                          "contact", "result", "data", "table",
                                          "partner", "customer", "comparison")):
                if len(alt) < 15:
                    info_imgs.append(img.get("src", ""))

        if info_imgs:
            clean = BeautifulSoup(html, "html.parser")
            for tag in clean.find_all(["script", "style", "noscript"]):
                tag.decompose()
            text_len = len(clean.get_text(strip=True))

            if text_len < 500:
                findings.append(_make(
                    "Business Facts Locked in Images", "high",
                    f"{page['url']}: {len(info_imgs)} info-bearing images, only {text_len} chars of text.",
                    "Reproduce image content as HTML text with descriptive alt attributes.",
                    "non_text_facts",
                ))

    for f in findings:
        f["_source"] = "content_extraction"
    return findings


# ---------------------------------------------------------------------------
# Specialist 4: Freshness/Corroboration Auditor
# ---------------------------------------------------------------------------

def skill_freshness(pages: list[dict]) -> list[dict]:
    """Check for stale content."""
    findings = []
    current_year = datetime.now().year
    stale_threshold = current_year - 2
    all_years = []
    stale_signals = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        years = re.findall(r'\b(20[12]\d)\b', text)
        for ym in years:
            y = int(ym)
            if 2015 <= y <= current_year:
                all_years.append(y)

        # Check specific staleness patterns
        for pattern, label in [
            (r'(?:last\s+updated|updated)\s*:?\s*.*?(20[12]\d)', "update date"),
            (r'(?:pricing|prices?)\s+(?:effective|as of)\s+.*?(20[12]\d)', "pricing date"),
            (r'©\s*(20[12]\d)', "copyright"),
        ]:
            match = re.search(pattern, text, re.I)
            if match and int(match.group(1)) < stale_threshold:
                stale_signals.append(f"{label}={match.group(1)}")

    if stale_signals:
        most_recent = max(all_years) if all_years else 0
        findings.append(_make(
            "Stale Content", "high",
            f"Stale signals: {'; '.join(stale_signals)}. Most recent year: {most_recent}.",
            "Update all time-sensitive content and add visible publication dates.",
            "freshness",
        ))
    elif all_years and max(all_years) < stale_threshold:
        findings.append(_make(
            "No Recent Content", "medium",
            f"Most recent year referenced: {max(all_years)} (current: {current_year}).",
            "Add recent updates and news.", "freshness",
        ))

    for f in findings:
        f["_source"] = "freshness"
    return findings


# ---------------------------------------------------------------------------
# Specialist 5: Entity/Identity Auditor
# ---------------------------------------------------------------------------

def skill_entity_identity(pages: list[dict]) -> list[dict]:
    """Check for entity name consistency."""
    findings = []
    names = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Title
        title = soup.find("title")
        if title:
            t = title.get_text(strip=True)
            for sep in (" - ", " | ", " — ", " :: "):
                if sep in t:
                    names.append(t.split(sep)[0].strip())
                    break

        # H1
        h1 = soup.find("h1")
        if h1:
            h1t = h1.get_text(strip=True)
            if len(h1t) < 60:
                names.append(h1t)

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    for key in ("name", "alternateName"):
                        if data.get(key):
                            names.append(data[key])
            except (json.JSONDecodeError, TypeError):
                pass

        # Footer copyright
        footer = soup.find("footer")
        if footer:
            ft = footer.get_text(strip=True)
            match = re.search(r'©\s*\d{4}\s+([^.]+?)(?:\.|All rights|$)', ft)
            if match:
                names.append(match.group(1).strip().rstrip(','))

        # Body text patterns
        text = soup.get_text(" ", strip=True)
        for pattern in [r'(?:Welcome to|About)\s+([A-Z][A-Za-z\s]+?)(?:\.|,|\s+is\s)']:
            match = re.search(pattern, text)
            if match:
                n = match.group(1).strip()
                if 3 < len(n) < 50:
                    names.append(n)

    # Deduplicate by root
    unique = set(n.strip() for n in names if len(n.strip()) > 2)
    name_roots = {}
    for n in unique:
        root = re.sub(r'\s+(Inc|Corp|LLC|Ltd|Co|Group|Holdings|Industries|Solutions|Technologies|Corporation)\b\.?',
                       '', n, flags=re.I).strip().lower()
        if root not in name_roots:
            name_roots[root] = []
        name_roots[root].append(n)

    if len(name_roots) > 2:
        all_names = [n for ns in name_roots.values() for n in ns]
        findings.append(_make(
            "Entity Name Inconsistency", "high",
            f"{len(all_names)} distinct names across {len(name_roots)} roots: "
            f"{', '.join(repr(n) for n in all_names[:6])}.",
            "Use one canonical name consistently across all pages and JSON-LD.",
            "entity_identity",
        ))

    for f in findings:
        f["_source"] = "entity_identity"
    return findings


# ---------------------------------------------------------------------------
# Specialist 6: Engagement/Orientation Auditor
# ---------------------------------------------------------------------------

def skill_engagement(pages: list[dict]) -> list[dict]:
    """Check navigation and engagement signals."""
    findings = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        has_nav = bool(soup.find("nav"))
        parsed = urlparse(page["url"])
        internal_links = 0
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = urljoin(page["url"], href)
            if urlparse(abs_url).netloc == parsed.netloc:
                internal_links += 1

        if not has_nav and internal_links < 3:
            findings.append(_make(
                "Poor Navigation Structure", "high",
                f"{page['url']}: no <nav>, {internal_links} internal links.",
                "Add navigation menu with links to key sections.",
                "engagement",
            ))
            break  # One finding is enough for navigation

    for f in findings:
        f["_source"] = "engagement"
    return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def orchestrate(all_findings: list[dict]) -> list[dict]:
    """Merge, deduplicate, reconcile, and prioritize findings."""
    # Deduplicate by category
    seen_categories = set()
    deduped = []
    for f in all_findings:
        cat = f.get("_category", "other")
        if cat not in seen_categories:
            seen_categories.add(cat)
            deduped.append(f)
        else:
            # Merge: keep the one with longer evidence
            for existing in deduped:
                if existing.get("_category") == cat:
                    if len(f.get("evidence", "")) > len(existing.get("evidence", "")):
                        existing["evidence"] = f["evidence"]
                    # Keep highest severity
                    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
                    if sev_rank.get(f["severity"], 0) > sev_rank.get(existing["severity"], 0):
                        existing["severity"] = f["severity"]
                        existing["suggested_action"]["priority"] = f["severity"]
                    break

    return deduped


def run_audit(url: str) -> dict[str, Any]:
    """Run the specialist marketplace audit."""
    pages = crawl_site(url, max_pages=15)

    # Run all specialists
    all_findings = []
    all_findings.extend(skill_crawl_render(pages))
    all_findings.extend(skill_structured_data(pages))
    all_findings.extend(skill_content_extraction(pages))
    all_findings.extend(skill_freshness(pages))
    all_findings.extend(skill_entity_identity(pages))
    all_findings.extend(skill_engagement(pages))

    # Orchestrate
    findings = orchestrate(all_findings)

    # Sort and assign IDs
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
