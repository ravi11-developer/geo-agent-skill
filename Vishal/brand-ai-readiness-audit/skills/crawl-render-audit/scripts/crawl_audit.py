#!/usr/bin/env python3
"""
Crawl & Render Audit — specialist script.

Accepts a JSON page-list on stdin (from orchestrator) OR a single URL argument.
Checks robots.txt, sitemaps, HTTP status, redirect chains, canonical tags,
raw HTML content visibility, content locked in images, hidden content,
iframe accessibility, sitemap coverage, orphan page risk, noindex/nofollow,
HTTPS enforcement, page size, and mixed content.

Dependencies: Python standard library only.
"""
import json, sys, os, re, urllib.request, urllib.parse, urllib.error, urllib.robotparser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page, make_finding


# ===========================================================================
# Robots helpers
# ===========================================================================

def check_robots(base_url, timeout=10):
    """Fetch and parse robots.txt. Returns (RobotFileParser | None, findings_list)."""
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    findings = []
    try:
        r = fetch(robots_url, timeout=timeout)
        if r["status"] == 200:
            txt = r["body"].decode("utf-8", "replace")
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.parse(txt.splitlines())
            return rp, findings, txt
        else:
            return None, findings, ""
    except Exception:
        return None, findings, ""


# ===========================================================================
# Site-level checks
# ===========================================================================

def audit_robots(base_url):
    """Check robots.txt and sitemaps.
    Returns (findings_list, sitemap_page_urls_set, robots_text)."""
    findings = []
    sitemap_page_urls = set()
    p = urllib.parse.urlsplit(base_url)
    base = "{}://{}".format(p.scheme, p.netloc)

    rp, _, robots_text = check_robots(base)
    if rp is not None:
        if not rp.can_fetch("*", base_url):
            findings.append(make_finding(
                "CRAWL-001", "Robots.txt blocks general crawlers", "critical",
                "robots.txt at {}/robots.txt disallows fetching {} for user-agent *. "
                "AI crawlers using a generic user-agent will be unable to index this content.".format(base, base_url),
                "Review robots.txt Disallow rules and permit crawling of public, valuable paths.",
                affected_urls=[base_url],
            ))
        # Check if AI-specific crawlers are explicitly blocked
        ai_agents = ["GPTBot", "ChatGPT-User", "Google-Extended", "CCBot", "anthropic-ai"]
        blocked_ai = []
        for agent in ai_agents:
            if not rp.can_fetch(agent, base_url):
                # Verify it's actually mentioned in robots.txt (not just a default deny)
                if agent.lower() in robots_text.lower():
                    blocked_ai.append(agent)
        if blocked_ai:
            findings.append(make_finding(
                "CRAWL-001b", "AI-specific crawlers explicitly blocked", "high",
                "robots.txt explicitly blocks these AI crawlers: {}. "
                "These bots are used by major AI assistants to discover and index content. "
                "Blocking them prevents the brand from appearing in AI-generated answers.".format(
                    ", ".join(blocked_ai)),
                "Consider allowing AI crawlers access to public content. If blocking is "
                "intentional, understand that the brand will not appear in those AI systems' answers.",
                affected_urls=[base + "/robots.txt"],
            ))
        sitemaps = rp.site_maps() or []
    else:
        sitemaps = []

    if not sitemaps:
        sitemaps = [urllib.parse.urljoin(base, "/sitemap.xml")]

    sitemap_found = False
    for sm in sitemaps[:3]:
        try:
            r = fetch(sm, timeout=8)
            if r["status"] == 200 and ("xml" in r.get("content_type", "") or r["body"][:100].strip().startswith(b"<")):
                sitemap_found = True
                body_text = r["body"].decode("utf-8", "replace")
                locs = re.findall(r'<loc>\s*(.*?)\s*</loc>', body_text, re.I)
                sitemap_page_urls.update(locs)
                break
        except Exception:
            continue

    if not sitemap_found:
        findings.append(make_finding(
            "CRAWL-002", "No accessible XML sitemap", "medium",
            "No valid XML sitemap was found via robots.txt Sitemap directives or "
            "the conventional /sitemap.xml path. Without a sitemap, crawlers must "
            "rely solely on link discovery, which may miss important pages.",
            "Publish an XML sitemap listing all public pages and reference it in robots.txt.",
            confidence="high", finding_type="defect",
        ))

    return findings, sitemap_page_urls


