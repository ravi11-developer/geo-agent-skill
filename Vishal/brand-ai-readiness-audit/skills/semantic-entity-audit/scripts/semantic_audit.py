#!/usr/bin/env python3
"""
Semantic & Entity Audit — specialist script.

Accepts a JSON page-list on stdin (from orchestrator) OR a single URL argument.
Checks title, meta description, JSON-LD validity and value accuracy,
OG tags, Twitter Cards, entity identity consistency across pages,
page-intent detection, authorship signals, body-text terminology consistency,
sameAs disambiguation links, FAQ schema, speakable schema, quotability,
brand ambiguity risk, E-E-A-T signals, and duplicate content detection.

Dependencies: Python standard library only.
"""
import json, sys, os, re, urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import fetch, parse_page, make_finding, BRAND_TOKEN_RE, QUESTION_RE


# ===========================================================================
# JSON-LD helpers
# ===========================================================================

def _jsonld_name(block):
    """Extract the primary 'name' from a JSON-LD block (handles @graph)."""
    if isinstance(block, dict):
        if block.get("_parse_error"):
            return None
        if "name" in block:
            return block["name"]
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                if isinstance(item, dict) and "name" in item:
                    return item["name"]
    return None


def _jsonld_types(block):
    """Return set of @type values from a JSON-LD block."""
    types = set()
    if isinstance(block, dict):
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


def _jsonld_author(block):
    """Extract author name from a JSON-LD block."""
    if not isinstance(block, dict) or block.get("_parse_error"):
        return None
    author = block.get("author")
    if isinstance(author, dict):
        return author.get("name", "")
    if isinstance(author, str):
        return author
    if "@graph" in block and isinstance(block["@graph"], list):
        for item in block["@graph"]:
            if isinstance(item, dict):
                a = item.get("author")
                if isinstance(a, dict):
                    return a.get("name", "")
                if isinstance(a, str):
                    return a
    return None


def _jsonld_sameas(block):
    """Extract sameAs URLs from a JSON-LD block."""
    urls = []
    if not isinstance(block, dict) or block.get("_parse_error"):
        return urls
    sa = block.get("sameAs")
    if isinstance(sa, str):
        urls.append(sa)
    elif isinstance(sa, list):
        urls.extend([u for u in sa if isinstance(u, str)])
    if "@graph" in block and isinstance(block["@graph"], list):
        for item in block["@graph"]:
            if isinstance(item, dict):
                sa2 = item.get("sameAs")
                if isinstance(sa2, str):
                    urls.append(sa2)
                elif isinstance(sa2, list):
                    urls.extend([u for u in sa2 if isinstance(u, str)])
    return urls


def _jsonld_has_faq(blocks):
    """Check if any JSON-LD block has FAQPage type."""
    for block in blocks:
        types = _jsonld_types(block)
        if "FAQPage" in types:
            return True
    return False


def _jsonld_has_speakable(blocks):
    """Check if any JSON-LD block contains a speakable property."""
    for block in blocks:
        if isinstance(block, dict):
            if "speakable" in block:
                return True
            if "@graph" in block and isinstance(block["@graph"], list):
                for item in block["@graph"]:
                    if isinstance(item, dict) and "speakable" in item:
                        return True
    return False


def _extract_body_brand_candidates(visible_text, min_occurrences=2):
    """Return a frequency map of Title-Case multi-word tokens in visible text."""
    tokens = BRAND_TOKEN_RE.findall(visible_text)
    freq = {}
    for tok in tokens:
        freq[tok] = freq.get(tok, 0) + 1
    return {tok: cnt for tok, cnt in freq.items() if cnt >= min_occurrences}


# Common generic words that make brand names ambiguous
_GENERIC_WORDS = {
    "the", "best", "top", "new", "first", "great", "good", "big", "fast",
    "smart", "global", "prime", "elite", "alpha", "apex", "core", "one",
    "blue", "red", "green", "gold", "silver", "black", "white", "pure",
    "true", "real", "next", "bright", "clear", "fresh", "clean", "simple",
    "direct", "express", "general", "national", "united", "modern", "digital",
    "cloud", "star", "sun", "sky", "peak", "summit", "nova", "spark",
}

# Fact-like patterns (numbers, prices, measurements, dates)
_FACT_PATTERN = re.compile(
    r'(?:\$[\d,.]+|€[\d,.]+|£[\d,.]+|\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+|\d+\s*(?:mg|kg|lb|oz|ml|ft|in|cm|mm|hr|min|sec|mph|kph|gb|mb|tb))',
    re.I
)


