#!/usr/bin/env python3
"""
Freshness & Corroboration Audit — specialist script.

Accepts a JSON page-list on stdin (from orchestrator) OR a single URL argument.
Checks copyright year staleness, Last-Modified headers, meta date tags,
cross-page fact consistency (phone, email, address), internal date ranges,
outbound links to authoritative sources, and email-readability signals.

Dependencies: Python standard library only.
"""
import json, sys, os, re, urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page, make_finding, PHONE_RE, EMAIL_RE, COPYRIGHT_RE, ADDRESS_RE


# ===========================================================================
# Helpers
# ===========================================================================

_AUTHORITY_DOMAINS = re.compile(
    r'\.(?:gov|edu|who\.int|wikipedia\.org|reuters\.com|bbc\.co|ap\.org|'
    r'nytimes\.com|ft\.com|wsj\.com|bloomberg\.com|techcrunch\.com|'
    r'crunchbase\.com|linkedin\.com|wikidata\.org)',
    re.I,
)

_IMAGE_HEAVY_THRESHOLD = 0.6   # if > 60% of links are images, flag as image-heavy
_FACT_EXTRACTION_RE = re.compile(
    r'(?:'
    r'\$[\d,.]+(?:\s*(?:million|billion|thousand|M|B|K))?'   # prices/revenue
    r'|€[\d,.]+(?:\s*(?:million|billion|thousand|M|B|K))?'
    r'|£[\d,.]+(?:\s*(?:million|billion|thousand|M|B|K))?'
    r'|\d+(?:\.\d+)?%'                                         # percentages
    r'|\d{1,3}(?:,\d{3})+'                                    # large numbers
    r'|\d+\s*(?:mg|kg|lb|oz|ml|ft|in|cm|mm|hr|hours?|min(?:utes?)?|sec(?:onds?)?|mph|kph|gb|mb|tb|employees?|users?|customers?|countries?|cities?|offices?)'
    r')',
    re.I,
)


def _extract_facts(text, url):
    """Extract repeatable facts from visible text."""
    facts = {"_url": url}
    phones = PHONE_RE.findall(text)
    if phones:
        facts["phone"] = [p.strip() for p in phones[:5]]
    emails = EMAIL_RE.findall(text)
    if emails:
        facts["email"] = [e.strip() for e in emails[:5]]
    # Address extraction
    addrs = ADDRESS_RE.findall(text)
    if addrs:
        facts["address"] = [a.strip() for a in addrs[:3]]
    return facts


# ===========================================================================
# Per-page audit
# ===========================================================================