def check_sitemap_coverage(sitemap_page_urls, sampled_pages):
    """Check if sampled pages appear in the XML sitemap."""
    findings = []
    if not sitemap_page_urls or len(sampled_pages) < 2:
        return findings

    norm_sitemap = {u.rstrip("/") for u in sitemap_page_urls}
    missing = [page for page in sampled_pages if page.rstrip("/") not in norm_sitemap]

    if missing and len(missing) > len(sampled_pages) * 0.5:
        findings.append(make_finding(
            "CRAWL-010", "Important pages missing from XML sitemap", "medium",
            "{} of {} sampled pages are not listed in the XML sitemap. "
            "Missing: {}. Pages not in the sitemap may be discovered more slowly "
            "or missed entirely by crawlers.".format(
                len(missing), len(sampled_pages), ", ".join(missing[:5])),
            "Add all important public pages to the XML sitemap.",
            affected_urls=missing[:10], confidence="medium",
        ))

    return findings


def check_orphan_pages(sampled_pages, page_outlinks):
    """Check if any sampled pages have zero inbound links from other sampled pages."""
    findings = []
    if len(sampled_pages) < 3:
        return findings

    inbound = {page: set() for page in sampled_pages}
    for source_page, links in page_outlinks.items():
        for link_url in links:
            normalised = link_url.rstrip("/").split("?")[0].split("#")[0]
            for target in sampled_pages:
                if target.rstrip("/") == normalised and target != source_page:
                    inbound[target].add(source_page)

    homepage = sampled_pages[0]
    orphans = [page for page, sources in inbound.items()
               if len(sources) == 0 and page != homepage]

    if orphans:
        findings.append(make_finding(
            "CRAWL-013", "Potential orphan pages detected", "medium",
            "{} sampled page(s) have no inbound internal links from other sampled "
            "pages: {}. Orphan pages are harder for crawlers to discover through "
            "link following.".format(len(orphans), ", ".join(orphans[:5])),
            "Ensure important pages are linked from other relevant pages on the site.",
            affected_urls=orphans[:10], confidence="low",
            finding_type="recommendation",
        ))

    return findings


def check_https(base_url):
    """Check if the site uses HTTPS."""
    findings = []
    if base_url.startswith("http://"):
        findings.append(make_finding(
            "CRAWL-016", "Site does not use HTTPS", "high",
            "The target URL {} uses HTTP instead of HTTPS. AI systems and search "
            "engines strongly prefer HTTPS sites. Lack of HTTPS reduces trust signals "
            "and may cause content to be deprioritised or flagged as insecure.".format(base_url),
            "Migrate the site to HTTPS and set up HTTP→HTTPS redirects. Obtain and "
            "install a valid TLS certificate.",
            affected_urls=[base_url],
        ))
    return findings


# ===========================================================================
# Per-page checks
# ===========================================================================