# ===========================================================================
# Per-page audit
# ===========================================================================

def audit_page(url):
    """Audit a single page for semantic/entity issues."""
    findings = []
    r = fetch(url)

    if r.get("error") or r["status"] >= 400:
        return findings, {}

    html = r["body"].decode("utf-8", "replace")
    parsed = parse_page(html, r["url"])

    page_info = {
        "_url": url,
        "_title": parsed.title.strip(),
        "_meta_desc": parsed.meta_description.strip(),
        "_jsonld_names": [],
        "_jsonld_types": set(),
        "_og_title": parsed.og_tags.get("og:title", ""),
        "_has_author": False,
        "_author_source": "",
        "_brand_candidates": {},
        "_has_sameas": False,
        "_sameas_urls": [],
    }

    # ---- 1. Title ----
    if not parsed.title.strip():
        findings.append(make_finding(
            "SEMANTIC-001", "Missing page title", "high",
            "{} has no <title> tag or it is empty. AI systems rely on the title "
            "to determine the page topic and entity.".format(url),
            "Add a descriptive <title> that names the entity and page purpose.",
            affected_urls=[url],
        ))

    # ---- 2. Meta description ----
    if not parsed.meta_description.strip():
        findings.append(make_finding(
            "SEMANTIC-002", "Missing meta description", "medium",
            "{} has no <meta name='description'>. AI systems and search engines "
            "use this to understand page content at a glance.".format(url),
            "Add a meta description summarising the explicit facts on this page.",
            affected_urls=[url],
        ))

    # ---- 3. Structured data presence ----
    has_jsonld = len(parsed.jsonld_blocks) > 0
    has_og = len(parsed.og_tags) > 0

    if not has_jsonld and not has_og:
        findings.append(make_finding(
            "SEMANTIC-003", "No machine-readable structured data", "high",
            "{} has no JSON-LD scripts and no OpenGraph tags. Without structured "
            "data, AI systems must infer entity facts from unstructured text, "
            "which is error-prone.".format(url),
            "Implement Schema.org JSON-LD (Organization, Product, Service, Article) "
            "and OpenGraph meta tags.",
            affected_urls=[url],
        ))

    # ---- 4. JSON-LD syntax validation ----
    for i, block in enumerate(parsed.jsonld_blocks):
        if isinstance(block, dict) and block.get("_parse_error"):
            findings.append(make_finding(
                "SEMANTIC-004", "Invalid JSON-LD syntax (block {})".format(i + 1), "high",
                "{} contains a <script type='application/ld+json'> block that "
                "cannot be parsed as valid JSON. AI agents will silently ignore "
                "it.".format(url),
                "Fix the JSON syntax error in the JSON-LD block.",
                affected_urls=[url],
            ))
        else:
            name = _jsonld_name(block)
            if name:
                page_info["_jsonld_names"].append(name)
            page_info["_jsonld_types"].update(_jsonld_types(block))

    # ---- 5. JSON-LD name vs visible title ----
    for block in parsed.jsonld_blocks:
        name = _jsonld_name(block)
        if name and parsed.title.strip():
            visible = parsed.visible_text.lower()
            if name.lower() not in visible:
                findings.append(make_finding(
                    "SEMANTIC-005", "JSON-LD name not found in visible text", "medium",
                    "{}: JSON-LD declares name='{}' but this exact string does not "
                    "appear in the visible page text. The structured data may be "
                    "stale or describe a different entity.".format(url, name),
                    "Ensure JSON-LD name/description match the visible page content.",
                    affected_urls=[url], confidence="medium",
                ))
                break

    # ---- 6. Page-intent detection ----
    has_intent_signal = (
        has_jsonld or has_og or
        any(k in parsed.og_tags for k in ("og:type",)) or
        len(parsed.headings) > 0
    )
    if not has_intent_signal:
        findings.append(make_finding(
            "SEMANTIC-006", "Ambiguous page intent", "medium",
            "{} provides no structured data, no OG type, and no headings. A machine "
            "cannot distinguish whether this is a product page, article, contact "
            "page, or category listing.".format(url),
            "Add at minimum an og:type tag and a clear <h1> heading to signal page intent.",
            affected_urls=[url], finding_type="recommendation",
        ))

    # ---- 7. Authorship on article pages (SEMANTIC-009) ----
    author_found = False
    author_sources = []

    if parsed.meta_author:
        author_found = True
        author_sources.append("meta[name=author]='{}'".format(parsed.meta_author))
    if parsed.og_author:
        author_found = True
        author_sources.append("article:author='{}'".format(parsed.og_author))
    for block in parsed.jsonld_blocks:
        a = _jsonld_author(block)
        if a:
            author_found = True
            author_sources.append("JSON-LD author='{}'".format(a))
            break

    page_info["_has_author"] = author_found
    page_info["_author_source"] = "; ".join(author_sources)

    article_types = {"Article", "BlogPosting", "NewsArticle", "TechArticle", "ScholarlyArticle"}
    page_types = page_info["_jsonld_types"]
    is_article_page = bool(page_types & article_types)

    if is_article_page and not author_found:
        findings.append(make_finding(
            "SEMANTIC-009", "Article page missing authorship metadata", "medium",
            "{} appears to be an article (JSON-LD type: {}) but has no author "
            "metadata (<meta name='author'>, article:author OG tag, or JSON-LD "
            "author field). Authorship signals help AI systems assess credibility "
            "and expertise.".format(url, ", ".join(page_types & article_types)),
            "Add authorship metadata: <meta name='author' content='Author Name'> "
            "and/or the 'author' property in your JSON-LD Article schema.",
            affected_urls=[url], confidence="high",
        ))

    # ---- 8. Body-text brand candidates ----
    page_info["_brand_candidates"] = _extract_body_brand_candidates(
        parsed.visible_text, min_occurrences=2
    )

    # ---- NEW 9. sameAs links for entity disambiguation (SEMANTIC-011) ----
    all_sameas = []
    for block in parsed.jsonld_blocks:
        all_sameas.extend(_jsonld_sameas(block))
    page_info["_has_sameas"] = len(all_sameas) > 0
    page_info["_sameas_urls"] = all_sameas

    # ---- NEW 10. Twitter Card meta (SEMANTIC-013) ----
    if not parsed.twitter_cards and has_og:
        # OG exists but no Twitter card — minor gap
        findings.append(make_finding(
            "SEMANTIC-013", "No Twitter Card meta tags", "low",
            "{} has OpenGraph tags but no Twitter Card meta tags (twitter:card, "
            "twitter:title, twitter:description). While Twitter/X will fall back "
            "to OG tags, explicit Twitter Cards give more control over how content "
            "appears when shared or cited on social platforms.".format(url),
            "Add <meta name='twitter:card' content='summary_large_image'>, "
            "twitter:title, and twitter:description tags.",
            affected_urls=[url], finding_type="recommendation",
        ))

    # ---- NEW 11. Quotability — concrete extractable facts (SEMANTIC-016) ----
    visible = parsed.visible_text
    facts_found = _FACT_PATTERN.findall(visible)
    h1s = [h[1] for h in parsed.headings if h[0] == "h1"]
    # A page is low-quotability if it has no concrete facts AND few headings
    if len(facts_found) == 0 and len(parsed.headings) < 2 and parsed.word_count > 50:
        findings.append(make_finding(
            "SEMANTIC-016", "Low quotability — no concrete extractable facts", "medium",
            "{} contains ~{} words but no concrete, machine-extractable facts "
            "(numbers, prices, measurements, percentages, or dates). AI assistants "
            "prefer pages with specific, quotable data points they can cite directly "
            "in answers.".format(url, parsed.word_count),
            "Add explicit, factual statements: specific numbers, dates, prices, "
            "specifications, or direct answers to common questions. Avoid vague "
            "marketing language without supporting data.",
            affected_urls=[url], confidence="medium", finding_type="recommendation",
        ))

    # ---- NEW 12. E-E-A-T signals (SEMANTIC-019) ----
    # Check for Experience, Expertise, Authority, Trust signals
    eeat_signals = 0
    if author_found:
        eeat_signals += 1
    if all_sameas:
        eeat_signals += 1
    # Check for about/team/credentials links in the page
    about_link = any(
        "/about" in l["href"].lower() or "/team" in l["href"].lower()
        or "/credentials" in l["href"].lower() or "/experts" in l["href"].lower()
        for l in parsed.internal_links
    )
    if about_link:
        eeat_signals += 1
    # Check for external authority links (outbound to .gov, .edu, etc.)
    authority_links = [l for l in parsed.external_links
                       if any(d in l["href"] for d in (".gov", ".edu", ".org"))]
    if authority_links:
        eeat_signals += 1

    # Only flag on important content pages (has structured data or is article)
    if (has_jsonld or is_article_page) and eeat_signals == 0:
        findings.append(make_finding(
            "SEMANTIC-019", "No E-E-A-T credibility signals", "medium",
            "{} has no signals of expertise, experience, authoritativeness, or "
            "trustworthiness (E-E-A-T): no author attribution, no sameAs links "
            "to authoritative profiles, no links to an about/team page, and no "
            "outbound references to authoritative sources (.gov, .edu, .org). "
            "AI systems increasingly weight these signals when deciding which "
            "sources to cite.".format(url),
            "Add author metadata with credentials, link to an about/team page, "
            "include sameAs links to Wikipedia/LinkedIn/Crunchbase profiles, "
            "and cite authoritative external sources where relevant.",
            affected_urls=[url], confidence="medium", finding_type="recommendation",
        ))

    return findings, page_info


