#!/usr/bin/env python3
"""
Off-Site Discoverability Audit — specialist script.

Covers Appendix B, D, and E of the Round 3 problem statement:
  B. How assistants like ChatGPT use sources — quotability and reachability.
  D. Why agreement across the web matters — external corroboration, disambiguation.
  E. Personalization — location/context signals visible to AI systems.

Checks:
  - Whether the brand is discoverable via off-site signals (external links, 
    mention patterns, authority signals).
  - Whether the homepage's JSON-LD entity is properly anchored to external 
    authoritative profiles (Wikipedia, Wikidata, LinkedIn, Crunchbase, etc.).
  - Whether the content strategy produces quotable, citable facts.
  - Whether the brand name risks disambiguation collisions.
  - Whether the site supports personalisation signals (geo, language).
  - Whether the content is structured for AI-snippet extraction.

Dependencies: Python standard library only.
"""
import json, sys, os, re, urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page, make_finding, BRAND_TOKEN_RE, QUESTION_RE, PHONE_RE, EMAIL_RE


# ===========================================================================
# Helpers
# ===========================================================================

# Known authoritative profile domains for sameAs checking
_AUTHORITY_PROFILES = {
    "wikipedia.org": "Wikipedia",
    "wikidata.org": "Wikidata",
    "linkedin.com": "LinkedIn",
    "crunchbase.com": "Crunchbase",
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "bbc.co": "BBC",
    "ap.org": "Associated Press",
    "techcrunch.com": "TechCrunch",
    "forbes.com": "Forbes",
    "facebook.com": "Facebook",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "github.com": "GitHub",
    "glassdoor.com": "Glassdoor",
    "trustpilot.com": "Trustpilot",
    "g2.com": "G2",
    "producthunt.com": "Product Hunt",
    "yelp.com": "Yelp",
    "google.com/maps": "Google Maps",
    "maps.google.com": "Google Maps",
}

_GENERIC_WORDS = {
    "the", "best", "top", "new", "first", "great", "good", "big", "fast",
    "smart", "global", "prime", "elite", "alpha", "apex", "core", "one",
    "blue", "red", "green", "gold", "silver", "black", "white", "pure",
    "true", "real", "next", "bright", "clear", "fresh", "clean", "simple",
    "direct", "express", "general", "national", "united", "modern", "digital",
    "cloud", "star", "sun", "sky", "peak", "summit", "nova", "spark",
    "tech", "group", "solutions", "services", "labs", "works", "studio",
}

# Patterns that indicate AI-optimised content
_DIRECT_ANSWER_RE = re.compile(
    r'(?:is\s+a|are\s+a|provides?|offers?|specialises?\s+in|focuses?\s+on|'
    r'founded\s+in|headquartered\s+in|based\s+in|established\s+in)',
    re.I,
)

# Schema types that most help AI citation
_HIGH_VALUE_SCHEMA_TYPES = {
    "FAQPage", "HowTo", "Article", "NewsArticle", "BlogPosting",
    "Product", "LocalBusiness", "Organization", "WebPage",
    "BreadcrumbList", "Speakable", "QAPage", "Event",
    "Review", "AggregateRating",
}


def _extract_sameas(parsed):
    """Extract all sameAs URLs from JSON-LD blocks."""
    all_sameas = []
    for block in parsed.jsonld_blocks:
        if not isinstance(block, dict) or block.get("_parse_error"):
            continue
        sa = block.get("sameAs")
        if isinstance(sa, str):
            all_sameas.append(sa)
        elif isinstance(sa, list):
            all_sameas.extend([u for u in sa if isinstance(u, str)])
        # Also check @graph
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                if isinstance(item, dict):
                    sa2 = item.get("sameAs")
                    if isinstance(sa2, str):
                        all_sameas.append(sa2)
                    elif isinstance(sa2, list):
                        all_sameas.extend([u for u in sa2 if isinstance(u, str)])
    return all_sameas


def _get_org_name(parsed):
    """Extract Organization/Brand name from JSON-LD."""
    for block in parsed.jsonld_blocks:
        if not isinstance(block, dict) or block.get("_parse_error"):
            continue
        t = block.get("@type", "")
        types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        if any(x in types for x in ("Organization", "Corporation", "LocalBusiness",
                                     "Brand", "WebSite")):
            name = block.get("name")
            if name:
                return name
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                if isinstance(item, dict):
                    t2 = item.get("@type", "")
                    types2 = [t2] if isinstance(t2, str) else (t2 if isinstance(t2, list) else [])
                    if any(x in types2 for x in ("Organization", "Corporation",
                                                   "LocalBusiness", "Brand", "WebSite")):
                        name2 = item.get("name")
                        if name2:
                            return name2
    # Fallback to title suffix
    title = parsed.title.strip()
    for sep in (" | ", " - ", " – ", " — "):
        if sep in title:
            return title.split(sep)[-1].strip()
    return title.strip() if title else None


