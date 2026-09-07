#!/usr/bin/env python3
"""
SEO Audit Crawler — Core Crawling Engine
=========================================
Responsibly crawls a public website (respecting robots.txt) and collects
per-page raw data for SEO analysis. Returns structured dicts that the
analyzer module consumes.

This module is NOT the CLI entry point — see seo_audit.py for that.
"""

from __future__ import annotations

import ipaddress
import logging
import random
import re
import socket
import time
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

import requests
import trafilatura
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 2
DEFAULT_DELAY = 1.5  # seconds between requests
DEFAULT_USER_AGENT = (
    "SEOAuditCrawler/1.0 "
    "(+https://github.com/example/seo-audit-crawler; research bot)"
)
REQUEST_TIMEOUT = 15  # seconds per HTTP request

# Tracking / analytics query parameters to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
    "mc_cid", "mc_eid", "ref", "referrer",
}

log = logging.getLogger("seo-crawler")

# ---------------------------------------------------------------------------
# URL safety helpers
# ---------------------------------------------------------------------------


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to a loopback / private / reserved IP."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True  # can't resolve → treat as unsafe
    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
        ):
            return True
    return False


def validate_url(raw: str) -> str:
    """Validate and normalise *raw* URL.  Raises ValueError on unsafe input."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{parsed.scheme}' — only http/https allowed")
    if not parsed.hostname:
        raise ValueError("URL has no hostname")

    hostname_lower = parsed.hostname.lower()
    if hostname_lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("Requests to localhost are not allowed")

    if _is_private_ip(hostname_lower):
        raise ValueError(
            f"Hostname '{hostname_lower}' resolves to a private/reserved IP — blocked for SSRF safety"
        )

    return raw


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def strip_tracking_params(url: str) -> str:
    """Remove common analytics query parameters from *url*."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in qs.items() if k.lower() not in TRACKING_PARAMS}
    new_query = urlencode(cleaned, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def normalise_url(url: str) -> str:
    """Normalise *url* for deduplication (strip fragment & tracking params)."""
    parsed = urlparse(url)
    cleaned = parsed._replace(fragment="")
    return strip_tracking_params(urlunparse(cleaned))


def is_same_domain(url: str, base_domain: str) -> bool:
    """Return True if *url* belongs to *base_domain* (including subdomains)."""
    host = urlparse(url).hostname
    if host is None:
        return False
    host = host.lower()
    return host == base_domain or host.endswith(f".{base_domain}")


# ---------------------------------------------------------------------------
# Robots.txt handling
# ---------------------------------------------------------------------------


def fetch_robots(base_url: str, user_agent: str, session: requests.Session):
    """Return a configured RobotFileParser for *base_url* and the raw text."""
    from urllib.robotparser import RobotFileParser

    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    robots_text = None
    try:
        resp = session.get(robots_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            robots_text = resp.text
            rp.parse(robots_text.splitlines())
            log.info("robots.txt fetched and parsed from %s", robots_url)
        else:
            rp.parse([])
            log.info("robots.txt returned %d — treating as allow-all", resp.status_code)
    except requests.RequestException as exc:
        rp.parse([])
        log.warning("Could not fetch robots.txt (%s) — treating as allow-all", exc)
    return rp, robots_text


# ---------------------------------------------------------------------------
# Sitemap.xml handling
# ---------------------------------------------------------------------------


def fetch_sitemap(base_url: str, session: requests.Session) -> list[str]:
    """Fetch and parse sitemap.xml, returning a list of URLs found."""
    parsed = urlparse(base_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    urls: list[str] = []
    try:
        resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "lxml-xml")
            # Handle sitemap index (nested sitemaps)
            sitemap_tags = soup.find_all("sitemap")
            if sitemap_tags:
                # It's a sitemap index — fetch each child sitemap
                for sm in sitemap_tags:
                    loc = sm.find("loc")
                    if loc and loc.text.strip():
                        try:
                            child_resp = session.get(loc.text.strip(), timeout=REQUEST_TIMEOUT)
                            if child_resp.status_code == 200:
                                child_soup = BeautifulSoup(child_resp.content, "lxml-xml")
                                for url_tag in child_soup.find_all("url"):
                                    loc_tag = url_tag.find("loc")
                                    if loc_tag and loc_tag.text.strip():
                                        urls.append(loc_tag.text.strip())
                        except requests.RequestException:
                            pass
            else:
                # Regular sitemap
                for url_tag in soup.find_all("url"):
                    loc_tag = url_tag.find("loc")
                    if loc_tag and loc_tag.text.strip():
                        urls.append(loc_tag.text.strip())
            log.info("sitemap.xml: found %d URLs", len(urls))
        else:
            log.info("sitemap.xml returned %d — no sitemap found", resp.status_code)
    except requests.RequestException as exc:
        log.warning("Could not fetch sitemap.xml (%s)", exc)
    return urls


# ---------------------------------------------------------------------------
# Content & link extraction
# ---------------------------------------------------------------------------


def extract_content(html: str, url: str) -> dict[str, str | None]:
    """Extract title, meta description, and clean body text from *html*."""
    body_text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        favor_precision=True,
        url=url,
    )

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    meta_desc = None
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"].strip()

    return {
        "title": title,
        "meta_description": meta_desc,
        "text": body_text,
    }


def extract_links(html: str, page_url: str, base_domain: str) -> tuple[list[str], list[str]]:
    """Return (internal_links, external_links) found in *html*."""
    soup = BeautifulSoup(html, "lxml")
    internal: list[str] = []
    external: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if is_same_domain(absolute, base_domain):
            internal.append(absolute)
        else:
            external.append(absolute)
    return internal, external


# ---------------------------------------------------------------------------
# BFS Crawler
# ---------------------------------------------------------------------------


def crawl(
    start_url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    delay: float = DEFAULT_DELAY,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """BFS-crawl starting from *start_url*.

    Returns:
        (pages, site_data) where:
        - pages: list of per-page data dicts
        - site_data: dict with sitemap_urls, robots_txt_content, base_url, base_domain
    """

    start_url = validate_url(start_url)
    base_domain = urlparse(start_url).hostname.lower()

    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # --- Site-wide fetches ---
    robot_parser, robots_text = fetch_robots(start_url, user_agent, session)
    sitemap_urls = fetch_sitemap(start_url, session)

    site_data: dict[str, Any] = {
        "sitemap_urls": sitemap_urls,
        "robots_txt_content": robots_text,
        "base_url": start_url,
        "base_domain": base_domain,
    }

    # --- BFS state ---
    queue: deque[tuple[str, int]] = deque()  # (url, depth)
    visited: set[str] = set()
    results: list[dict[str, Any]] = []

    norm_start = normalise_url(start_url)
    queue.append((norm_start, 0))
    visited.add(norm_start)

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()

        # Check robots.txt
        if not robot_parser.can_fetch(user_agent, url):
            log.info("SKIP (robots.txt disallow): %s", url)
            continue

        # --- Build page entry ---
        entry: dict[str, Any] = {
            "url": url,
            "title": None,
            "meta_description": None,
            "text": None,
            "html_raw": None,
            "status_code": None,
            "response_headers": {},
            "redirect_chain": [],
            "content_length": 0,
            "links_internal": [],
            "links_external": [],
            "is_https": urlparse(url).scheme == "https",
            "depth": depth,
            "error": None,
        }

        try:
            log.info("FETCH [depth=%d, #%d]: %s", depth, len(results) + 1, url)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            entry["status_code"] = resp.status_code
            entry["response_headers"] = dict(resp.headers)
            entry["redirect_chain"] = [r.url for r in resp.history]
            entry["content_length"] = len(resp.content)

            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                entry["error"] = f"Non-HTML content type: {content_type}"
                results.append(entry)
                continue

            resp.raise_for_status()
            html = resp.text
            entry["html_raw"] = html

        except requests.Timeout:
            entry["error"] = "Request timed out"
            results.append(entry)
            continue
        except requests.ConnectionError as exc:
            entry["error"] = f"Connection error: {exc}"
            results.append(entry)
            continue
        except requests.HTTPError as exc:
            entry["error"] = f"HTTP error: {exc}"
            results.append(entry)
            continue
        except requests.RequestException as exc:
            entry["error"] = f"Request failed: {exc}"
            results.append(entry)
            continue

        # --- Extract content ---
        try:
            content = extract_content(html, url)
            entry.update(content)
        except Exception as exc:
            entry["error"] = f"Extraction error: {exc}"

        # --- Extract links ---
        try:
            internal_links, external_links = extract_links(html, url, base_domain)
            entry["links_internal"] = internal_links
            entry["links_external"] = external_links
        except Exception:
            pass

        results.append(entry)

        # --- Discover links (only if we haven't hit max depth) ---
        if depth < max_depth:
            for link in entry["links_internal"]:
                norm_link = normalise_url(link)
                if norm_link in visited:
                    continue
                visited.add(norm_link)
                queue.append((norm_link, depth + 1))

        # --- Polite delay (jittered) ---
        jitter = delay + random.uniform(0, 0.5)
        time.sleep(jitter)

    log.info("Crawl complete — %d pages collected", len(results))
    return results, site_data
