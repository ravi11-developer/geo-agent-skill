#!/usr/bin/env python3
"""
On-Site Engagement Audit — specialist script.

Accepts a JSON page-list on stdin (from orchestrator) OR a single URL argument.
Checks orientation cues, headings (presence, hierarchy, multiple H1s),
breadcrumbs, internal/external link health, dead-end pages, CTAs,
viewport meta, word count, broken links, hreflang, lang attribute,
FAQ/Q&A content patterns, and personalization readiness signals.

Dependencies: Python standard library only.
"""
import json, sys, os, re, urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page, make_finding, QUESTION_RE


# ===========================================================================
# Word count thresholds
# ===========================================================================

_THIN_CONTENT_WORDS = 100
_SPARSE_CONTENT_WORDS = 300


# ===========================================================================
# Per-page audit
# ===========================================================================

def audit_page(url):
    """Return (findings_list, parsed_page | None) for a single page."""
    findings = []
    r = fetch(url)

    if r.get("error") or r["status"] >= 400:
        return findings, None

    html = r["body"].decode("utf-8", "replace")
    parsed = parse_page(html, r["url"])

    # ---- 1. Orientation: H1 ----
    h1s = [h for h in parsed.headings if h[0] == "h1"]
    if not h1s:
        findings.append(make_finding(
            "ENGAGE-001", "Missing H1 heading", "high",
            "{} has no <h1> tag. Visitors arriving from an AI answer cannot "
            "quickly confirm the page is relevant to their question.".format(url),
            "Add a single, descriptive <h1> that summarises the page purpose.",
            affected_urls=[url],
        ))

    if len(parsed.headings) == 0:
        findings.append(make_finding(
            "ENGAGE-002", "No semantic headings at all", "high",
            "{} contains zero heading tags (h1–h6). The page lacks any "
            "structural cues for scanning.".format(url),
            "Use a heading hierarchy (h1 > h2 > h3) to organise page content.",
            affected_urls=[url],
        ))

    # ---- NEW: ENGAGE-013 — Multiple H1 tags ----
    if len(h1s) > 1:
        h1_texts = [h[1][:60] for h in h1s]
        findings.append(make_finding(
            "ENGAGE-013", "Multiple H1 tags on a single page", "medium",
            "{} contains {} <h1> elements: {}. Multiple H1s confuse AI systems "
            "about the primary topic of the page, potentially causing misclassification "
            "or reduced extraction accuracy.".format(
                url, len(h1s), "; ".join('"{}"'.format(t) for t in h1_texts[:3])),
            "Use exactly one <h1> per page that clearly names the primary topic. "
            "Convert additional H1s to H2 or H3 as appropriate.",
            affected_urls=[url], confidence="high",
        ))

    # ---- NEW: ENGAGE-014 — Heading hierarchy violations ----
    if len(parsed.headings) >= 2:
        _heading_levels = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
        violations = []
        prev_level = 0
        for tag, text in parsed.headings:
            level = _heading_levels.get(tag, 0)
            if prev_level > 0 and level > prev_level + 1:
                violations.append(
                    "Jumps from H{} to H{} ('{}'...) ".format(prev_level, level, text[:40])
                )
            prev_level = level

        if violations:
            findings.append(make_finding(
                "ENGAGE-014", "Heading hierarchy skips levels", "medium",
                "{} has heading hierarchy violations: {}. Skipping heading levels "
                "reduces the clarity of page structure for AI content parsers and "
                "screen readers, potentially causing misextraction of document hierarchy.".format(
                    url, "; ".join(violations[:3])),
                "Maintain sequential heading nesting: H1 → H2 → H3. Never skip a "
                "heading level (e.g., H1 directly to H3). Fix by inserting missing "
                "levels or adjusting heading ranks.",
                affected_urls=[url], confidence="high",
            ))

    # ---- 2. Sparse text ----
    if parsed.paragraph_count < 2:
        findings.append(make_finding(
            "ENGAGE-003", "Very little informational text", "medium",
            "{} contains only {} <p> element(s). A first-time visitor may not "
            "find enough context to understand what the page offers.".format(
                url, parsed.paragraph_count),
            "Provide concise explanatory paragraphs that establish relevance.",
            affected_urls=[url], confidence="medium",
        ))

    # ---- Word count ----
    wc = parsed.word_count
    if wc < _THIN_CONTENT_WORDS:
        findings.append(make_finding(
            "ENGAGE-010", "Critically thin word count", "high",
            "{} contains only ~{} words of visible text. Pages with very little "
            "text provide minimal substance for AI retrieval and are unlikely to "
            "satisfy user queries.".format(url, wc),
            "Add substantive content. Aim for at least 300 words of meaningful, "
            "factual text relevant to the page's purpose.",
            affected_urls=[url], confidence="medium",
        ))
    elif wc < _SPARSE_CONTENT_WORDS:
        findings.append(make_finding(
            "ENGAGE-011", "Low word count", "low",
            "{} contains ~{} words of visible text, which is below the 300-word "
            "threshold commonly associated with substantive content. The page may "
            "not provide enough context for AI systems to extract useful answers.".format(
                url, wc),
            "Consider expanding the page content with relevant factual detail, "
            "examples, or structured information.",
            affected_urls=[url], confidence="low", finding_type="recommendation",
        ))

    # ---- 3. Navigation / internal links ----
    n_internal = len(parsed.internal_links)
    if n_internal == 0:
        findings.append(make_finding(
            "ENGAGE-004", "Dead-end page — no internal links", "high",
            "{} contains zero internal links. Visitors have no path to continue "
            "exploring the site.".format(url),
            "Add contextual links to related pages, a navigation bar, or a "
            "breadcrumb trail.",
            affected_urls=[url],
        ))
    elif n_internal < 3:
        findings.append(make_finding(
            "ENGAGE-005", "Very few internal links", "medium",
            "{} contains only {} internal link(s), limiting the visitor's ability "
            "to navigate further.".format(url, n_internal),
            "Add navigation paths, related content links, or breadcrumbs.",
            affected_urls=[url],
        ))

    # ---- 4. Breadcrumbs on deep pages ----
    path_depth = url.rstrip("/").count("/") - 2
    if path_depth >= 2 and not parsed.has_breadcrumb:
        findings.append(make_finding(
            "ENGAGE-006", "Deep page without breadcrumbs", "medium",
            "{} is {} levels deep but has no breadcrumb navigation. Visitors lose "
            "context about where they are in the site hierarchy.".format(url, path_depth),
            "Add breadcrumb navigation (e.g., <nav aria-label='breadcrumb'>) "
            "on pages more than one level deep.",
            affected_urls=[url], confidence="medium", finding_type="recommendation",
        ))

    # ---- 5. Calls to action ----
    if parsed.buttons == 0:
        findings.append(make_finding(
            "ENGAGE-007", "No clear calls to action", "low",
            "{} has no <button> elements or link-styled CTAs. Visitors may not "
            "know what action to take next.".format(url),
            "Add a clear, meaningful next-step action appropriate to the page intent.",
            affected_urls=[url], finding_type="recommendation",
        ))

    # ---- 6. Broken internal links (sample up to 5) ----
    broken_internal = []
    for link in parsed.internal_links[:5]:
        try:
            probe = fetch(link["href"], timeout=6)
            if probe["status"] >= 400:
                broken_internal.append(
                    "{} → HTTP {}".format(link["href"], probe["status"]))
        except Exception:
            pass

    if broken_internal:
        findings.append(make_finding(
            "ENGAGE-008", "Broken internal links detected", "high",
            "{} links to pages that return errors: {}".format(
                url, "; ".join(broken_internal)),
            "Fix or remove broken internal links to prevent dead-end experiences.",
            affected_urls=[url] + [b.split(" →")[0] for b in broken_internal],
        ))

    # ---- 7. Broken external links ----
    broken_external = []
    ext_http_links = [l for l in parsed.external_links if l["href"].startswith("http")]
    for link in ext_http_links[:5]:
        try:
            probe = fetch(link["href"], timeout=6)
            if probe["status"] >= 400:
                broken_external.append(
                    "{} → HTTP {}".format(link["href"], probe["status"]))
        except Exception:
            pass

    if broken_external:
        findings.append(make_finding(
            "ENGAGE-012", "Broken external links detected", "medium",
            "{} links to external pages that return errors: {}. Broken outbound "
            "links undermine credibility and signal poor maintenance to crawlers.".format(
                url, "; ".join(broken_external)),
            "Fix or remove broken external links. Replace with working references "
            "or remove outdated citations.",
            affected_urls=[url] + [b.split(" →")[0] for b in broken_external],
            confidence="high",
        ))

    # ---- 8. Viewport meta ----
    if not parsed.viewport_meta:
        findings.append(make_finding(
            "ENGAGE-009", "Missing viewport meta tag", "medium",
            "{} does not include <meta name='viewport'>. The page may not render "
            "well on mobile devices, harming engagement for mobile users arriving "
            "from AI answers.".format(url),
            "Add <meta name='viewport' content='width=device-width, initial-scale=1'>.",
            affected_urls=[url],
        ))

    # ---- NEW: ENGAGE-015 — Missing lang attribute (Appendix E: personalization) ----
    if not parsed.lang_attr:
        findings.append(make_finding(
            "ENGAGE-015", "Missing lang attribute on <html> element", "medium",
            "{} has no lang attribute on the <html> tag (e.g., lang='en'). "
            "AI systems and screen readers use the lang attribute to correctly "
            "interpret content. It also enables hreflang to work correctly for "
            "international visitors.".format(url),
            "Add a lang attribute to the <html> element: <html lang='en'>. "
            "Use the appropriate BCP 47 language code for the page content.",
            affected_urls=[url], confidence="high",
        ))

    # ---- NEW: ENGAGE-016 — Missing hreflang (Appendix E: personalization) ----
    # Only flag on homepage or if site appears to target international users
    path = urllib.parse.urlsplit(url).path
    is_homepage = path in ("/", "")
    if is_homepage and not parsed.hreflang_links:
        findings.append(make_finding(
            "ENGAGE-016", "No hreflang tags for international content", "low",
            "{} is the homepage but has no hreflang link elements. "
            "If the site targets users in multiple countries or languages, "
            "hreflang helps AI assistants serve the correct language/region "
            "version to each user (Appendix E: personalization signals).".format(url),
            "If the site has multi-language or multi-region versions, add "
            "<link rel='alternate' hreflang='en' href='...'> elements for each variant. "
            "Include an x-default fallback. This enables AI systems to personalise "
            "which version to cite for each user.",
            affected_urls=[url], confidence="low", finding_type="recommendation",
        ))

    # ---- NEW: ENGAGE-017 — FAQ/Q&A content pattern without schema ----
    # Check if the page body contains question patterns but no FAQ schema
    visible_text = parsed.visible_text
    questions_found = QUESTION_RE.findall(visible_text)
    # Filter to meaningful questions (longer than 20 chars)
    meaningful_questions = [q.strip() for q in questions_found if len(q.strip()) > 20]

    if len(meaningful_questions) >= 3:
        # Check if there's any FAQPage JSON-LD
        has_faq_schema = any(
            "FAQPage" in str(block.get("@type", ""))
            for block in parsed.jsonld_blocks
            if isinstance(block, dict) and not block.get("_parse_error")
        )
        if not has_faq_schema:
            findings.append(make_finding(
                "ENGAGE-017", "Page with Q&A content lacks FAQPage schema", "medium",
                "{} contains {} question-style sentences (e.g., '{}...') but no "
                "FAQPage JSON-LD schema. AI assistants specifically target FAQPage "
                "markup to extract direct question-answer pairs for conversational "
                "responses. Without the schema, these answers are much less likely "
                "to be cited.".format(
                    url, len(meaningful_questions),
                    meaningful_questions[0][:60] if meaningful_questions else ""),
                "Add FAQPage JSON-LD schema wrapping the existing Q&A content. "
                "Structure each question as a 'Question' entity with an 'acceptedAnswer'. "
                "This is one of the highest-ROI schema types for AI discoverability.",
                affected_urls=[url], confidence="medium", finding_type="recommendation",
            ))

    # ---- NEW: ENGAGE-018 — Form accessibility (labels) ----
    if parsed.forms_count > 0 and parsed.input_count > parsed.label_count:
        unlabelled = parsed.input_count - parsed.label_count
        findings.append(make_finding(
            "ENGAGE-018", "Form inputs without associated labels", "medium",
            "{} has {} form input(s) but only {} label(s) — approximately {} "
            "input(s) may be unlabelled. Unlabelled inputs are inaccessible to "
            "screen readers and AI form parsers, and reduce the page's overall "
            "accessibility score.".format(
                url, parsed.input_count, parsed.label_count, unlabelled),
            "Add a <label for='input-id'> element for every form input. "
            "Alternatively, use aria-label or aria-labelledby attributes.",
            affected_urls=[url], confidence="medium",
        ))

    return findings, parsed


# ===========================================================================
# Main
# ===========================================================================

def main():
    if not sys.stdin.isatty():
        pages = json.load(sys.stdin)
    elif len(sys.argv) == 2:
        pages = [sys.argv[1]]
    else:
        raise SystemExit("usage: engagement_audit.py URL  OR  pipe JSON page list to stdin")

    all_findings = []
    for url in pages:
        page_findings, _ = audit_page(url)
        all_findings.extend(page_findings)

    print(json.dumps(all_findings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
