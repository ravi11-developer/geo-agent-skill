#!/usr/bin/env python3
"""
Audit Orchestrator — entrypoint skill.

1. Fetches the homepage, discovers internal links, and samples representative pages.
2. Passes the page list (as JSON via stdin) to each specialist script.
3. Collects findings, de-duplicates by root cause, tags type (defect/recommendation),
   sorts by severity, and emits the final JSON report.

Specialist skills:
  - crawl-render-audit      : reachability, robots, redirects, canonical, JS content,
                               noindex/nofollow, HTTPS, page size, mixed content
  - semantic-entity-audit   : structured data, entity identity, sameAs, FAQ/speakable schema,
                               quotability, E-E-A-T signals, duplicate titles/descs
  - freshness-corroboration : copyright, Last-Modified, date meta, fact consistency,
                               address consistency, email readability (Appendix F)
  - engagement-audit        : headings (hierarchy, multiple H1), navigation, breadcrumbs,
                               broken links, CTAs, viewport, word count, lang, hreflang,
                               FAQ content patterns, form accessibility
  - offsite-discoverability-audit : off-site signals (Appendix B, D, E) — sameAs anchoring,
                               brand disambiguation, content quotability, Org schema quality,
                               press/media mentions, direct-answer content patterns

Dependencies: Python standard library only.
Usage:
    python orchestrator.py https://example.com
"""
import sys, subprocess, json, re, os, urllib.request, urllib.parse, urllib.error, urllib.robotparser
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page


# ===========================================================================
# Page discovery helpers
# ===========================================================================

_CAT_PATTERNS = re.compile(
    r"/(category|categories|collection|collections|shop|store|browse|catalog|department|section)/",
    re.I,
)
_PRODUCT_PATTERNS = re.compile(
    r"/(product|item|listing|detail|p|dp|goods)/", re.I
)
_INFO_PATTERNS = re.compile(
    r"/(about|contact|faq|help|support|team|careers|press|blog|article|news|story|post|privacy|terms|legal)/",
    re.I,
)


def _classify_url(url):
    path = urllib.parse.urlsplit(url).path.lower()
    if _CAT_PATTERNS.search(path):
        return "category"
    if _PRODUCT_PATTERNS.search(path):
        return "product"
    if _INFO_PATTERNS.search(path):
        return "info"
    return "other"


def discover_pages(homepage_url, homepage_html, budget=15):
    """Find internal links on *homepage_html*, classify them, and return a
    bounded sample dict, respecting robots.txt."""
    parsed = parse_page(homepage_html, homepage_url)
    seen = {homepage_url}
    buckets = {"homepage": [homepage_url], "category": [], "product": [], "info": [], "other": []}
    limits = {"category": 3, "product": 4, "info": 3, "other": 3}

    rp = None
    try:
        robots_url = urllib.parse.urljoin(homepage_url, "/robots.txt")
        robots_req = fetch(robots_url, timeout=5)
        if not robots_req.get("error") and robots_req["status"] == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.parse(robots_req["body"].decode("utf-8", "replace").splitlines())
    except Exception as e:
        print(f"[orchestrator] Error fetching robots.txt: {e}", file=sys.stderr)

    for link in parsed.internal_links:
        href = link["href"].split("#")[0].split("?")[0]
        if href in seen or not href.startswith("http"):
            continue
        if rp and not rp.can_fetch("*", href):
            seen.add(href)
            continue
        seen.add(href)
        kind = _classify_url(href)
        if len(buckets[kind]) < limits.get(kind, 3):
            buckets[kind].append(href)
        if sum(len(v) for v in buckets.values()) >= budget:
            break

    return buckets


