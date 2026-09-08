#!/usr/bin/env python3
"""Agent C: Reasoning-First Website Intelligence Agent

Crawls a website, builds a brand/entity model, then reasons about
why an AI assistant might fail to discover, trust, retrieve, cite,
or accurately represent this brand.

Uses heuristic reasoning (not LLM calls) for reproducibility.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None


USER_AGENT = "ReasoningAuditor/1.0 (+research)"
REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Brand Model
# ---------------------------------------------------------------------------

class BrandModel:
    """Represents extracted knowledge about the brand/entity."""

    def __init__(self):
        self.names: list[str] = []
        self.descriptions: list[str] = []
        self.products: list[dict] = []
        self.prices: list[dict] = []
        self.facts: list[str] = []
        self.claims: list[str] = []
        self.dates: list[tuple[int, str]] = []  # (year, context)
        self.contact_info: dict = {}
        self.trust_signals: list[str] = []  # certifications, awards
        self.navigation_links: list[str] = []
        self.structured_data: list[dict] = []
        self.page_count: int = 0
        self.total_text_length: int = 0
        self.has_nav: bool = False
        self.has_footer: bool = False
        self.image_count: int = 0
        self.info_images_count: int = 0  # images that seem to contain facts
        self.js_dependent: bool = False
        self.pages: list[dict] = []


def extract_brand_model(pages: list[dict]) -> BrandModel:
    """Build a comprehensive brand model from crawled pages."""
    model = BrandModel()
    model.page_count = len(pages)
    model.pages = pages

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        # --- Names ---
        title = soup.find("title")
        if title:
            t = title.get_text(strip=True)
            for sep in (" - ", " | ", " — ", " :: "):
                if sep in t:
                    model.names.append(t.split(sep)[0].strip())
                    break
            else:
                if len(t) < 60:
                    model.names.append(t)

        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            if len(h1_text) < 60:
                model.names.append(h1_text)

        # From JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                model.structured_data.append(data)
                if isinstance(data, dict):
                    if data.get("name"):
                        model.names.append(data["name"])
                    if data.get("alternateName"):
                        model.names.append(data["alternateName"])
                    if data.get("description"):
                        model.descriptions.append(data["description"])
            except (json.JSONDecodeError, TypeError):
                pass

        # From footer copyright
        footer = soup.find("footer")
        if footer:
            model.has_footer = True
            ft = footer.get_text(strip=True)
            match = re.search(r'©\s*\d{4}\s+([^.]+?)(?:\.|All rights|$)', ft)
            if match:
                model.names.append(match.group(1).strip().rstrip(','))

        # --- Navigation ---
        nav = soup.find("nav")
        if nav:
            model.has_nav = True
            for a in nav.find_all("a", href=True):
                model.navigation_links.append(a["href"])

        # --- Text content ---
        text_soup = BeautifulSoup(html, "html.parser")
        for tag in text_soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        visible_text = text_soup.get_text(" ", strip=True)
        model.total_text_length += len(visible_text)

        # --- Dates ---
        year_matches = re.findall(r'\b(20[12]\d)\b', visible_text)
        for ym in year_matches:
            # Get context around the year
            idx = visible_text.find(ym)
            context = visible_text[max(0, idx-40):idx+40]
            model.dates.append((int(ym), context))

        # --- Products/Prices ---
        for match in re.finditer(r'\$[\d,]+(?:\.\d{2})?(?:/\w+)?', visible_text):
            price_ctx = visible_text[max(0, match.start()-50):match.end()+30]
            model.prices.append({"price": match.group(), "context": price_ctx})

        # --- Trust signals ---
        trust_patterns = [
            r'SOC\s*2', r'ISO\s*2700[01]', r'GDPR', r'HIPAA',
            r'certified', r'compliance', r'uptime\s+SLA',
        ]
        for pattern in trust_patterns:
            if re.search(pattern, visible_text, re.I):
                model.trust_signals.append(pattern)

        # --- Images ---
        images = soup.find_all("img")
        model.image_count += len(images)
        for img in images:
            src = img.get("src", "").lower()
            alt = img.get("alt", "").lower()
            if any(kw in src for kw in ("product", "pricing", "feature", "info",
                                          "contact", "result", "data", "table")):
                if len(alt) < 15:
                    model.info_images_count += 1

        # --- JS dependency ---
        noscript = soup.find_all("noscript")
        for ns in noscript:
            if any(kw in ns.get_text(strip=True).lower()
                   for kw in ("enable javascript", "requires javascript")):
                model.js_dependent = True

    return model


# ---------------------------------------------------------------------------
# Reasoning Engine
# ---------------------------------------------------------------------------

def reason_about_discoverability(model: BrandModel) -> list[dict]:
    """Reason about what would cause an AI to fail with this brand."""
    findings = []
    current_year = datetime.now().year

    # --- 1. Can an AI identify the entity? ---
    unique_names = list(set(n.strip() for n in model.names if len(n.strip()) > 2))
    # Deduplicate by lowercase
    name_map = {}
    for n in unique_names:
        key = re.sub(r'\s+(Inc|Corp|LLC|Ltd|Co|Group|Holdings|Industries|Solutions|Technologies)\b\.?',
                     '', n, flags=re.I).strip().lower()
        if key not in name_map:
            name_map[key] = []
        name_map[key].append(n)

    if len(name_map) > 2:
        all_names = [n for names in name_map.values() for n in names]
        findings.append({
            "title": "Entity Identity Ambiguity",
            "severity": "high",
            "evidence": (
                f"An AI system attempting to identify this brand would encounter "
                f"{len(all_names)} different names: {', '.join(repr(n) for n in all_names[:6])}. "
                f"These map to {len(name_map)} distinct root names. "
                "This makes it impossible for AI to confidently associate facts with a single entity."
            ),
            "suggested_action": {
                "summary": (
                    "Choose one canonical name and use it in the title, H1, JSON-LD 'name' field, "
                    "footer, and body text. Use 'alternateName' in JSON-LD for any well-known abbreviation."
                ),
                "priority": "high",
            },
            "_category": "entity_identity",
        })

    # --- 2. Can an AI extract key facts? ---
    if model.js_dependent:
        findings.append({
            "title": "Business-Critical Content Hidden Behind JavaScript",
            "severity": "high",
            "evidence": (
                "The site explicitly warns that JavaScript is required to view content. "
                f"Only {model.total_text_length} characters of text are in the raw HTML. "
                "AI crawlers that do not execute JavaScript will miss products, pricing, and company facts."
            ),
            "suggested_action": {
                "summary": (
                    "Use server-side rendering to include product descriptions, pricing, and key facts "
                    "in the initial HTML. Mirror these facts in JSON-LD structured data."
                ),
                "priority": "high",
            },
            "_category": "rendering",
        })

    if model.info_images_count > 2 and model.total_text_length < 600:
        findings.append({
            "title": "Important Facts Locked in Non-Text Content",
            "severity": "high",
            "evidence": (
                f"Found {model.info_images_count} images with filenames suggesting factual content "
                f"(products, pricing, etc.) but only {model.total_text_length} characters of "
                "extractable text. AI systems cannot read text from images."
            ),
            "suggested_action": {
                "summary": (
                    "Reproduce all factual content currently in images as HTML text. "
                    "Use images as supplementary visual content with descriptive alt text."
                ),
                "priority": "high",
            },
            "_category": "non_text_facts",
        })

    # --- 3. Can an AI trust the facts? ---
    if model.dates:
        years = [d[0] for d in model.dates]
        most_recent = max(years)
        if most_recent < current_year - 1:
            stale_contexts = [f"'{d[1].strip()[:60]}'" for d in model.dates
                            if d[0] < current_year - 1][:4]
            findings.append({
                "title": "Stale Content Undermines Trust",
                "severity": "high",
                "evidence": (
                    f"No content on the site references anything more recent than {most_recent} "
                    f"(current year: {current_year}). "
                    f"Examples of dated content: {'; '.join(stale_contexts)}. "
                    "An AI system citing this information risks presenting outdated facts as current."
                ),
                "suggested_action": {
                    "summary": (
                        "Update all time-sensitive content (pricing, team, statistics, press releases). "
                        "Add visible publication/update dates. Remove or clearly label historical information."
                    ),
                    "priority": "high",
                },
                "_category": "freshness",
            })

    # --- 4. Can an AI understand the brand's structured identity? ---
    if not model.structured_data:
        findings.append({
            "title": "No Machine-Readable Brand Identity",
            "severity": "medium",
            "evidence": (
                f"None of the {model.page_count} crawled pages contain JSON-LD structured data. "
                "AI systems use structured data to understand entity type, name, description, "
                "location, products, and relationships. Without it, the AI must infer these from "
                "unstructured text, which is less reliable."
            ),
            "suggested_action": {
                "summary": (
                    "Add Organization JSON-LD with name, description, url, foundingDate, address, "
                    "and contactPoint. Add Product/Service JSON-LD to product pages."
                ),
                "priority": "medium",
            },
            "_category": "structured_data",
        })

    # --- 5. Can a user orient themselves on the site? ---
    if not model.has_nav and len(model.navigation_links) < 2:
        findings.append({
            "title": "No Site Navigation for User or Bot Orientation",
            "severity": "high",
            "evidence": (
                "The site has no <nav> element and fewer than 2 navigation links. "
                "A user arriving from an AI citation cannot discover related pages, "
                "understand the site structure, or continue engaging with the brand."
            ),
            "suggested_action": {
                "summary": (
                    "Add a persistent navigation menu with links to core pages "
                    "(About, Products, Pricing, Contact). Add breadcrumbs and a footer "
                    "with site-wide links."
                ),
                "priority": "high",
            },
            "_category": "engagement",
        })

    return findings


# ---------------------------------------------------------------------------
# Crawling (reused from deterministic)
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
        entry = {"url": current, "html_raw": None, "status_code": None, "error": None}
        try:
            resp = session.get(current, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            entry["status_code"] = resp.status_code
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_audit(url: str) -> dict[str, Any]:
    """Run the reasoning-first audit."""
    pages = crawl_site(url, max_pages=15)
    model = extract_brand_model(pages)
    findings = reason_about_discoverability(model)

    # Assign IDs
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