def audit_page(url):
    """Audit a single page for crawl/render issues.
    Returns (findings, parsed_page_or_None, list_of_internal_link_urls)."""
    findings = []
    r = fetch(url)

    if r.get("error") or r["status"] == 0:
        findings.append(make_finding(
            "CRAWL-003", "Page unreachable", "critical",
            "Failed to fetch {}: {}.".format(url, r.get("error", "unknown error")),
            "Investigate server connectivity; ensure the page returns a 200 OK.",
            affected_urls=[url],
        ))
        return findings, None, []

    if r["status"] >= 400:
        findings.append(make_finding(
            "CRAWL-004", "HTTP {} error on page".format(r["status"]), "high",
            "{} returned HTTP {}. This page is invisible to crawlers.".format(url, r["status"]),
            "Fix the HTTP error so the page returns a 200 OK.",
            affected_urls=[url],
        ))
        return findings, None, []

    # Redirect chain
    if len(r["redirect_chain"]) > 2:
        chain_desc = " → ".join([h["url"] for h in r["redirect_chain"]] + [r["url"]])
        findings.append(make_finding(
            "CRAWL-005", "Long redirect chain", "medium",
            "{} passes through {} redirects before reaching {}. Long chains slow "
            "discovery and may be truncated by some crawlers. Chain: {}".format(
                url, len(r["redirect_chain"]), r["url"], chain_desc),
            "Reduce the redirect chain to at most one hop.",
            affected_urls=[url], confidence="high",
        ))

    html = r["body"].decode("utf-8", "replace")
    parsed = parse_page(html, r["url"])
    outlinks = [l["href"] for l in parsed.internal_links]

    # Canonical check
    if parsed.canonical:
        canon_abs = urllib.parse.urljoin(r["url"], parsed.canonical)
        if canon_abs != r["url"]:
            findings.append(make_finding(
                "CRAWL-006", "Canonical points to a different URL", "medium",
                "{} has <link rel='canonical' href='{}'> which resolves to {}, "
                "different from the fetched URL {}. Crawlers may skip this page "
                "in favour of the canonical target.".format(
                    url, parsed.canonical, canon_abs, r["url"]),
                "Ensure canonical tags are self-referencing unless intentional consolidation.",
                affected_urls=[url], confidence="high",
            ))

    # Low link count — potential JS-rendered content
    if len(parsed.internal_links) < 3 and len(html) > 2000:
        findings.append(make_finding(
            "CRAWL-007", "Very few crawlable links in raw HTML", "high",
            "{} contains {} internal links in raw HTML despite having {} bytes of "
            "content. Navigation or content may be rendered only via client-side "
            "JavaScript, making it invisible to crawlers that do not execute JS.".format(
                url, len(parsed.internal_links), len(html)),
            "Implement server-side rendering (SSR) or embed critical navigation links "
            "in the initial HTML response.",
            affected_urls=[url], confidence="high",
        ))

    # Thin text content
    visible = parsed.visible_text.strip()
    if len(visible) < 200 and len(html) > 3000:
        findings.append(make_finding(
            "CRAWL-008", "Thin visible text relative to page size", "high",
            "{} has only ~{} characters of visible text in {} bytes of HTML. "
            "Important content may be locked behind JavaScript rendering, "
            "images, or interactive widgets.".format(url, len(visible), len(html)),
            "Ensure core factual content (product details, descriptions, prices, "
            "service info) is present in server-rendered HTML text.",
            affected_urls=[url], confidence="medium",
        ))

    # Images without alt text
    images_no_alt = [img for img in parsed.images if not img["alt"].strip()]
    if len(images_no_alt) > 3:
        findings.append(make_finding(
            "CRAWL-009", "Multiple images missing alt text", "medium",
            "{} contains {} images without alt attributes. If these images carry "
            "important information (text in images, infographics, product photos), "
            "that content is invisible to AI crawlers.".format(url, len(images_no_alt)),
            "Add descriptive alt text to images that convey meaningful information. "
            "Decorative images can use alt=''.",
            affected_urls=[url], confidence="medium",
        ))

    # Hidden content
    hidden_text_len = len(parsed.hidden_text.strip())
    if hidden_text_len > 300:
        total_hidden = (parsed.hidden_elements + parsed.display_none_elements
                        + parsed.aria_hidden_elements)
        parts = []
        if parsed.hidden_elements:
            parts.append("{} with 'hidden' attribute".format(parsed.hidden_elements))
        if parsed.display_none_elements:
            parts.append("{} with 'display:none'".format(parsed.display_none_elements))
        if parsed.aria_hidden_elements:
            parts.append("{} with 'aria-hidden=true'".format(parsed.aria_hidden_elements))
        snippet = " ".join(parsed.hidden_text.split())
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        findings.append(make_finding(
            "CRAWL-011", "Significant hidden text content in raw HTML", "medium",
            "{} contains ~{} characters of text hidden inside elements ({}). "
            "If important factual information is inside these elements, it may be "
            "inaccessible to crawlers and AI retrieval systems that process only "
            "visible content. Sample hidden text: '{}'".format(
                url, hidden_text_len, ", ".join(parts), snippet),
            "Ensure important factual content is not hidden behind accordions, "
            "tabs, or collapsed sections by default. Use progressive disclosure "
            "only for supplementary detail.",
            affected_urls=[url], confidence="medium",
        ))

    # Iframe content
    if parsed.iframes > 0:
        findings.append(make_finding(
            "CRAWL-012", "Content embedded in iframes", "medium",
            "{} contains {} <iframe> element(s). Content inside iframes is loaded "
            "from a separate URL and is typically invisible to crawlers processing "
            "the parent page's HTML.".format(url, parsed.iframes),
            "Where possible, embed important content directly in the page HTML "
            "rather than inside iframes.",
            affected_urls=[url], confidence="medium",
        ))

    # Lazy images without noscript fallback
    if parsed.lazy_images > 3 and not parsed.has_noscript:
        findings.append(make_finding(
            "CRAWL-012b", "Lazy-loaded images without noscript fallback", "low",
            "{} has {} lazy-loaded images (loading='lazy') but no <noscript> "
            "fallback. Crawlers that do not execute JavaScript may not load "
            "these images.".format(url, parsed.lazy_images),
            "Provide <noscript> fallbacks for lazy-loaded images that carry "
            "important visual information.",
            affected_urls=[url], confidence="low", finding_type="recommendation",
        ))

    # ---- NEW: CRAWL-014 — noindex meta tag / header ----
    x_robots = r["headers"].get("x-robots-tag", "").lower()
    if (parsed.robots_meta and "noindex" in parsed.robots_meta) or "noindex" in x_robots:
        evidence_parts = []
        if parsed.robots_meta and "noindex" in parsed.robots_meta:
            evidence_parts.append("<meta name='robots' content='{}'>".format(parsed.robots_meta))
        if "noindex" in x_robots:
            evidence_parts.append("X-Robots-Tag: {}".format(x_robots))
            
        findings.append(make_finding(
            "CRAWL-014", "Page has noindex directive — invisible to search and AI", "critical",
            "{} contains {} which includes 'noindex'. "
            "This directive tells search engines and AI crawlers to NOT index this page. "
            "The page will be excluded from search results and AI training/retrieval.".format(
                url, " and ".join(evidence_parts)),
            "Remove the noindex directive if this page should be discoverable. "
            "If noindex is intentional, verify that no important public content is "
            "locked behind it.",
            affected_urls=[url],
        ))

    # ---- NEW: CRAWL-015 — nofollow meta tag / header ----
    if (parsed.robots_meta and "nofollow" in parsed.robots_meta) or "nofollow" in x_robots:
        evidence_parts = []
        if parsed.robots_meta and "nofollow" in parsed.robots_meta:
            evidence_parts.append("<meta name='robots' content='{}'>".format(parsed.robots_meta))
        if "nofollow" in x_robots:
            evidence_parts.append("X-Robots-Tag: {}".format(x_robots))
            
        findings.append(make_finding(
            "CRAWL-015", "Page has nofollow directive — link equity blocked", "medium",
            "{} contains {} which includes 'nofollow'. "
            "This prevents crawlers from following outbound links on this page, "
            "reducing the discoverability of linked pages.".format(
                url, " and ".join(evidence_parts)),
            "Remove the nofollow directive unless there is a specific reason to "
            "prevent crawlers from following links (e.g., user-generated content).",
            affected_urls=[url], confidence="high",
        ))

    # ---- NEW: CRAWL-017 — Excessive page size ----
    page_bytes = len(r["body"])
    if page_bytes > 2_000_000:
        findings.append(make_finding(
            "CRAWL-017", "Excessively large page", "medium",
            "{} is {:.1f} MB in raw size. Very large pages may timeout during "
            "AI crawling, get truncated, or be deprioritised. Crawlers typically "
            "read only the first 1-2 MB.".format(url, page_bytes / 1_000_000),
            "Reduce page size by lazy-loading non-essential content, removing "
            "inline data URIs, and optimising HTML structure.",
            affected_urls=[url], confidence="medium",
        ))

    # ---- NEW: CRAWL-018 — Mixed content on HTTPS ----
    if url.startswith("https://"):
        http_resources = []
        for img in parsed.images:
            if img["src"].startswith("http://"):
                http_resources.append(img["src"])
        # Check link hrefs for http:// stylesheet/script references
        for link in parsed.links:
            if link["href"].startswith("http://") and not link["is_internal"]:
                pass  # external links are OK
        if len(http_resources) > 2:
            findings.append(make_finding(
                "CRAWL-018", "Mixed content — HTTP resources on HTTPS page", "medium",
                "{} loads {} resources over insecure HTTP (e.g., {}). Mixed content "
                "triggers browser warnings and reduces trust signals that AI systems "
                "use to assess site quality.".format(
                    url, len(http_resources), ", ".join(http_resources[:3])),
                "Update all resource URLs to use HTTPS.",
                affected_urls=[url], confidence="high",
            ))

    return findings, parsed, outlinks


# ===========================================================================
# Main
# ===========================================================================

def main():
    if not sys.stdin.isatty():
        pages = json.load(sys.stdin)
    elif len(sys.argv) == 2:
        pages = [sys.argv[1]]
    else:
        raise SystemExit("usage: crawl_audit.py URL  OR  pipe JSON page list to stdin")

    all_findings = []

    # Robots + sitemap check (once, based on first URL)
    sitemap_page_urls = set()
    if pages:
        robot_findings, sitemap_page_urls = audit_robots(pages[0])
        all_findings.extend(robot_findings)

        # HTTPS check (site-level)
        all_findings.extend(check_https(pages[0]))

    # Sitemap coverage check
    all_findings.extend(check_sitemap_coverage(sitemap_page_urls, pages))

    # Per-page checks — also collect outlinks for orphan detection
    page_outlinks = {}
    for url in pages:
        page_findings, _, outlinks = audit_page(url)
        all_findings.extend(page_findings)
        page_outlinks[url] = outlinks

    # Orphan page risk
    all_findings.extend(check_orphan_pages(pages, page_outlinks))

    print(json.dumps(all_findings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
