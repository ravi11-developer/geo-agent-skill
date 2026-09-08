#!/usr/bin/env python3
"""Agent B: Deterministic Technical Auditor

Focused on AI discoverability and engagement, NOT generic SEO.
Every check is a concrete, deterministic measurement.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

USER_AGENT = "BenchmarkAuditor/1.0 (+research; respects robots.txt)"
REQUEST_TIMEOUT = 15


def fetch_page(session: requests.Session, url: str) -> dict[str, Any]:
    """Fetch a single page and extract raw data."""
    entry = {
        "url": url, "status_code": None, "html_raw": None,
        "headers": {}, "error": None, "content_length": 0,
    }
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        entry["status_code"] = resp.status_code
        entry["headers"] = dict(resp.headers)
        entry["content_length"] = len(resp.content)
        ct = resp.headers.get("Content-Type", "")
        if "text/html" in ct and resp.status_code < 400:
            entry["html_raw"] = resp.text
    except Exception as exc:
        entry["error"] = str(exc)
    return entry


def crawl_site(url: str, max_pages: int = 15) -> tuple[list[dict], dict]:
    """Simple BFS crawl. Returns (pages, site_data)."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Fetch robots.txt
    robots_txt = None
    try:
        r = session.get(f"{base}/robots.txt", timeout=10)
        if r.status_code == 200:
            robots_txt = r.text
    except Exception:
        pass

    # Fetch sitemap
    sitemap_urls = []
    try:
        r = session.get(f"{base}/sitemap.xml", timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.content, "html.parser")
            for loc in soup.find_all("loc"):
                if loc.text.strip():
                    sitemap_urls.append(loc.text.strip())
    except Exception:
        pass

    # BFS
    pages = []
    visited = set()
    queue = [url]
    visited.add(url)

    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    site_prefix = "/" + path_parts[0] + "/" if path_parts else "/"

    while queue and len(pages) < max_pages:
        current = queue.pop(0)
        page = fetch_page(session, current)
        pages.append(page)

        if page["html_raw"]:
            soup = BeautifulSoup(page["html_raw"], "html.parser")
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

    site_data = {
        "robots_txt": robots_txt,
        "sitemap_urls": sitemap_urls,
        "base_url": url,
    }
    return pages, site_data


# ---------------------------------------------------------------------------
# Finding builder
# ---------------------------------------------------------------------------

def make_finding(fid: str, title: str, severity: str, evidence: str,
                 action: str, category: str = "") -> dict:
    return {
        "id": fid,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "suggested_action": {
            "summary": action,
            "priority": severity,
        },
        "_category": category,
    }


# ---------------------------------------------------------------------------
# AI Discoverability Checks
# ---------------------------------------------------------------------------

def check_structured_data(pages: list[dict]) -> list[dict]:
    """Check for JSON-LD structured data presence and quality."""
    findings = []
    pages_with_jsonld = 0
    total_pages = 0
    schema_types_found = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        total_pages += 1
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")

        if scripts:
            pages_with_jsonld += 1
            for script in scripts:
                try:
                    data = json.loads(script.string or "{}")
                    if isinstance(data, dict):
                        schema_types_found.append(data.get("@type", "unknown"))
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                schema_types_found.append(item.get("@type", "unknown"))
                except (json.JSONDecodeError, TypeError):
                    findings.append(make_finding(
                        "", "Invalid JSON-LD Structured Data", "high",
                        f"JSON-LD on {page['url']} contains invalid JSON that cannot be parsed by machines.",
                        "Fix the JSON syntax errors in the JSON-LD structured data block.",
                        "structured_data",
                    ))

    if total_pages > 0 and pages_with_jsonld == 0:
        findings.append(make_finding(
            "", "No Structured Data (JSON-LD) Found", "medium",
            f"None of the {total_pages} crawled pages contain JSON-LD structured data. "
            "AI systems and search engines rely on structured data to understand entity identity, "
            "products, and organization details in a machine-readable format.",
            "Add Organization JSON-LD to the homepage and Product/Service JSON-LD to product pages "
            "to make key business facts machine-readable for AI systems.",
            "structured_data",
        ))
    elif total_pages > 0 and pages_with_jsonld > 0:
        # Check if Organization type is present
        has_org = any(t in ("Organization", "Corporation", "LocalBusiness")
                      for t in schema_types_found)
        if not has_org:
            findings.append(make_finding(
                "", "Missing Organization Structured Data", "low",
                f"JSON-LD is present (types: {', '.join(set(schema_types_found))}) but no "
                "Organization/Corporation type was found. AI systems use Organization schema "
                "to identify the entity behind the website.",
                "Add Organization JSON-LD with name, description, url, and contactPoint properties.",
                "structured_data",
            ))

    return findings


