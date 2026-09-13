#!/usr/bin/env python3
"""Capture a real-web corpus once, so every agent replays identical bytes.

Why capture instead of auditing live sites six times over:

* **Politeness** - one fetch per page for the whole benchmark instead of one per
  agent. robots.txt is obeyed, requests to a host are serialised with a delay,
  and nothing but HTML is downloaded.
* **Fairness** - all agents see byte-identical input. A site that changes, rate
  limits, or A/B-tests between runs would otherwise silently score agents on
  different content.
* **Reproducibility** - the corpus is a directory you can re-run, diff and ship
  with the submission. Real-web results that cannot be re-run are anecdotes.

Storage mirrors the original URL paths, so root-relative links, robots.txt and
sitemap.xml all behave in replay exactly as they do live. Only the *host* part of
same-host absolute links is rewritten (to a root-relative path), which is what
keeps a replayed crawl from leaking back onto the live site.

Usage:
    python bench/runners/capture_corpus.py                       # curated list
    python bench/runners/capture_corpus.py --limit 60 --pages 4
    python bench/runners/capture_corpus.py --urls my_urls.txt --out bench/sites/real/custom
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_SITES = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "sites.json")
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "bench", "sites", "real", "corpus")

USER_AGENT = ("AIDiscoverabilityAuditBot/1.0 (+read-only research crawler; "
              "Adobe University Hackathon 2026 benchmark; obeys robots.txt)")
TIMEOUT = 15
MAX_BYTES = 3_000_000
PER_HOST_DELAY = 1.0

_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_locks_guard = threading.Lock()


def _host_gate(host: str):
    with _locks_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    return lock


def polite_get(session: requests.Session, url: str) -> dict:
    """One rate-limited GET. Never raises."""
    host = urlparse(url).netloc
    lock = _host_gate(host)
    with lock:
        wait = PER_HOST_DELAY - (time.monotonic() - _host_last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        entry = {"url": url, "status": None, "final_url": url, "content_type": "",
                 "bytes": b"", "error": None, "elapsed_ms": 0}
        start = time.monotonic()
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            entry["status"] = resp.status_code
            entry["final_url"] = resp.url
            entry["content_type"] = resp.headers.get("Content-Type", "")
            if "html" in entry["content_type"].lower() or not entry["content_type"]:
                chunks, total = [], 0
                for chunk in resp.iter_content(65536):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        entry["error"] = "truncated at size cap"
                        break
                entry["bytes"] = b"".join(chunks)
            resp.close()
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        _host_last[host] = time.monotonic()
    return entry


_UNSAFE_SEGMENT = re.compile(r'[<>:"|?*\\\x00-\x1f]')


def local_path_for(url: str) -> str:
    """Mirror the URL path on disk so root-relative links keep working.

    Segments are sanitised and length-capped because this has to survive Windows
    (reserved characters, 260-character path limit) as well as POSIX.
    """
    path = urlparse(url).path or "/"
    path = re.sub(r"/{2,}", "/", path)
    trailing = path.endswith("/")
    segments = [_UNSAFE_SEGMENT.sub("_", seg)[:60] for seg in path.strip("/").split("/") if seg]
    if not segments:
        return "index.html"
    joined = "/".join(segments[:8])
    if not trailing and re.search(r"\.(html?|xml|txt)$", segments[-1], re.I):
        return joined
    return joined + "/index.html"


_ANCHOR_RE = re.compile(rb"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def rewrite_same_host_links(raw: bytes, page_url: str, host: str) -> bytes:
    """Point same-host absolute links at the replay server (root-relative)."""
    def repl(match: re.Match) -> bytes:
        prefix, quote, target = match.group(1), match.group(2), match.group(3)
        try:
            href = target.decode("utf-8", errors="ignore").strip()
        except Exception:  # noqa: BLE001
            return match.group(0)
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return match.group(0)
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc.lower().lstrip("www.") != host.lower().lstrip("www."):
            return match.group(0)  # external link: leave it exactly as authored
        local = urlunparse(("", "", parsed.path or "/", "", parsed.query, parsed.fragment))
        return prefix + quote + local.encode("utf-8") + quote
    return _ANCHOR_RE.sub(repl, raw)


def capture_site(site: dict, out_root: str, max_pages: int) -> dict:
    slug, url = site["slug"], site["url"]
    record = {
        "slug": slug, "url": url, "category": site.get("category", ""),
        "why_hard": site.get("why_hard", ""), "captured_at": datetime.now(timezone.utc).isoformat(),
        "pages": [], "robots_status": None, "robots_disallows_entry": False,
        "status": "ok", "reason": "", "bytes_total": 0,
    }
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    site_dir = os.path.join(out_root, slug)

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc

    # robots.txt: stored for replay AND obeyed during capture
    robots = polite_get(session, origin + "/robots.txt")
    record["robots_status"] = robots["status"]
    robots_text = ""
    if robots["status"] == 200 and robots["bytes"]:
        robots_text = robots["bytes"].decode("utf-8", errors="replace")
        parser = RobotFileParser()
        parser.parse(robots_text.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            record["status"] = "skipped"
            record["reason"] = "robots.txt disallows the entry URL for our user agent"
            record["robots_disallows_entry"] = True
            return record
        rp = parser
    else:
        rp = None

    queue, seen = [url], {url}
    saved: list[dict] = []
    while queue and len(saved) < max_pages:
        current = queue.pop(0)
        if rp is not None and not rp.can_fetch(USER_AGENT, current):
            continue
        page = polite_get(session, current)
        if page["status"] != 200 or not page["bytes"]:
            if current == url:
                record["status"] = "failed"
                record["reason"] = page["error"] or f"entry returned HTTP {page['status']}"
                return record
            continue

        rel = local_path_for(page["final_url"])
        body = rewrite_same_host_links(page["bytes"], page["final_url"], host)
        target = os.path.join(site_dir, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(body)
        saved.append({
            "requested": current, "final_url": page["final_url"], "path": "/" + rel.replace("index.html", ""),
            "file": rel, "status": page["status"], "content_type": page["content_type"],
            "bytes": len(body), "elapsed_ms": page["elapsed_ms"], "is_entry": current == url,
        })
        record["bytes_total"] += len(body)

        for match in _ANCHOR_RE.finditer(body):
            href = match.group(3).decode("utf-8", errors="ignore").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            absolute = urljoin(page["final_url"], href).split("#")[0]
            target_parsed = urlparse(absolute)
            if target_parsed.netloc and target_parsed.netloc.lower().lstrip("www.") != host.lower().lstrip("www."):
                continue
            absolute = urljoin(origin, urlparse(absolute).path)
            if absolute in seen or re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|css|js|ico|mp4|webp)$", absolute, re.I):
                continue
            seen.add(absolute)
            queue.append(absolute)

    if not saved:
        record["status"] = "failed"
        record["reason"] = record["reason"] or "no HTML pages captured"
        return record

    if robots_text:
        with open(os.path.join(site_dir, "robots.txt"), "w", encoding="utf-8") as fh:
            fh.write(robots_text)

    record["pages"] = saved
    with open(os.path.join(site_dir, "_capture.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record


def main() -> None:
    global PER_HOST_DELAY
    ap = argparse.ArgumentParser(description="Capture a real-web corpus for the benchmark")
    ap.add_argument("--sites", default=DEFAULT_SITES, help="curated sites.json")
    ap.add_argument("--urls", help="plain-text file of URLs, one per line (overrides --sites)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="corpus output directory")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of sites")
    ap.add_argument("--pages", type=int, default=4, help="max pages captured per site")
    ap.add_argument("--workers", type=int, default=6, help="sites fetched in parallel (one host each)")
    ap.add_argument("--delay", type=float, default=PER_HOST_DELAY,
                    help="seconds between requests to the same host (lower only for local testing)")
    args = ap.parse_args()

    if args.urls:
        with open(args.urls, encoding="utf-8") as fh:
            urls = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
        sites = [{"slug": re.sub(r"[^a-z0-9]+", "-", urlparse(u).netloc.lower()).strip("-"),
                  "url": u, "category": "custom", "why_hard": ""} for u in urls]
        meta = {"selection_method": f"custom URL list: {args.urls}"}
    else:
        with open(args.sites, encoding="utf-8") as fh:
            data = json.load(fh)
        sites = data["sites"]
        meta = {k: v for k, v in data.items() if k != "sites"}

    if args.limit:
        sites = sites[:args.limit]

    PER_HOST_DELAY = args.delay

    os.makedirs(args.out, exist_ok=True)
    print(f"Capturing {len(sites)} sites, up to {args.pages} pages each, {args.workers} in parallel")
    print(f"User-Agent: {USER_AGENT}\n")

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(capture_site, site, args.out, args.pages): site for site in sites}
        for done, future in enumerate(as_completed(futures), start=1):
            site = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001
                record = {"slug": site["slug"], "url": site["url"], "status": "failed",
                          "reason": f"{type(exc).__name__}: {exc}", "pages": []}
            records.append(record)
            mark = {"ok": "ok", "skipped": "skip", "failed": "FAIL"}.get(record["status"], "?")
            print(f"  [{done:>3}/{len(sites)}] {mark:<4} {record['slug']:<26} "
                  f"pages={len(record.get('pages', []))} {record.get('reason', '')[:60]}")

    ok = [r for r in records if r["status"] == "ok"]
    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "user_agent": USER_AGENT,
        "max_pages_per_site": args.pages,
        "requested": len(sites),
        "captured": len(ok),
        "skipped_by_robots": len([r for r in records if r["status"] == "skipped"]),
        "failed": len([r for r in records if r["status"] == "failed"]),
        "total_pages": sum(len(r.get("pages", [])) for r in ok),
        "meta": meta,
        "sites": sorted(records, key=lambda r: r["slug"]),
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"\nCaptured {manifest['captured']}/{manifest['requested']} sites, "
          f"{manifest['total_pages']} pages "
          f"({manifest['skipped_by_robots']} robots-skipped, {manifest['failed']} failed)")
    print(f"Corpus: {args.out}")


if __name__ == "__main__":
    main()