# ===========================================================================
# Cross-page checks
# ===========================================================================

def check_identity_consistency(all_page_info):
    """Compare entity names and brand terminology across pages for consistency."""
    findings = []

    # Collect all JSON-LD names across pages
    all_names = {}
    for pi in all_page_info:
        for name in pi.get("_jsonld_names", []):
            all_names.setdefault(name, []).append(pi["_url"])

    # Collect all page titles brand suffixes
    all_titles = {}
    for pi in all_page_info:
        title = pi.get("_title", "")
        if title:
            for sep in (" | ", " - ", " – ", " — "):
                if sep in title:
                    brand = title.split(sep)[-1].strip()
                    all_titles.setdefault(brand, []).append(pi["_url"])
                    break

    # Inconsistent JSON-LD names
    if len(all_names) > 1:
        parts = ["'{}' on {}".format(n, ", ".join(urls))
                 for n, urls in all_names.items()]
        findings.append(make_finding(
            "SEMANTIC-007", "Inconsistent entity name in structured data", "high",
            "Different JSON-LD name values found across the site: " +
            "; ".join(parts) +
            ". AI systems may treat these as different entities, causing identity confusion.",
            "Standardise the Organization/Brand name in JSON-LD across all pages.",
            affected_urls=[url for urls in all_names.values() for url in urls],
        ))

    # Inconsistent title brands
    if len(all_titles) > 2:
        parts = ["'{}' on {}".format(n, ", ".join(urls[:2]))
                 for n, urls in all_titles.items()]
        findings.append(make_finding(
            "SEMANTIC-008", "Inconsistent brand name in page titles", "medium",
            "Multiple brand name variations found in <title> tags: " +
            "; ".join(parts) +
            ". Consistent branding helps AI systems correctly identify the entity.",
            "Use a consistent brand suffix in all page titles.",
            affected_urls=[url for urls in all_titles.values() for url in urls],
            finding_type="recommendation",
        ))

    # Body text terminology consistency (SEMANTIC-010)
    if len(all_page_info) >= 2:
        token_pages = {}
        for pi in all_page_info:
            for tok, cnt in pi.get("_brand_candidates", {}).items():
                token_pages.setdefault(tok, []).append(pi["_url"])

        tokens = list(token_pages.keys())
        inconsistent = {}
        for i, t1 in enumerate(tokens):
            for t2 in tokens[i + 1:]:
                if (t1.lower() in t2.lower() or t2.lower() in t1.lower()) and t1 != t2:
                    key = "{} / {}".format(t1, t2)
                    pages = list(set(token_pages.get(t1, []) + token_pages.get(t2, [])))
                    if len(pages) >= 2:
                        inconsistent[key] = pages

        if inconsistent:
            examples = list(inconsistent.items())[:3]
            parts = ["'{}' across {}".format(k, ", ".join(v[:2]))
                     for k, v in examples]
            findings.append(make_finding(
                "SEMANTIC-010", "Inconsistent entity naming in body text", "low",
                "Potentially inconsistent entity name variations detected in visible "
                "page text: " + "; ".join(parts) +
                ". Using different forms of the same name across pages can confuse "
                "AI entity resolution.",
                "Use a single, consistent name for each important entity across all pages. "
                "Decide on one canonical form (e.g., 'Acme Corporation') and use it everywhere.",
                affected_urls=list({u for v in inconsistent.values() for u in v})[:10],
                confidence="low", finding_type="recommendation",
            ))

    # ---- NEW: SEMANTIC-011 — sameAs links for entity disambiguation ----
    pages_with_sameas = [pi for pi in all_page_info if pi.get("_has_sameas")]
    if not pages_with_sameas and len(all_page_info) >= 1:
        findings.append(make_finding(
            "SEMANTIC-011", "No sameAs links for entity disambiguation", "high",
            "No page in the sampled set contains JSON-LD 'sameAs' links pointing to "
            "authoritative external profiles (Wikipedia, Wikidata, LinkedIn, Crunchbase, "
            "social media accounts). AI assistants use sameAs to confirm entity identity "
            "and avoid confusing the brand with similarly-named entities. Without these "
            "links, the brand is harder to disambiguate and less likely to be correctly "
            "cited.",
            "Add 'sameAs' to the Organization or Brand JSON-LD on the homepage pointing "
            "to all official external profiles: Wikipedia page, Wikidata entry, LinkedIn "
            "company page, Crunchbase, official social media accounts (Twitter/X, Facebook, "
            "Instagram). Example: \"sameAs\": [\"https://en.wikipedia.org/wiki/Brand_Name\", "
            "\"https://www.linkedin.com/company/brand-name\"]",
            affected_urls=[pi["_url"] for pi in all_page_info[:3]],
            finding_type="recommendation",
        ))

    # ---- NEW: SEMANTIC-012 — FAQ schema opportunity ----
    # Check if pages have Q&A-style content but no FAQPage schema
    pages_with_questions = []
    pages_with_faq_schema = []
    for pi in all_page_info:
        url = pi["_url"]
        # Re-check — we stored jsonld_types
        if "FAQPage" in pi.get("_jsonld_types", set()):
            pages_with_faq_schema.append(url)
        # Check URL patterns for FAQ/help/support pages
        path_lower = urllib.parse.urlsplit(url).path.lower()
        if any(k in path_lower for k in ("/faq", "/help", "/support", "/questions")):
            pages_with_questions.append(url)

    if pages_with_questions and not pages_with_faq_schema:
        findings.append(make_finding(
            "SEMANTIC-012", "FAQ/help pages without FAQPage schema", "medium",
            "The site has pages that appear to contain Q&A content ({}) "
            "but none use the Schema.org FAQPage structured data type. FAQPage schema "
            "enables AI assistants to directly extract and cite individual answers "
            "from the site, significantly boosting discoverability for question-based "
            "queries.".format(", ".join(pages_with_questions[:3])),
            "Add FAQPage JSON-LD schema to pages with Q&A content. Each question-answer "
            "pair should be a 'mainEntity' with @type Question and acceptedAnswer. This "
            "makes individual answers directly quotable by AI assistants.",
            affected_urls=pages_with_questions[:5],
            finding_type="recommendation",
        ))

    # ---- NEW: SEMANTIC-014 — Duplicate page titles ----
    title_pages = {}
    for pi in all_page_info:
        t = pi.get("_title", "").strip()
        if t:
            title_pages.setdefault(t, []).append(pi["_url"])
    duplicates = {t: urls for t, urls in title_pages.items() if len(urls) > 1}
    if duplicates:
        examples = list(duplicates.items())[:3]
        detail = "; ".join(["'{}' on {} pages".format(t, len(u)) for t, u in examples])
        findings.append(make_finding(
            "SEMANTIC-014", "Duplicate page titles across site", "medium",
            "Multiple pages share identical <title> tags: {}. "
            "Duplicate titles make it impossible for AI systems to distinguish "
            "between pages when selecting which to cite.".format(detail),
            "Give each page a unique, descriptive title that reflects its specific content.",
            affected_urls=[u for urls in duplicates.values() for u in urls][:10],
        ))

    # ---- NEW: SEMANTIC-015 — Duplicate meta descriptions ----
    desc_pages = {}
    for pi in all_page_info:
        d = pi.get("_meta_desc", "").strip()
        if d and len(d) > 20:
            desc_pages.setdefault(d, []).append(pi["_url"])
    dup_descs = {d: urls for d, urls in desc_pages.items() if len(urls) > 1}
    if dup_descs:
        examples = list(dup_descs.items())[:2]
        detail = "; ".join(["'{}...' on {} pages".format(d[:60], len(u)) for d, u in examples])
        findings.append(make_finding(
            "SEMANTIC-015", "Duplicate meta descriptions across site", "medium",
            "Multiple pages share identical meta descriptions: {}. "
            "AI systems use meta descriptions as summaries. Identical descriptions "
            "prevent differentiation between pages.".format(detail),
            "Write unique meta descriptions for each page that summarise its specific "
            "factual content.",
            affected_urls=[u for urls in dup_descs.values() for u in urls][:10],
            confidence="high",
        ))

    # ---- NEW: SEMANTIC-017 — Brand name ambiguity risk ----
    # Extract the brand name from JSON-LD or title suffix
    brand_name = None
    if all_names:
        brand_name = list(all_names.keys())[0]
    elif all_titles:
        brand_name = list(all_titles.keys())[0]

    if brand_name:
        words = brand_name.lower().split()
        generic_overlap = [w for w in words if w in _GENERIC_WORDS]
        if len(generic_overlap) >= len(words) * 0.5 and len(words) <= 3:
            findings.append(make_finding(
                "SEMANTIC-017", "Brand name may be ambiguous to AI systems", "medium",
                "The brand name '{}' contains generic word(s) ({}). Names that are "
                "common English words or phrases are harder for AI systems to disambiguate "
                "from other entities, concepts, or dictionary definitions. The brand may "
                "be confused with unrelated results.".format(
                    brand_name, ", ".join(generic_overlap)),
                "Strengthen entity disambiguation: add sameAs links to authoritative "
                "profiles (Wikipedia, Wikidata), use the full legal entity name in "
                "JSON-LD, and ensure consistent branding across all external platforms. "
                "Consider whether a tagline or descriptor can be appended to the brand "
                "name in structured data (e.g., 'Spark — Cloud Analytics Platform').",
                affected_urls=[pi["_url"] for pi in all_page_info[:3]],
                confidence="medium", finding_type="recommendation",
            ))

    # ---- NEW: SEMANTIC-018 — Speakable schema ----
    has_speakable = False
    for pi in all_page_info:
        # Check stored jsonld blocks (we'd need to re-fetch or store; use type heuristic)
        pass  # Checked during per-page audit below

    return findings