def check_rendering_gaps(pages: list[dict]) -> list[dict]:
    """Check for content that is only available via JavaScript rendering."""
    findings = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Check 1: noscript tags with warnings about JS
        noscript_tags = soup.find_all("noscript")
        for ns in noscript_tags:
            text = ns.get_text(strip=True).lower()
            if any(kw in text for kw in ("enable javascript", "requires javascript",
                                          "javascript is required", "please enable")):
                # Check if main content area is thin
                main_text = ""
                for tag in soup.find_all(["main", "article", "section", "div"]):
                    tag_id = tag.get("id", "")
                    tag_class = " ".join(tag.get("class", []))
                    if any(k in (tag_id + tag_class).lower()
                           for k in ("content", "main", "app", "root")):
                        main_text = tag.get_text(strip=True)
                        break

                # Count visible text (excluding scripts/styles)
                for s in soup.find_all(["script", "style", "noscript"]):
                    s.decompose()
                visible_text = soup.get_text(strip=True)

                if len(visible_text) < 200:
                    findings.append(make_finding(
                        "", "Critical Content Requires JavaScript Rendering", "high",
                        f"Page {page['url']} contains a noscript warning ('{ns.get_text(strip=True)[:100]}') "
                        f"and the raw HTML contains only {len(visible_text)} characters of visible text. "
                        "Non-rendering crawlers and AI systems will see minimal content.",
                        "Render critical business content (products, pricing, company facts) in the "
                        "initial HTML response using server-side rendering or static generation.",
                        "rendering",
                    ))
                    break

        # Check 2: JS framework indicators with thin body
        body = soup.find("body")
        if body:
            # Look for React/Vue/Angular root elements with no content
            for div in body.find_all("div"):
                div_id = div.get("id", "")
                if div_id in ("root", "app", "__next", "__nuxt") and not div.get_text(strip=True):
                    findings.append(make_finding(
                        "", "Single-Page Application with Empty Root", "high",
                        f"Page {page['url']} has a #{div_id} element with no server-rendered content. "
                        "This indicates a client-side SPA where content is injected via JavaScript. "
                        "Non-rendering crawlers will see an empty page.",
                        "Use server-side rendering (SSR) or static site generation (SSG) to ensure "
                        "critical content is available in the initial HTML response.",
                        "rendering",
                    ))
                    break

        # Check 3: Hidden divs that appear to be JS-populated
        for div in soup.find_all("div", style=True):
            style = div.get("style", "")
            if "display: none" in style or "display:none" in style:
                div_id = div.get("id", "")
                if div_id and not div.get_text(strip=True):
                    # Hidden empty div with an ID = likely JS-populated
                    pass  # Don't flag unless combined with noscript

    return findings