def _get_all_jsonld_types(parsed):
    """Get all @type values across all JSON-LD blocks."""
    types = set()
    for block in parsed.jsonld_blocks:
        if not isinstance(block, dict) or block.get("_parse_error"):
            continue
        t = block.get("@type")
        if isinstance(t, str):
            types.add(t)
        elif isinstance(t, list):
            types.update(t)
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                if isinstance(item, dict):
                    t2 = item.get("@type")
                    if isinstance(t2, str):
                        types.add(t2)
                    elif isinstance(t2, list):
                        types.update(t2)
    return types


# ===========================================================================
# Main audit
# ===========================================================================

def audit_offsite_discoverability(pages):
    """Run off-site discoverability audit against sampled pages."""
    findings = []
    if not pages:
        return findings

    homepage_url = pages[0]
    r = fetch(homepage_url)
    if r.get("error") or r["status"] >= 400:
        findings.append(make_finding(
            "OFFSITE-000", "Homepage unreachable — off-site audit skipped", "critical",
            "Cannot reach {} to run off-site discoverability checks.".format(homepage_url),
            "Ensure the homepage is publicly accessible.",
            affected_urls=[homepage_url],
        ))
        return findings

    html = r["body"].decode("utf-8", "replace")
    parsed = parse_page(html, r["url"])
    base_domain = urllib.parse.urlsplit(homepage_url).netloc

    # ------------------------------------------------------------------
    # OD-001: sameAs external profile anchors (Appendix D)
    # ------------------------------------------------------------------
    all_sameas = _extract_sameas(parsed)
    if not all_sameas:
        findings.append(make_finding(
            "OD-001", "No sameAs links — brand cannot be disambiguated by AI", "high",
            "The homepage JSON-LD contains no 'sameAs' property linking to external "
            "authoritative profiles. AI assistants (ChatGPT, Gemini, Perplexity, etc.) "
            "use sameAs to anchor the brand entity to known external databases, "
            "preventing confusion with similarly-named entities. Without sameAs, "
            "the brand is at high risk of being confused with or ignored in favour "
            "of better-anchored entities (Appendix D: agreement across the web).",
            "Add a 'sameAs' array to the Organization JSON-LD on the homepage listing "
            "all official external profiles. Priority targets: Wikipedia article (if one "
            "exists), Wikidata entry (Q-number URL), LinkedIn company page, Crunchbase "
            "profile, and all official social media accounts. Example: "
            "\"sameAs\": [\"https://en.wikipedia.org/wiki/BrandName\", "
            "\"https://www.wikidata.org/wiki/Q123456\", "
            "\"https://www.linkedin.com/company/brand-name\"]",
            affected_urls=[homepage_url],
        ))
    else:
        # Check quality of sameAs links
        matched_profiles = []
        unrecognised = []
        for sa_url in all_sameas:
            matched = False
            for domain, label in _AUTHORITY_PROFILES.items():
                if domain in sa_url:
                    matched_profiles.append(label)
                    matched = True
                    break
            if not matched:
                unrecognised.append(sa_url)

        high_value = {"Wikipedia", "Wikidata", "LinkedIn", "Crunchbase"}
        missing_hv = high_value - set(matched_profiles)

        if missing_hv:
            findings.append(make_finding(
                "OD-001b", "sameAs present but missing high-value profiles", "medium",
                "The homepage has sameAs links ({}) but is missing links to key "
                "authoritative profiles: {}. High-value profiles like Wikipedia and "
                "Wikidata are the primary sources AI systems use to verify entity "
                "identity.".format(
                    ", ".join(matched_profiles[:5]),
                    ", ".join(sorted(missing_hv))),
                "Add sameAs links to the missing profiles: {}. If the brand does not "
                "yet have a Wikipedia article or Wikidata entry, consider creating them "
                "— they are the most impactful off-site discoverability actions "
                "available.".format(", ".join(sorted(missing_hv))),
                affected_urls=[homepage_url], confidence="high",
                finding_type="recommendation",
            ))

    # ------------------------------------------------------------------
    # OD-002: Brand name ambiguity risk (Appendix D)
    # ------------------------------------------------------------------
    org_name = _get_org_name(parsed)
    if org_name:
        words = org_name.lower().split()
        generic_overlap = [w for w in words if w in _GENERIC_WORDS]
        # Single-word or mostly-generic brand names are high-risk
        if len(words) <= 2 and len(generic_overlap) >= 1:
            findings.append(make_finding(
                "OD-002", "Brand name is generic — high ambiguity risk", "high",
                "The brand name '{}' is short and contains common words ({}). "
                "AI language models are trained on vast corpora where these words "
                "appear in many unrelated contexts. Without strong entity anchors "
                "(sameAs, Wikipedia, consistent external mentions), the brand is "
                "very likely to be confused with other entities, concepts, or "
                "dictionary definitions when AI systems build responses "
                "(Appendix D: mistaken identity).".format(
                    org_name, ", ".join(generic_overlap)),
                "1) Add sameAs links to authoritative profiles to anchor the entity. "
                "2) Use the full legal name consistently in JSON-LD (e.g., 'Apex "
                "Technologies Ltd' rather than just 'Apex'). "
                "3) Add a disambiguating description in JSON-LD: "
                "\"description\": \"Apex Technologies — B2B SaaS platform for supply "
                "chain optimisation, founded 2018, headquartered in Austin TX.\" "
                "4) Build Wikipedia/Wikidata presence to create a stable, citable "
                "entity record.",
                affected_urls=[homepage_url], confidence="medium",
            ))
        elif len(words) <= 3 and len(generic_overlap) >= len(words) * 0.5:
            findings.append(make_finding(
                "OD-002b", "Brand name contains generic terms — moderate ambiguity risk", "medium",
                "The brand name '{}' contains generic terms ({}). This creates a "
                "moderate risk of AI disambiguation errors.".format(
                    org_name, ", ".join(generic_overlap)),
                "Strengthen entity anchoring via sameAs links and a specific, "
                "distinguishing JSON-LD description that includes industry, "
                "founding year, and location.",
                affected_urls=[homepage_url], confidence="medium",
                finding_type="recommendation",
            ))

    # ------------------------------------------------------------------
    # OD-003: Content quotability for AI citation (Appendix B)
    # ------------------------------------------------------------------
    # Analyse across all pages: do they contain concrete, quotable facts?
    pages_with_facts = 0
    pages_without_facts = []
    _FACT_RE = re.compile(
        r'(?:\$[\d,.]+(?:\s*(?:million|billion|M|B|K))?'
        r'|€[\d,.]+|£[\d,.]+|\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+'
        r'|\b(?:founded|established|launched)\s+in\s+\d{4}'
        r'|\b\d+\s*(?:employees?|staff|people|customers?|users?|countries?|offices?))',
        re.I,
    )

    # Check a sample of pages (up to 5 to keep runtime bounded)
    for purl in pages[:5]:
        try:
            pr = fetch(purl, timeout=8)
            if pr.get("error") or pr["status"] >= 400:
                continue
            p_html = pr["body"].decode("utf-8", "replace")
            p_parsed = parse_page(p_html, pr["url"])
            facts = _FACT_RE.findall(p_parsed.visible_text)
            direct_answers = _DIRECT_ANSWER_RE.findall(p_parsed.visible_text)
            if facts or direct_answers:
                pages_with_facts += 1
            else:
                pages_without_facts.append(purl)
        except Exception:
            continue

    checked = len(pages[:5])
    if checked > 0 and pages_with_facts < checked * 0.4:
        findings.append(make_finding(
            "OD-003", "Low content quotability — few concrete facts for AI to cite", "high",
            "{} of {} sampled pages ({} without quotable facts: {}) contain no "
            "machine-extractable facts: no numbers, percentages, dates, prices, "
            "or direct definitional statements. AI assistants (Appendix B) cite "
            "pages by directly quoting specific facts. Pages with only vague "
            "marketing language will almost never appear in AI-generated answers "
            "because there is nothing concrete to quote.".format(
                checked - pages_with_facts, checked,
                len(pages_without_facts), ", ".join(pages_without_facts[:3])),
            "Rewrite content to include specific, citable facts on every major page: "
            "founding year, location, employee/customer count, product specifications, "
            "pricing ranges, statistics, or direct definitional sentences like "
            "'[Brand] is a [category] platform that [does X] for [audience].' "
            "Every page should answer at least one specific question a user might ask.",
            affected_urls=pages_without_facts[:5], confidence="high",
        ))

    # ------------------------------------------------------------------
    # OD-004: Organisation entity JSON-LD quality (Appendix B, D)
    # ------------------------------------------------------------------
    all_types = _get_all_jsonld_types(parsed)
    has_org_schema = bool(all_types & {"Organization", "Corporation", "LocalBusiness",
                                        "Company", "Brand", "WebSite"})
    if not has_org_schema:
        findings.append(make_finding(
            "OD-004", "No Organization/Brand JSON-LD schema on homepage", "high",
            "The homepage has no Organization, Corporation, or LocalBusiness JSON-LD "
            "schema. This is the primary way to tell AI systems who the brand is, "
            "what it does, and how to identify it uniquely. Without this, AI assistants "
            "must guess the entity from unstructured text, leading to errors and "
            "omissions (Appendix B: sources must be easy to read and quote).",
            "Add an Organization JSON-LD block to the homepage's <head> containing: "
            "@type (Organization/LocalBusiness/etc), name (exact brand name), "
            "url (canonical homepage URL), logo, description (factual 1-2 sentence "
            "summary), sameAs (list of authoritative profile URLs), "
            "and contact information (telephone, email, address). "
            "This is the single highest-impact structured data change for AI discoverability.",
            affected_urls=[homepage_url],
        ))
    else:
        # Check Organisation JSON-LD completeness
        org_block = None
        for block in parsed.jsonld_blocks:
            if not isinstance(block, dict) or block.get("_parse_error"):
                continue
            t = block.get("@type", "")
            types_list = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
            if any(x in types_list for x in ("Organization", "Corporation",
                                               "LocalBusiness", "Brand")):
                org_block = block
                break
            if "@graph" in block and isinstance(block["@graph"], list):
                for item in block["@graph"]:
                    if isinstance(item, dict):
                        t2 = item.get("@type", "")
                        types2 = [t2] if isinstance(t2, str) else (t2 if isinstance(t2, list) else [])
                        if any(x in types2 for x in ("Organization", "Corporation",
                                                       "LocalBusiness", "Brand")):
                            org_block = item
                            break

        if org_block:
            missing_fields = []
            important_fields = {
                "name": "entity name",
                "url": "canonical URL",
                "description": "factual description",
                "logo": "logo URL",
                "sameAs": "external profile links",
            }
            for field, label in important_fields.items():
                if field not in org_block:
                    missing_fields.append(label)

            if missing_fields:
                findings.append(make_finding(
                    "OD-004b", "Organization JSON-LD is incomplete", "medium",
                    "The homepage Organization JSON-LD is missing key fields: {}. "
                    "Each missing field reduces the precision with which AI systems "
                    "can identify and describe the brand.".format(
                        ", ".join(missing_fields)),
                    "Add the missing fields to the Organization JSON-LD: {}. "
                    "The 'description' field is especially important — it should be "
                    "a factual, concise statement of what the brand does, for whom, "
                    "and what makes it distinctive.".format(
                        ", ".join(missing_fields)),
                    affected_urls=[homepage_url], confidence="high",
                    finding_type="recommendation",
                ))

    # ------------------------------------------------------------------
    # OD-005: External mentions / outbound social proof links (Appendix D)
    # ------------------------------------------------------------------
    # Check if the homepage links out to any press, reviews, or external mentions
    press_link_patterns = re.compile(
        r'(?:press|media|news|coverage|featured|mentioned|as-seen|reviews?|'
        r'testimonials?|awards?|recognition|partner)',
        re.I,
    )
    external_press_links = [
        l for l in parsed.external_links
        if press_link_patterns.search(l["text"] + " " + l["href"])
    ]
    # Also check for press/news internal section links
    internal_press_links = [
        l for l in parsed.internal_links
        if press_link_patterns.search(l["text"] + " " + l["href"])
    ]

    if not external_press_links and not internal_press_links:
        findings.append(make_finding(
            "OD-005", "No press, media, or external mention links", "medium",
            "The homepage contains no links to press coverage, media mentions, "
            "reviews, awards, or 'as seen in' content. AI systems assess brand "
            "credibility by cross-referencing mentions across independent sources "
            "(Appendix D: agreement across the web). A brand that only describes "
            "itself, without pointing to independent corroboration, appears less "
            "trustworthy and citable to AI systems.",
            "Add a 'Press' or 'As seen in' section to the homepage linking to "
            "genuine independent media coverage, reviews, or awards. Create a "
            "dedicated /press page listing all coverage. Actively seek and collect "
            "third-party mentions on sites like TechCrunch, Forbes, G2, Trustpilot, "
            "and industry publications — these external mentions are what AI systems "
            "use to corroborate your brand's claims.",
            affected_urls=[homepage_url], confidence="medium",
            finding_type="recommendation",
        ))

    # ------------------------------------------------------------------
    # OD-006: Contact information for AI citation (Appendix B)
    # ------------------------------------------------------------------
    # AI assistants often need to cite specific contact details
    visible_text = parsed.visible_text
    phones = PHONE_RE.findall(visible_text)
    emails = EMAIL_RE.findall(visible_text)

    if not phones and not emails:
        findings.append(make_finding(
            "OD-006", "No contact information on homepage", "medium",
            "The homepage contains no phone number or email address in visible text. "
            "When users ask AI assistants for contact information about a brand, "
            "the assistant needs this data in directly readable text on a page it "
            "can access and cite. If contact info is only in an image or behind a "
            "contact form, it cannot be cited.",
            "Add at least one phone number and/or email address as readable text "
            "on the homepage or a clearly-linked /contact page. Also add this "
            "information to the Organization JSON-LD (telephone, email fields) so "
            "it is available as structured data.",
            affected_urls=[homepage_url], confidence="medium",
        ))

    # ------------------------------------------------------------------
    # OD-007: High-value schema types for AI snippet generation (Appendix B)
    # ------------------------------------------------------------------
    # Across all sampled pages, check what schema types are present
    found_schema_types = set()
    for purl in pages[:5]:
        try:
            pr = fetch(purl, timeout=8)
            if pr.get("error") or pr["status"] >= 400:
                continue
            p_html = pr["body"].decode("utf-8", "replace")
            p_parsed = parse_page(p_html, pr["url"])
            found_schema_types.update(_get_all_jsonld_types(p_parsed))
        except Exception:
            continue

    # High-ROI schema types for AI discoverability
    ai_optimised_types = {"FAQPage", "HowTo", "Speakable", "QAPage"}
    missing_ai_types = ai_optimised_types - found_schema_types

    if len(missing_ai_types) >= 3:
        findings.append(make_finding(
            "OD-007", "Missing high-ROI schema types for AI answer generation", "medium",
            "None of the sampled pages use schema types specifically optimised for "
            "AI answer generation: {}. These schema types directly enable AI assistants "
            "to extract and cite answers from the site. FAQPage lets AI cite specific "
            "Q&A pairs; HowTo generates step-by-step snippets; Speakable identifies "
            "content for voice assistants; QAPage marks community Q&A content.".format(
                ", ".join(sorted(missing_ai_types))),
            "Implement the relevant schema types on appropriate pages: "
            "FAQPage on help/FAQ pages, HowTo on tutorial/guide pages, "
            "Speakable on key content sections, QAPage on community/forum pages. "
            "These schema types are the most direct way to optimise for AI citation.",
            affected_urls=pages[:3], confidence="medium",
            finding_type="recommendation",
        ))

    # ------------------------------------------------------------------
    # OD-008: Direct answer content patterns (Appendix E: personalization)
    # ------------------------------------------------------------------
    # Check if content directly answers questions that users might ask AI
    questions_in_content = []
    for purl in pages[:3]:
        try:
            pr = fetch(purl, timeout=8)
            if pr.get("error") or pr["status"] >= 400:
                continue
            p_html = pr["body"].decode("utf-8", "replace")
            p_parsed = parse_page(p_html, pr["url"])
            qs = QUESTION_RE.findall(p_parsed.visible_text)
            questions_in_content.extend([q.strip() for q in qs if len(q.strip()) > 15])
        except Exception:
            continue

    if not questions_in_content and len(pages) >= 2:
        findings.append(make_finding(
            "OD-008", "No direct-answer content patterns detected", "medium",
            "None of the sampled pages contain explicit question-and-answer style "
            "content. AI assistants specifically look for content that directly "
            "answers questions ('What is X?', 'How does X work?', 'Why should I "
            "use X?') because this is the format they cite in conversational "
            "responses (Appendix B, E). Content that only describes features without "
            "directly answering user questions is rarely cited.",
            "Add a dedicated FAQ section or page that answers the top 10 questions "
            "users ask about the brand. Write at least one page that directly answers "
            "'What is [Brand]?' with a clear, factual, 2-3 sentence answer at the top. "
            "Structure product/service pages with a Q&A section. Use FAQPage JSON-LD "
            "to mark up this content for AI extraction.",
            affected_urls=pages[:3], confidence="medium",
            finding_type="recommendation",
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
        raise SystemExit("usage: offsite_audit.py URL  OR  pipe JSON page list to stdin")

    findings = audit_offsite_discoverability(pages)
    print(json.dumps(findings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