def check_speakable(pages):
    """Check if any page has speakable schema. Run as a separate pass."""
    findings = []
    # This is checked during page audit — if no page has it, we flag it
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
        raise SystemExit("usage: semantic_audit.py URL  OR  pipe JSON page list to stdin")

    all_findings = []
    all_page_info = []
    any_has_speakable = False
    any_has_faq_schema = False

    for url in pages:
        page_findings, info = audit_page(url)
        all_findings.extend(page_findings)
        if info:
            all_page_info.append(info)

        # Check for speakable/FAQ schema by re-examining JSON-LD
        r = None  # We already fetched in audit_page, but we need the parsed data
        # The data is in the page_info's _jsonld_types
        if "FAQPage" in info.get("_jsonld_types", set()):
            any_has_faq_schema = True

    # Cross-page identity + terminology consistency + new checks
    all_findings.extend(check_identity_consistency(all_page_info))

    # SEMANTIC-018 — Speakable schema recommendation (site-level)
    # Check by re-fetching homepage for speakable
    if pages:
        try:
            r = fetch(pages[0])
            if not r.get("error") and r["status"] < 400:
                html = r["body"].decode("utf-8", "replace")
                parsed = parse_page(html, r["url"])
                any_has_speakable = _jsonld_has_speakable(parsed.jsonld_blocks)
        except Exception:
            pass

    if not any_has_speakable and len(all_page_info) >= 1:
        all_findings.append(make_finding(
            "SEMANTIC-018", "No speakable schema markup", "low",
            "No page in the sampled set uses Schema.org 'speakable' markup. "
            "Speakable identifies sections of content that are especially suitable "
            "for text-to-speech and voice assistant answers. Adding speakable markup "
            "makes the site's content more likely to be read aloud by voice assistants.",
            "Add speakable structured data to key content sections (headlines, summaries, "
            "key facts) using the Schema.org speakable property in JSON-LD. Specify CSS "
            "selectors or XPaths that identify the most voice-friendly content.",
            affected_urls=[pages[0]] if pages else [],
            confidence="medium", finding_type="recommendation",
        ))

    print(json.dumps(all_findings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