def check_entity_identity(pages: list[dict]) -> list[dict]:
    """Check for entity/brand name consistency across pages."""
    findings = []
    names_found: dict[str, list[str]] = {}  # name -> [urls where found]

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Extract candidate entity names from various sources
        candidates = set()

        # From title
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            # Extract company name (usually before " - " or " | ")
            for sep in (" - ", " | ", " — ", " :: "):
                if sep in title_text:
                    candidates.add(title_text.split(sep)[0].strip())
                    break

        # From JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    if data.get("name"):
                        candidates.add(data["name"])
                    if data.get("alternateName"):
                        candidates.add(data["alternateName"])
            except (json.JSONDecodeError, TypeError):
                pass

        # From h1
        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            if len(h1_text) < 60:  # Likely a brand name if short
                candidates.add(h1_text)

        # From OG title
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            og_text = og["content"]
            for sep in (" - ", " | ", " — "):
                if sep in og_text:
                    og_text = og_text.split(sep)[0].strip()
                    break
            candidates.add(og_text)

        # From copyright/footer
        footer = soup.find("footer")
        if footer:
            footer_text = footer.get_text(strip=True)
            # Look for "© YYYY CompanyName" pattern
            match = re.search(r'©\s*\d{4}\s+([^.]+?)(?:\.|All rights|$)', footer_text)
            if match:
                candidates.add(match.group(1).strip().rstrip(','))

        # From body text - look for "about [Company]" patterns
        body_text = soup.get_text(" ", strip=True)
        for pattern in [
            r'(?:Welcome to|About)\s+([A-Z][A-Za-z\s]+?)(?:\.|,|\s+is\s)',
        ]:
            match = re.search(pattern, body_text)
            if match:
                name = match.group(1).strip()
                if 3 < len(name) < 50:
                    candidates.add(name)

        # Filter out generic terms
        filtered = set()
        for c in candidates:
            c_clean = c.strip()
            if len(c_clean) < 3 or c_clean.lower() in ("home", "about", "contact", "products"):
                continue
            filtered.add(c_clean)

        for name in filtered:
            if name not in names_found:
                names_found[name] = []
            names_found[name].append(page["url"])

    # Analyze name consistency
    if len(names_found) > 3:
        name_list = sorted(names_found.keys())
        # Check if these are genuinely different names (not just substrings)
        unique_roots = set()
        for name in name_list:
            root = re.sub(r'\s+(Inc|Corp|LLC|Ltd|Co|Group|Holdings|Industries|Solutions|Technologies)\b\.?',
                         '', name, flags=re.I).strip()
            unique_roots.add(root.lower())

        if len(unique_roots) > 2:
            findings.append(make_finding(
                "", "Inconsistent Entity/Brand Names", "high",
                f"The website uses {len(name_list)} different names to refer to the same entity: "
                f"{', '.join(repr(n) for n in name_list[:6])}. "
                "This creates ambiguity for AI systems trying to identify and cite the brand. "
                "An AI assistant may not recognize these as the same company.",
                "Establish one canonical company name and use it consistently in the title tag, "
                "H1, JSON-LD, footer copyright, and body text across all pages. "
                "Use the 'alternateName' property in JSON-LD only for well-known abbreviations.",
                "entity_identity",
            ))

    return findings


def check_freshness(pages: list[dict]) -> list[dict]:
    """Check for stale content indicators."""
    findings = []
    current_year = datetime.now().year
    stale_threshold = current_year - 2  # Anything older than 2 years is stale

    all_years: list[int] = []
    stale_indicators: list[str] = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        # Find all years mentioned
        year_matches = re.findall(r'\b(20[12]\d)\b', text)
        for ym in year_matches:
            y = int(ym)
            if 2015 <= y <= current_year:
                all_years.append(y)

        # Check for explicit staleness indicators
        stale_patterns = [
            (r'(?:last\s+updated|updated)\s*:?\s*.*?(20[12]\d)', "Last updated date"),
            (r'(?:pricing|prices?)\s+(?:effective|as of|from)\s+.*?(20[12]\d)', "Pricing date"),
            (r'©\s*(20[12]\d)', "Copyright year"),
        ]
        for pattern, label in stale_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                year = int(match.group(1))
                if year < stale_threshold:
                    stale_indicators.append(
                        f"{label}: {year} on {page['url']}"
                    )

    if stale_indicators:
        # Most recent year found
        most_recent = max(all_years) if all_years else 0
        oldest = min(all_years) if all_years else 0

        findings.append(make_finding(
            "", "Outdated Content Detected", "high",
            f"Multiple freshness signals indicate stale content. "
            f"Years referenced range from {oldest} to {most_recent} (current year: {current_year}). "
            f"Specific indicators: {'; '.join(stale_indicators[:5])}. "
            "AI systems may cite outdated information as current facts.",
            "Update pricing, team information, statistics, and press releases to reflect "
            "current data. Add visible 'last updated' dates to time-sensitive content. "
            "Update the copyright year in the footer.",
            "freshness",
        ))
    elif all_years:
        most_recent = max(all_years)
        if most_recent < stale_threshold:
            findings.append(make_finding(
                "", "No Recent Content Dates Found", "medium",
                f"The most recent year referenced on the site is {most_recent} "
                f"(current year: {current_year}). No content appears to have been "
                "updated in the last 2+ years.",
                "Add recent news, updates, or blog posts. Update existing content "
                "with current dates and information.",
                "freshness",
            ))

    return findings


