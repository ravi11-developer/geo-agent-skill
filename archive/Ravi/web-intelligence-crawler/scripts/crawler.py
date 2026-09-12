#!/usr/bin/env python3
"""Robots-aware public website crawler for intelligence reporting.

This script validates a public HTTP/HTTPS URL, checks robots.txt before fetching
any page, crawls allowed internal pages with BFS, extracts cleaned page content,
and prints a JSON array to stdout.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import random
import re
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence, Set
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 2
DEFAULT_DELAY_MIN = 1.0
DEFAULT_DELAY_MAX = 2.0
REQUEST_TIMEOUT = (10, 20)
MAX_HTML_BYTES = 5_000_000
USER_AGENT = "WebIntelligenceCrawler/1.0 (+public research; respects robots.txt)"
TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "trk",
    "spm",
    "pk_campaign",
    "pk_kwd",
    "yclid",
}
IGNORED_PATH_PARTS = {
    "login",
    "logout",
    "sign-in",
    "signin",
    "sign-up",
    "signup",
    "register",
    "account",
    "cart",
    "checkout",
    "basket",
    "auth",
    "session",
    "password",
}
BANNER_KEYWORDS = {
    "cookie",
    "consent",
    "gdpr",
    "privacy banner",
    "cookie banner",
    "newsletter",
    "subscribe",
    "popup",
    "modal",
    "interstitial",
    "promo",
}
BLOCK_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "iframe",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "dialog",
    "menu",
    "template",
}


@dataclass(frozen=True)
class CrawlItem:
    url: str
    depth: int
    parent_url: Optional[str]


class CrawlAbort(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Robots-aware public website crawler")
    parser.add_argument("url", help="Starting public HTTP/HTTPS URL")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN)
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX)
    return parser.parse_args()


def fail(message: str) -> int:
    print(f"crawler: {message}", file=sys.stderr)
    print("[]")
    return 1


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def dedupe_preserve_order(items: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        if not item:
            continue
        marker = item.lower()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def is_ip_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def is_public_ip(hostname: str) -> bool:
    ip_obj = ipaddress.ip_address(hostname)
    return not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def hostname_is_public(hostname: str) -> bool:
    try:
        addresses = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    resolved: List[str] = []
    for family, _, _, _, sockaddr in addresses:
        if family == socket.AF_INET:
            resolved.append(sockaddr[0])
        elif family == socket.AF_INET6:
            resolved.append(sockaddr[0])
    return bool(resolved) and all(is_public_ip(address) for address in resolved)


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith(TRACKING_PREFIXES) or lowered in TRACKING_KEYS:
            continue
        query_items.append((key, value))
    query_items.sort(key=lambda item: (item[0].lower(), item[1]))
    query = urlencode(query_items, doseq=True)

    netloc = hostname
    if parsed.port:
        default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
        if parsed.port != default_port:
            netloc = f"{hostname}:{parsed.port}"
    return urlunparse((scheme, netloc, path, "", query, ""))


def validate_public_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise CrawlAbort("URL must use http or https")
    if not parsed.hostname:
        raise CrawlAbort("URL must include a hostname")

    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise CrawlAbort("localhost targets are not allowed")
    if hostname.endswith((".local", ".internal", ".intranet", ".corp", ".lan")):
        raise CrawlAbort("internal hostnames are not allowed")

    if is_ip_address(hostname):
        if not is_public_ip(hostname):
            raise CrawlAbort("private, loopback, reserved, or link-local IPs are not allowed")
    elif not hostname_is_public(hostname):
        raise CrawlAbort("host resolves to a private or non-public address")

    return normalize_url(raw_url)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
    session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
    return session


def fetch_with_retries(session: requests.Session, url: str, attempts: int = 3) -> requests.Response:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except (requests.Timeout, requests.ConnectionError, requests.RequestException) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(8.0, (2 ** (attempt - 1)) + random.uniform(0.25, 0.75)))
    assert last_error is not None
    raise last_error


def robots_parser_for_origin(session: requests.Session, start_url: str) -> RobotFileParser:
    parsed = urlparse(start_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    response = fetch_with_retries(session, robots_url, attempts=2)

    parser = RobotFileParser()
    parser.set_url(robots_url)
    if response.status_code in {404, 410}:
        parser.parse(["User-agent: *", "Allow: /"])
        return parser
    if response.status_code != 200:
        raise CrawlAbort(f"robots.txt returned HTTP {response.status_code}")
    parser.parse((response.text or "").splitlines())
    return parser


def same_origin(url_a: str, url_b: str) -> bool:
    parsed_a = urlparse(url_a)
    parsed_b = urlparse(url_b)
    return parsed_a.scheme == parsed_b.scheme and parsed_a.netloc.lower() == parsed_b.netloc.lower()


def is_ignored_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(part in path for part in IGNORED_PATH_PARTS):
        return True
    return bool(re.search(r"\.(pdf|jpg|jpeg|png|gif|webp|svg|zip|rar|7z|mp4|mp3|avi|mov|wmv|docx?|xlsx?|pptx?)$", path))


def prune_boilerplate(soup: BeautifulSoup) -> None:
    for tag_name in BLOCK_TAGS:
        for element in soup.find_all(tag_name):
            element.decompose()
    for element in soup.find_all(True):
        attributes = []
        for attr_name in ("id", "class", "aria-label", "role", "data-testid", "data-cookiebanner", "data-consent"):
            attr_value = element.get(attr_name)
            if not attr_value:
                continue
            if isinstance(attr_value, list):
                attributes.extend(str(value).lower() for value in attr_value)
            else:
                attributes.append(str(attr_value).lower())
        if attributes and any(keyword in " ".join(attributes) for keyword in BANNER_KEYWORDS):
            element.decompose()


def canonical_from_html(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", attrs={"rel": re.compile(r"\bcanonical\b", re.I)})
    if canonical and canonical.get("href"):
        return normalize_url(urljoin(base_url, canonical["href"]))
    return None


def extract_metadata_and_text(html: str, url: str) -> Dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    prune_boilerplate(soup)

    title_tag = soup.find("title")
    title = normalize_text(title_tag.get_text(" ", strip=True)) if title_tag else ""

    meta_description = ""
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta and meta.get("content"):
        meta_description = normalize_text(meta["content"])

    text = ""
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=False,
                favor_precision=True,
                deduplicate=True,
                output_format="txt",
            )
            if extracted:
                text = normalize_text(extracted)
        except Exception:
            text = ""
    if not text:
        text = normalize_text(soup.get_text(" ", strip=True))

    headings = []
    for level in ("h1", "h2", "h3"):
        for element in soup.find_all(level):
            value = normalize_text(element.get_text(" ", strip=True))
            if value:
                headings.append(value)

    return {
        "title": title,
        "meta_description": meta_description,
        "canonical_url": canonical_from_html(html, url),
        "text": text,
        "headings": dedupe_preserve_order(headings),
    }


def extract_internal_links(html: str, base_url: str, root_origin: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: List[str] = []
    seen: Set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href:
            continue
        href = href.strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        candidate = normalize_url(urljoin(base_url, href))
        if not candidate.startswith(root_origin):
            continue
        if is_ignored_url(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        discovered.append(candidate)
    return discovered


def link_priority(url: str) -> int:
    path = urlparse(url).path.lower()
    for token, score in [
        ("/", 0),
        ("about", 5),
        ("company", 6),
        ("product", 7),
        ("service", 7),
        ("solution", 7),
        ("pricing", 8),
        ("plan", 8),
        ("feature", 9),
        ("docs", 10),
        ("documentation", 10),
        ("faq", 11),
        ("case", 12),
        ("customer", 12),
        ("contact", 13),
        ("blog", 20),
    ]:
        if token == "/" and path == "/":
            return score
        if token != "/" and token in path:
            return score
    return 50


def crawl(start_url: str, max_pages: int, max_depth: int, delay_min: float, delay_max: float) -> List[Dict[str, object]]:
    session = build_session()
    robots = robots_parser_for_origin(session, start_url)

    normalized_start = normalize_url(start_url)
    if not robots.can_fetch(USER_AGENT, normalized_start):
        raise CrawlAbort("robots.txt disallows the starting URL for this user agent")

    queue: Deque[CrawlItem] = deque([CrawlItem(url=normalized_start, depth=0, parent_url=None)])
    visited: Set[str] = {normalized_start}
    results: List[Dict[str, object]] = []
    root_origin = f"{urlparse(normalized_start).scheme}://{urlparse(normalized_start).netloc}"
    request_count = 0

    while queue and len(results) < max_pages:
        item = queue.popleft()
        if item.depth > max_depth:
            continue
        if not robots.can_fetch(USER_AGENT, item.url):
            continue
        if request_count > 0:
            time.sleep(random.uniform(delay_min, delay_max))
        request_count += 1

        try:
            response = fetch_with_retries(session, item.url, attempts=3)
        except Exception:
            continue

        if response.status_code >= 400:
            continue
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            continue
        if len(response.content) > MAX_HTML_BYTES:
            continue

        final_url = normalize_url(response.url)
        if not same_origin(final_url, normalized_start):
            continue
        if not robots.can_fetch(USER_AGENT, final_url):
            continue
        if is_ignored_url(final_url):
            continue

        html = response.text
        extracted = extract_metadata_and_text(html, final_url)
        results.append(
            {
                "url": item.url,
                "final_url": final_url,
                "depth": item.depth,
                "parent_url": item.parent_url,
                "status_code": response.status_code,
                "title": extracted["title"],
                "meta_description": extracted["meta_description"],
                "canonical_url": extracted["canonical_url"],
                "headings": extracted["headings"],
                "text": extracted["text"],
            }
        )

        if item.depth >= max_depth:
            continue

        discovered = extract_internal_links(html, final_url, root_origin)
        discovered.sort(key=link_priority)
        for link in discovered:
            if link in visited:
                continue
            if not robots.can_fetch(USER_AGENT, link):
                continue
            visited.add(link)
            queue.append(CrawlItem(url=link, depth=item.depth + 1, parent_url=final_url))
            if len(visited) >= max_pages * 10:
                break

    return results


def main() -> int:
    args = parse_args()
    if args.max_pages <= 0:
        return fail("max-pages must be greater than zero")
    if args.max_depth < 0:
        return fail("max-depth must be zero or greater")
    if args.delay_min < 0 or args.delay_max < 0 or args.delay_min > args.delay_max:
        return fail("delay-min and delay-max must be non-negative and delay-min must not exceed delay-max")

    try:
        start_url = validate_public_url(args.url)
        pages = crawl(start_url, args.max_pages, args.max_depth, args.delay_min, args.delay_max)
    except CrawlAbort as exc:
        return fail(str(exc))
    except Exception as exc:
        return fail(f"unexpected error: {exc}")

    json.dump(pages, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