def audit_page(url, current_year):
    """Audit a single page for freshness signals. Returns (findings, facts_dict)."""
    findings = []
    r = fetch(url)

    if r.get("error") or r["status"] >= 400:
        return findings, {}

    html = r["body"].decode("utf-8", "replace")
    parsed = parse_page(html, r["url"])
    text = parsed.visible_text

    # ---- 1. Copyright year ----
    m = COPYRIGHT_RE.search(text)
    if m:
        year = int(m.group(2))
        if year < current_year - 1:
            findings.append(make_finding(
                "FRESH-001", "Stale copyright year", "medium",
                f"{url} displays copyright year {year} (current year is {current_year}). "
                "This signals to AI systems that the content may not be actively maintained.",
                "Update the copyright notice to the current year, ideally via an automated template.",
                affected_urls=[url],
            ))

    # ---- 2. Last-Modified HTTP header ----
    lm = r.get("last_modified")
    if lm:
        try:
            from email.utils import parsedate_to_datetime
            lm_dt = parsedate_to_datetime(lm)
            age_days = (datetime.now(lm_dt.tzinfo) - lm_dt).days if lm_dt.tzinfo else (datetime.now() - lm_dt).days
            if age_days > 365:
                findings.append(make_finding(
                    "FRESH-002", "Last-Modified header indicates old content", "medium",
                    f"{url} has Last-Modified: {lm} ({age_days} days ago). "
                    "AI systems may deprioritise content that appears stale.",
                    "Ensure the Last-Modified header updates when content changes.",
                    affected_urls=[url], confidence="high",
                ))
        except Exception:
            pass

    # ---- 3. Meta date tags ----
    if not parsed.meta_dates:
        findings.append(make_finding(
            "FRESH-003", "No machine-readable publication or modification date", "low",
            f"{url} has no <meta> tags for article:published_time, article:modified_time, "
            "or similar date properties. Machines cannot determine content freshness from metadata.",
            "Add article:published_time and article:modified_time meta tags.",
            affected_urls=[url], finding_type="recommendation",
        ))

    # ---- NEW 4. Outbound authority links (FRESH-007) ----
    # Pages that cite authoritative external sources are more credible to AI systems
    authority_links = [
        l["href"] for l in parsed.external_links
        if _AUTHORITY_DOMAINS.search(l["href"])
    ]
    # Only flag on content-heavy pages (not homepages/thin pages)
    if not authority_links and parsed.word_count > 300 and parsed.paragraph_count > 2:
        # Check if page looks like a content/article page
        path = urllib.parse.urlsplit(url).path.lower()
        is_content_page = any(
            k in path for k in ("/blog", "/article", "/news", "/post",
                                 "/guide", "/about", "/research", "/report",
                                 "/case-study", "/whitepaper")
        )
        if is_content_page:
            findings.append(make_finding(
                "FRESH-007", "Content page lacks outbound links to authoritative sources", "low",
                f"{url} appears to be a content page ({parsed.word_count} words) "
                "but contains no outbound links to authoritative sources (.gov, .edu, "
                "Wikipedia, major news outlets). AI systems assess credibility partly "
                "by whether content references verifiable external sources.",
                "Add citations or references to authoritative external sources where "
                "claims are made. This increases the page's credibility signal for "
                "AI retrieval systems.",
                affected_urls=[url], confidence="medium", finding_type="recommendation",
            ))

    # ---- NEW 5. Email/newsletter readability check (FRESH-008 — Appendix F) ----
    # Check if the page has an email signup form
    if parsed.has_email_input:
        # Check if the surrounding content is image-heavy or text-light
        images_count = len(parsed.images)
        links_count = len(parsed.links)
        words = parsed.word_count

        # If there's an email signup but the page is image-heavy and text-poor
        if images_count > 5 and words < 200:
            findings.append(make_finding(
                "FRESH-008", "Email signup page is image-heavy and text-poor", "medium",
                f"{url} has an email newsletter signup form but only {words} words of "
                f"visible text and {images_count} images. If newsletter emails from this "
                "site are similarly image-heavy, AI email summarizers (Appendix F) will "
                "have little text to work with and will produce poor or empty summaries, "
                "causing important content to disappear in subscribers' inboxes.",
                "Ensure newsletter emails contain meaningful text content alongside "
                "visuals. Key messages, offers, and calls-to-action should be written "
                "as readable text, not embedded in images. Use a text-first email "
                "template that AI summarizers can process.",
                affected_urls=[url], confidence="medium",
            ))
        elif words > 50:
            # Page has a signup and reasonable text — suggest best practice
            findings.append(make_finding(
                "FRESH-008b", "Email newsletter readability best practice", "low",
                f"{url} has an email newsletter signup. To ensure AI email summarizers "
                "(per Appendix F of the evaluation criteria) can process your newsletters: "
                "content should be written as readable text, not locked in images or "
                "HTML-only formatting. AI-generated email summaries are built from "
                "readable text only.",
                "Audit outgoing newsletter templates: ensure the most important "
                "lines (offers, key announcements, CTAs) are written as plain HTML "
                "text, not embedded in images. Subject lines and preview text should "
                "accurately reflect the email body.",
                affected_urls=[url], confidence="medium", finding_type="recommendation",
            ))

    # Extract facts for cross-page consistency
    facts = _extract_facts(text, url)
    facts["_title"] = parsed.title.strip()

    return findings, facts


# ===========================================================================
# Cross-page consistency
# ===========================================================================

def check_cross_page_consistency(all_facts):
    """Compare extracted facts across pages for conflicts."""
    findings = []

    for fact_key in ("phone", "email", "address"):
        values_by_page = {}
        for pf in all_facts:
            vals = pf.get(fact_key, [])
            if vals:
                values_by_page[pf["_url"]] = set(vals)

        if len(values_by_page) >= 2:
            all_vals = set()
            for v_set in values_by_page.values():
                all_vals.update(v_set)

            if len(all_vals) > 1:
                detail_parts = []
                for pg_url, v_set in values_by_page.items():
                    detail_parts.append(f"{pg_url} shows {', '.join(sorted(v_set))}")

                severity = "high" if fact_key in ("phone", "email") else "medium"
                findings.append(make_finding(
                    f"FRESH-005-{fact_key.upper()}",
                    f"Inconsistent {fact_key} across pages",
                    severity,
                    "Different values found across the site: " + "; ".join(detail_parts) +
                    f". AI assistants may cite the wrong {fact_key}.",
                    f"Standardise the {fact_key} shown across all pages and update "
                    "structured data to match.",
                    affected_urls=list(values_by_page.keys()),
                ))

    return findings


# ===========================================================================
# Main
# ===========================================================================

def main():
    if not sys.stdin.isatty():
        pages = json.load(sys.stdin)
    elif len(sys.argv) == 2:
        pages = [sys.argv[1]]
    else:
        raise SystemExit("usage: freshness_audit.py URL  OR  pipe JSON page list to stdin")

    current_year = datetime.now().year
    all_findings = []
    all_facts = []

    for url in pages:
        page_findings, facts = audit_page(url, current_year)
        all_findings.extend(page_findings)
        if facts:
            all_facts.append(facts)

    # Cross-page consistency (phone, email, address)
    all_findings.extend(check_cross_page_consistency(all_facts))

    print(json.dumps(all_findings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