# ===========================================================================
# Orchestrator logic
# ===========================================================================

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def run_skill_script(script_path, pages_json):
    """Run a specialist script, piping the page list as JSON via stdin."""
    if not os.path.exists(script_path):
        print(f"Warning: {script_path} not found, skipping.", file=sys.stderr)
        return []

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            input=pages_json,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print(f"Error from {script_path}: {result.stderr.strip()}", file=sys.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else []
    except subprocess.TimeoutExpired:
        print(f"Timeout running {script_path}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"JSON parse error from {script_path}: {e}", file=sys.stderr)
        return []


def deduplicate(findings):
    """Merge findings with the same title (same root cause) into one finding
    with combined affected_urls. Keeps the first occurrence's evidence and
    appends a scope note."""
    by_title = {}
    order = []
    for f in findings:
        title = f["title"]
        if title not in by_title:
            by_title[title] = {**f, "affected_urls": list(f.get("affected_urls", []))}
            order.append(title)
        else:
            existing = by_title[title]
            for u in f.get("affected_urls", []):
                if u not in existing["affected_urls"]:
                    existing["affected_urls"].append(u)
    deduped = []
    for title in order:
        merged = by_title[title]
        n = len(merged["affected_urls"])
        if n > 1:
            merged["evidence"] = (
                f"Observed on {n} sampled pages. "
                + merged["evidence"]
            )
        deduped.append(merged)
    return deduped


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: orchestrator.py URL")

    target_url = sys.argv[1]

    # Normalise URL
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    p = urllib.parse.urlsplit(target_url)
    site = p.netloc

    # ---- Step 1: Discover pages ----
    print(f"[orchestrator] Fetching homepage: {target_url}", file=sys.stderr)
    home = fetch(target_url)
    if home.get("error") or home["status"] >= 400:
        print(json.dumps({
            "site": site,
            "audited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "summary": {"total_findings": 1, "critical": 1, "high": 0, "medium": 0, "low": 0},
            "findings": [{
                "id": "ORCH-001", "title": "Cannot reach target site", "severity": "critical",
                "confidence": "high", "type": "defect",
                "evidence": "Failed to fetch {}: {}".format(
                    target_url, home.get("error", "HTTP {}".format(home["status"]))),
                "affected_urls": [target_url],
                "suggested_action": {"summary": "Ensure the site is reachable.", "priority": "critical"},
            }],
        }, indent=2))
        return

    homepage_html = home["body"].decode("utf-8", "replace")
    buckets = discover_pages(target_url, homepage_html, budget=15)

    # Flatten all sampled URLs
    all_pages = []
    for kind in ("homepage", "category", "product", "info", "other"):
        all_pages.extend(buckets.get(kind, []))
    all_pages = list(dict.fromkeys(all_pages))  # dedupe, preserve order

    sample_summary = {k: len(v) for k, v in buckets.items() if v}
    print(f"[orchestrator] Sampled {len(all_pages)} pages: {sample_summary}", file=sys.stderr)

    pages_json = json.dumps(all_pages)

    # ---- Step 2: Run specialist scripts ----
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    scripts = [
        os.path.join(base_dir, "crawl-render-audit", "scripts", "crawl_audit.py"),
        os.path.join(base_dir, "semantic-entity-audit", "scripts", "semantic_audit.py"),
        os.path.join(base_dir, "freshness-corroboration", "scripts", "freshness_audit.py"),
        os.path.join(base_dir, "engagement-audit", "scripts", "engagement_audit.py"),
        os.path.join(base_dir, "offsite-discoverability-audit", "scripts", "offsite_audit.py"),
    ]

    all_findings = []
    for script in scripts:
        name = os.path.basename(script)
        print(f"[orchestrator] Running {name}…", file=sys.stderr)
        findings = run_skill_script(script, pages_json)
        print(f"[orchestrator] {name} returned {len(findings)} finding(s).", file=sys.stderr)
        all_findings.extend(findings)

    # ---- Step 3: De-duplicate ----
    all_findings = deduplicate(all_findings)

    # ---- Step 4: Sort by severity ----
    all_findings.sort(key=lambda f: _SEV_ORDER.get(f.get("severity", "low"), 9))

    # ---- Step 5: Re-assign sequential IDs ----
    # for i, f in enumerate(all_findings, start=1):
    #     f["id"] = "F-{:03d}".format(i)

    # ---- Step 6: Compute summary ----
    summary = {"total_findings": len(all_findings), "critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        sev = f.get("severity", "low")
        if sev in summary:
            summary[sev] += 1

    # ---- Step 7: Emit report ----
    report = {
        "site": site,
        "audited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pages_sampled": len(all_pages),
        "sample_breakdown": sample_summary,
        "summary": summary,
        "findings": all_findings,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