def check_non_text_content(pages: list[dict]) -> list[dict]:
    """Check for important information locked in non-text content (images)."""
    findings = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Get text content length
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        visible_text = soup.get_text(strip=True)

        # Count images
        images = soup.find_all("img")
        images_with_vague_alt = []
        for img in images:
            alt = img.get("alt", "")
            src = img.get("src", "")
            # Check if alt is vague/non-descriptive
            if not alt or alt.lower() in ("image", "photo", "picture", "img",
                                            "logo", "icon", "banner"):
                images_with_vague_alt.append(src)
            elif len(alt) < 10 and any(kw in src.lower()
                                        for kw in ("product", "pricing", "feature",
                                                    "contact", "info", "result",
                                                    "partner", "customer", "table",
                                                    "comparison", "chart", "data")):
                images_with_vague_alt.append(f"{src} (alt='{alt}')")

        # If there are many images with info-suggesting filenames but little text
        info_images = [img for img in images_with_vague_alt
                       if any(kw in img.lower()
                              for kw in ("product", "pricing", "feature", "contact",
                                         "info", "result", "partner", "customer",
                                         "table", "comparison", "chart", "data"))]

        if info_images and len(visible_text) < 500:
            findings.append(make_finding(
                "", "Key Business Information Locked in Images", "high",
                f"Page {page['url']} contains {len(info_images)} images with names suggesting "
                f"they contain business-critical information ({', '.join(info_images[:3])}) "
                f"but only {len(visible_text)} characters of text content. "
                "AI systems and screen readers cannot extract facts from images.",
                "Reproduce the information currently in images as machine-readable HTML text. "
                "Add descriptive alt text to images that contain factual information. "
                "Keep images as supplementary visual content.",
                "non_text_facts",
            ))

    return findings


def check_navigation_engagement(pages: list[dict]) -> list[dict]:
    """Check for navigation structure and on-site engagement signals."""
    findings = []

    for page in pages:
        html = page.get("html_raw")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Check for nav element
        has_nav = bool(soup.find("nav"))

        # Check for internal links
        internal_links = []
        parsed_url = urlparse(page["url"])
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = urljoin(page["url"], href)
            if urlparse(abs_url).netloc == parsed_url.netloc:
                internal_links.append(abs_url)

        # Check for header/footer
        has_header = bool(soup.find("header"))
        has_footer = bool(soup.find("footer"))

        # Check for breadcrumbs
        has_breadcrumb = bool(
            soup.find(class_=re.compile(r'breadcrumb', re.I)) or
            soup.find(attrs={"aria-label": re.compile(r'breadcrumb', re.I)})
        )

        if not has_nav and len(internal_links) < 3:
            findings.append(make_finding(
                "", "Weak Navigation and Page Orientation", "high",
                f"Page {page['url']} lacks a navigation element (<nav>) and contains only "
                f"{len(internal_links)} internal links. "
                f"Header present: {has_header}. Footer present: {has_footer}. "
                "Users and AI crawlers cannot discover related content or understand "
                "the site structure.",
                "Add a navigation menu linking to key pages (About, Products, Pricing, Contact). "
                "Add breadcrumb navigation. Add header and footer with site-wide context. "
                "Link to related content from within page body.",
                "engagement",
            ))

    return findings


def check_crawlability(url: str, pages: list[dict], site_data: dict) -> list[dict]:
    """Check basic crawlability (HTTP, robots.txt, sitemap)."""
    findings = []

    # Check if the main page is accessible
    main_page = pages[0] if pages else None
    if main_page and main_page.get("status_code") and main_page["status_code"] >= 400:
        findings.append(make_finding(
            "", "Homepage Returns HTTP Error", "critical",
            f"GET {url} returned HTTP {main_page['status_code']}. "
            "The site is not accessible to crawlers or AI systems.",
            "Fix the HTTP error so the homepage returns a 200 status code.",
            "crawlability",
        ))

    if main_page and main_page.get("error"):
        findings.append(make_finding(
            "", "Homepage Unreachable", "critical",
            f"Could not fetch {url}: {main_page['error']}. "
            "The site is completely inaccessible.",
            "Ensure the server is running and accessible.",
            "crawlability",
        ))

    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_audit(url: str) -> dict[str, Any]:
    """Run the deterministic technical audit."""
    pages, site_data = crawl_site(url, max_pages=15)

    all_findings: list[dict] = []
    all_findings.extend(check_crawlability(url, pages, site_data))
    all_findings.extend(check_structured_data(pages))
    all_findings.extend(check_rendering_gaps(pages))
    all_findings.extend(check_entity_identity(pages))
    all_findings.extend(check_freshness(pages))
    all_findings.extend(check_non_text_content(pages))
    all_findings.extend(check_navigation_engagement(pages))

    # Assign IDs
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_findings.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 4))
    for i, f in enumerate(all_findings, 1):
        f["id"] = f"F-{i:03d}"

    # Build summary
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        sev = f.get("severity", "low")
        if sev in counts:
            counts[sev] += 1
    counts["total_findings"] = sum(counts.values())

    domain = urlparse(url).netloc
    return {
        "site": domain,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": counts,
        "findings": all_findings,
    }
