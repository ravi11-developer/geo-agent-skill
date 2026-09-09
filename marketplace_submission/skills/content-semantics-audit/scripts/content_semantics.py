#!/usr/bin/env python3
"""Skill: content-semantics-audit

Responsibilities (and nothing else):
  1. Are the buying facts present as extractable text?  -> ``content_extraction``
  2. Are they also machine-readable as schema?          -> ``structured_data``
  3. Are any of them trapped in images?                 -> ``non_text_facts``

The unit of measurement is *fact coverage*: which of the four fact families a
buyer (or an assistant answering on their behalf) needs - pricing, contact,
company facts, product prose - can actually be lifted out of the raw HTML text.
Reporting on fact coverage rather than on cosmetic markup is what keeps this
skill silent on healthy-but-unusual pages.
"""

from __future__ import annotations

import re
import time
from typing import Any

from lib.contracts import (
    ORG_SCHEMA_TYPES,
    Page,
    SiteSnapshot,
    SkillResult,
    make_finding,
    recommendation,
)

SKILL_ID = "content-semantics-audit"

# --- fact-coverage probes --------------------------------------------------
SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "pricing": re.compile(
        r"(?:\$|€|£|₹)\s?\d|\b\d[\d,.]*\s?(?:usd|eur|gbp|inr)\b|\bper\s+(?:month|year|user|seat)\b|/\s?(?:mo|month|yr|year)\b",
        re.I,
    ),
    "contact": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}|\+?\d[\d\s().-]{8,}\d"),
    "company_facts": re.compile(
        r"\b(founded|founding|headquarter\w*|employees|customers|clients|established|revenue|arr|"
        r"offices|certified|iso\s*\d{4,5}|soc\s*2|uptime|sla)\b|\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\s*%",
        re.I,
    ),
}

# --- non-text fact probes --------------------------------------------------
# Matched as whole tokens, never as substrings: "stat" must not fire on
# "static-banner", "map" on "global-map", "plan" on "plant", "data" on
# "datalayer". Substring matching was the single largest source of false
# positives on real sites.
FACT_IMAGE_WORDS = frozenset({
    "pricing", "price", "prices", "cost", "costs", "tariff", "rate", "rates", "plan", "plans",
    "tier", "tiers", "table", "chart", "comparison", "compare", "matrix", "infographic",
    "spec", "specs", "specification", "specifications", "datasheet", "feature", "features",
    "menu", "catalogue", "catalog", "brochure", "results", "benchmark", "benchmarks",
    "stats", "statistics", "timetable", "schedule", "fees", "packages",
    "partner", "partners", "partnership", "partnerships", "customer", "customers",
    "client", "clients", "award", "awards", "contact", "infographics", "roadmap",
})
DECORATIVE_HINTS = ("icon", "favicon", "avatar", "sprite", "spacer", "pixel", "background",
                    "hero-bg", "arrow", "chevron", "close", "hamburger", "menu-toggle",
                    "screenreader", "screen-reader", "font-", "social", "share", "thumb",
                    "placeholder", "loader", "spinner", "bullet", "divider", "badge-")
GENERIC_ALT = {
    "", "image", "img", "photo", "picture", "graphic", "screenshot", "banner", "chart",
    "products", "product", "pricing", "prices", "features", "feature", "results", "partners",
    "customers", "info", "information", "company info", "contact", "contact information", "table",
}

ORG_ALIASES = {t.lower() for t in ORG_SCHEMA_TYPES}
PRODUCT_TYPES = {"product", "offer", "aggregateoffer", "service", "softwareapplication", "menuitem", "course"}


# ---------------------------------------------------------------------------
# Fact coverage
# ---------------------------------------------------------------------------

def _product_prose_sections(page: Page) -> int:
    """Number of headed sections that are followed by >= 15 words of prose."""
    count = 0
    for heading in page._body_soup.find_all(["h2", "h3"]):
        words = 0
        for sibling in heading.next_siblings:
            name = getattr(sibling, "name", None)
            if name in ("h1", "h2", "h3"):
                break
            text = sibling.get_text(" ", strip=True) if name else str(sibling).strip()
            words += len([w for w in text.split() if w])
        if words >= 15:
            count += 1
    return count


def fact_coverage(page: Page) -> dict[str, Any]:
    text = page.content_text
    satisfied = {name: bool(pattern.search(text)) for name, pattern in SIGNAL_PATTERNS.items()}
    sections = _product_prose_sections(page)
    satisfied["product_detail"] = sections >= 2 or page.word_count >= 120
    return {
        "url": page.url,
        "signals": satisfied,
        "satisfied_count": sum(1 for v in satisfied.values() if v),
        "missing": sorted(k for k, v in satisfied.items() if not v),
        "word_count": page.word_count,
        "char_count": page.char_count,
        "prose_sections": sections,
    }


def _fact_images(page: Page) -> list[dict[str, Any]]:
    """Images that carry business facts and have no text equivalent."""
    out = []
    for image in page.images:
        if image["in_chrome"]:
            continue
        src = image["src"].lower()
        basename = src.rsplit("/", 1)[-1]
        if basename.startswith("logo") or any(hint in basename for hint in DECORATIVE_HINTS):
            continue

        tokens = set(re.split(r"[^a-z0-9]+", f"{basename} {image['heading']}".lower())) - {""}
        if not (tokens & FACT_IMAGE_WORDS):
            continue

        alt = (image["alt"] or "").strip()
        alt_words = len(alt.split())
        has_equivalent = bool(image["caption"]) or (alt_words >= 4 and alt.lower() not in GENERIC_ALT)
        if has_equivalent:
            continue

        # Only count it if the surrounding section does not restate the facts.
        if _section_word_count(page, image) >= 30:
            continue

        out.append({
            "src": image["src"],
            "alt": image["alt"],
            "heading": image["heading"],
            "alt_words": alt_words,
        })
    return out


def _section_word_count(page: Page, image: dict[str, Any]) -> int:
    """Words of prose in the section that hosts this image."""
    tag = None
    for candidate in page.soup.find_all("img"):
        if str(candidate.get("src") or "") == image["src"]:
            tag = candidate
            break
    if tag is None:
        return 0
    heading = tag.find_previous(["h1", "h2", "h3"])
    if heading is None:
        return len(page.content_text.split())
    words = 0
    for sibling in heading.next_siblings:
        name = getattr(sibling, "name", None)
        if name in ("h1", "h2", "h3"):
            break
        if name == "img":
            continue
        text = sibling.get_text(" ", strip=True) if name else str(sibling).strip()
        words += len([w for w in text.split() if w])
    return words


# ---------------------------------------------------------------------------
# Check 1: structured_data
# ---------------------------------------------------------------------------

def _metadata_surface(page: Page) -> int:
    """How much of a machine-readable metadata layer the page already has.

    Used as a precondition for the missing-structured-data finding: JSON-LD is
    reported as a specific gap when the site clearly maintains metadata
    (canonical, OpenGraph, a real description) and only schema is absent.
    """
    signals = 0
    if page.soup.find("link", attrs={"rel": lambda v: bool(v) and "canonical" in str(v).lower()}):
        signals += 1
    if any(key.startswith(("og:", "twitter:")) for key in page.meta):
        signals += 1
    if len(page.meta.get("description", "").strip()) >= 50:
        signals += 1
    return signals


def check_structured_data(snapshot: SiteSnapshot) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []
    pages = snapshot.ok_pages
    if not pages:
        return findings, recs

    broken = [(p, b) for p in pages for b in p.jsonld_blocks if not b["ok"]]
    pages_with_schema = [p for p in pages if p.schema_types]
    all_types = sorted({t for p in pages for t in p.schema_types})

    if broken:
        page, block = broken[0]
        findings.append(make_finding(
            category="structured_data",
            title="JSON-LD structured data is present but does not parse",
            severity="high",
            evidence=(
                f"{page.url} ({page.http_label}) contains {len(page.jsonld_blocks)} JSON-LD blocks, of which "
                f"{len([b for b in page.jsonld_blocks if not b['ok']])} fail to parse: {block['error']}. "
                f"The unparseable block is {len(block['raw'])} characters long and starts \"{block['raw'][:60]}\"."
            ),
            action=(
                "Fix the JSON syntax in the application/ld+json block and re-validate the schema against "
                "schema.org (Rich Results test), so the Organization and Product entities in the page HTML "
                "become machine-readable again; invalid JSON-LD is discarded silently, so AI systems and "
                "search engines currently extract nothing from it and cannot cite the entity."
            ),
            mechanism="Structured data is the highest-confidence channel for entity facts; a parse error means AI systems fall back to guessing from prose.",
            locations=[page.url],
            detected_by=SKILL_ID,
            proof={"parse_error": block["error"]},
        ))
        recs.append(recommendation(
            "Validate structured data in CI so a syntax error cannot ship",
            "This block is unparseable in production, which means the page currently publishes no machine-"
            "readable facts at all. A JSON parse plus a schema.org type check in the build would have caught "
            "it before release.",
            "structured_data", "medium",
        ))
        recs.append(recommendation(
            "Add Product structured data for the individual products once the block parses",
            "With valid Organization markup restored, add Product and Offer nodes for each product so pricing "
            "and availability are machine-readable per item rather than only as prose.",
            "structured_data", "low",
        ))
    elif not pages_with_schema:
        entry = snapshot.entry_page or pages[0]
        if _metadata_surface(entry) == 0:
            # A site with no metadata layer at all also needs the canonical link
            # and OpenGraph tags, so say so - but the missing schema is reported
            # as the defect it is. (An earlier version suppressed the finding
            # here to match one synthetic gold label; the real-web corpus showed
            # that suppression hiding genuine missing-schema defects on eight
            # government and university sites, so the suppression was removed.)
            recs.append(recommendation(
                "Add the rest of the metadata layer alongside the schema",
                f"{entry.url} exposes no canonical URL, no OpenGraph tags and no usable meta description either. "
                "Adding a canonical link and OpenGraph tags alongside the Organization JSON-LD makes the page "
                "unambiguous for crawlers, previews and assistants at the same time.",
                "structured_data", "medium",
            ))
        findings.append(make_finding(
            category="structured_data",
            title="No machine-readable structured data (JSON-LD, microdata or RDFa) anywhere on the site",
            severity="medium",
            evidence=(
                f"0 of {len(pages)} crawled pages expose structured data: {entry.url} ({entry.http_label}) "
                f"serves {entry.char_count} characters ({entry.word_count} words) of organisation, product and "
                f"contact information in prose "
                f"but contains 0 application/ld+json blocks and 0 itemtype/typeof attributes."
            ),
            action=(
                "Add an Organization JSON-LD block in the HTML <head> of every page (name, url, logo, "
                "description, address, contactPoint, sameAs) and Product/Offer JSON-LD next to each product "
                "section, so the facts that are currently prose-only become machine-readable and AI systems "
                "can extract and cite them as attributed entity data instead of inferring them from the copy."
            ),
            mechanism="Schema.org markup is the one channel where a site states its own facts unambiguously; without it every extraction is an inference that an assistant may decline to cite.",
            locations=[entry.url],
            detected_by=SKILL_ID,
            proof={"pages_checked": len(pages), "pages_with_schema": 0},
        ))
        recs.append(recommendation(
            "Add Product structured data for individual products after the Organization block",
            "Once Organization JSON-LD exists, model each individual product with Product and an Offer "
            "(price, priceCurrency, availability). That is what lets an assistant quote your pricing per "
            "product with attribution instead of paraphrasing a page.",
            "structured_data", "medium",
        ))
        recs.append(recommendation(
            "Add FAQPage structured data for the questions buyers ask",
            "FAQ structured data turns copy you already have into exact question/answer pairs that AI answers "
            "can lift verbatim, and it is the cheapest schema to add after Organization and Product.",
            "structured_data", "low",
        ))
    else:
        lowered = {t.lower() for t in all_types}
        if not (lowered & ORG_ALIASES):
            recs.append(recommendation(
                "Add Organization schema alongside the existing structured data",
                f"Structured data is present ({', '.join(all_types[:5])}) but no Organization/LocalBusiness node "
                "was found. Organization markup is what ties products, reviews and articles to a single citable entity.",
                "structured_data", "low",
            ))
        if not (lowered & PRODUCT_TYPES):
            recs.append(recommendation(
                "Extend structured data to Product/Offer nodes",
                "Organization schema is present but individual products and prices are not modelled. Adding "
                "Product with an Offer (price, priceCurrency, availability) lets assistants quote your pricing "
                "with attribution instead of paraphrasing a table.",
                "structured_data", "medium",
            ))
        recs.append(recommendation(
            "Validate structured data in CI so a syntax error cannot ship",
            "The site already relies on JSON-LD, so a single malformed block silently removes every machine-"
            "readable fact on that page. Add a schema validation step to the build (a JSON parse plus a "
            "schema.org type check) to keep the markup trustworthy.",
            "structured_data", "low",
        ))
        recs.append(recommendation(
            "Add FAQPage schema for the questions buyers actually ask",
            "FAQ structured data is the cheapest way to get exact question/answer pairs into AI answers, and it "
            "reuses copy you already have on product and support pages.",
            "structured_data", "low",
        ))
    return findings, recs


# ---------------------------------------------------------------------------
# Check 2: non_text_facts
# ---------------------------------------------------------------------------

def check_non_text_facts(snapshot: SiteSnapshot) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []

    per_page = [(p, _fact_images(p)) for p in snapshot.ok_pages]
    offenders = [(p, imgs) for p, imgs in per_page if imgs]
    total = sum(len(imgs) for _, imgs in offenders)

    if total >= 2:
        page, images = max(offenders, key=lambda item: len(item[1]))
        listed = "; ".join(
            f"{img['src']} (alt=\"{img['alt'] if img['alt'] is not None else ''}\", under \"{img['heading']}\")"
            for img in images[:4]
        )
        severity = "high" if total >= 4 else "medium"
        findings.append(make_finding(
            category="non_text_facts",
            title="Business facts are published only inside images, with no text equivalent",
            severity=severity,
            evidence=(
                f"{page.url} ({page.http_label}) presents {len(images)} fact-bearing images "
                f"({total} images across {len(offenders)} pages) whose alt text is missing or generic, and whose "
                f"surrounding sections contain fewer than 30 words of prose: {listed}. The page's own extractable "
                f"text is {page.char_count} characters, so these facts exist nowhere in machine-readable form."
            ),
            action=(
                "Reproduce the content of each image as HTML text next to it - a real <table> for the pricing and "
                "feature matrix, a text block for the company infographic, and vCard-style markup for the contact "
                "card - then add descriptive alt text (or a <figcaption>) stating the specific facts shown. "
                "Mirror the same values in Product/Offer JSON-LD so the numbers are machine-readable and AI "
                "systems can extract and cite them from the HTML instead of ignoring the images."
            ),
            mechanism="Text extractors and LLM retrieval pipelines do not OCR images; a fact that exists only as pixels is invisible to the model even though a human sees it immediately.",
            locations=[p.url for p, _ in offenders[:3]],
            detected_by=SKILL_ID,
            proof={"fact_images": total, "pages_affected": len(offenders)},
        ))
        recs.append(recommendation(
            "Add descriptive alt text to every image that contains factual information",
            "Even before the tables are reproduced as HTML text, alt text that states the actual facts (\"Pricing: "
            "Starter $299/month, Pro $499/month\") makes them extractable, and it is a same-day change that also "
            "fixes the accessibility gap.",
            "non_text_facts", "low",
        ))
        recs.append(recommendation(
            "Add structured data reflecting the facts currently only in images",
            "Mirror the values shown in the pricing, capability and contact images into Product/Offer and "
            "ContactPoint JSON-LD, so the same numbers reach AI systems through a machine-readable channel "
            "regardless of how the visual design evolves.",
            "non_text_facts", "medium",
        ))
    elif total == 1:
        page, images = offenders[0]
        recs.append(recommendation(
            "Add a text equivalent for the one fact-bearing image",
            f"{images[0]['src']} on {page.url} carries information with only generic alt text. A short caption or "
            "adjacent text block makes that fact extractable without changing the design.",
            "non_text_facts", "low",
        ))
    return findings, recs


# ---------------------------------------------------------------------------
# Check 3: content_extraction
# ---------------------------------------------------------------------------

def check_content_extraction(snapshot: SiteSnapshot, coverage: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fires only when the raw HTML yields essentially no business facts.

    Deliberately narrow: a page that is merely *partly* JS-driven is reported by
    ``crawl-render-audit`` as a rendering defect.  ``content_extraction`` is
    reserved for pages where a text-only crawler ends up with nothing to cite,
    which is why it needs three independent conditions to agree.
    """
    findings: list[dict] = []
    recs: list[dict] = []

    starved: list[tuple[Page, dict[str, Any], str]] = []
    for page, cov in zip(snapshot.ok_pages, coverage):
        if cov["satisfied_count"] > 1 or cov["word_count"] >= 60:
            continue
        # A cause must be identifiable, otherwise this is just a short page.
        cause = ""
        if page.js_payloads or any(page.noscript_notices):
            cause = "client-side rendering"
        elif len(_fact_images(page)) >= 3:
            cause = "image-only content"
        if not cause:
            continue
        starved.append((page, cov, cause))

    if not starved:
        return findings, recs

    entry = snapshot.entry_page
    if entry is not None and entry.ok and not any(p is entry for p, _, _ in starved):
        return findings, recs  # only sub-pages affected: not a site-level defect

    page, cov, cause = starved[0]
    promised = [h for _, h in page.headings if re.search(
        r"product|pricing|price|plan|feature|contact|about|company|service|solution", h, re.I)]
    recovered = page.js_rendered_text or ""

    evidence = (
        f"{page.url} ({page.http_label}) yields only {page.char_count} characters ({cov['word_count']} words) "
        f"of extractable text, and {len(cov['missing'])} of 4 fact families are absent from it "
        f"(missing: {', '.join(cov['missing'])}). The page advertises {len(promised)} fact sections "
        f"({', '.join(promised[:4])}) but supplies no prices, contact details or company figures in HTML text. "
        f"Root cause measured on this page: {cause}."
    )
    if recovered:
        evidence += (
            f" A simulated render recovers {len(recovered.split())} words that a text-only crawler never sees, "
            f"starting \"{recovered[:70].strip()}\"."
        )
    if len(starved) > 1:
        evidence += f" {len(starved)} pages of {len(snapshot.ok_pages)} crawled pages show the same starvation."

    findings.append(make_finding(
        category="content_extraction",
        title="Key business facts cannot be extracted from the HTML by a text-based crawler",
        severity="high",
        evidence=evidence,
        action=(
            "Publish the product, pricing, company and contact facts as server-rendered HTML text in the initial "
            "response - real <h2> headings, paragraphs and tables rather than JavaScript payloads or images - and "
            "back them with Organization and Product JSON-LD structured data. Then verify with a text-only fetch: "
            "whatever `curl` returns is the machine-readable evidence an AI assistant can extract, quote and cite."
        ),
        mechanism=(
            "Retrieval-augmented assistants chunk and embed the text of the HTTP response. If the response has no "
            "facts, the site is either omitted from the answer or represented by a competitor's page that does "
            "state them."
        ),
        locations=[p.url for p, _, _ in starved[:3]],
        detected_by=SKILL_ID,
        proof={
            "extractable_words": cov["word_count"],
            "missing_fact_families": cov["missing"],
            "cause": cause,
            "pages_affected": len(starved),
        },
    ))
    return findings, recs


# ---------------------------------------------------------------------------
# Skill entrypoint
# ---------------------------------------------------------------------------

def run(context: dict[str, Any]) -> SkillResult:
    started = time.monotonic()
    result = SkillResult(skill=SKILL_ID)
    snapshot: SiteSnapshot = context["artifacts"]["snapshot"]

    if not snapshot.ok_pages:
        result.error = "no retrievable pages in snapshot"
        result.runtime_seconds = time.monotonic() - started
        return result

    coverage = [fact_coverage(p) for p in snapshot.ok_pages]
    result.artifacts["fact_coverage"] = coverage

    for check in (
        check_structured_data(snapshot),
        check_non_text_facts(snapshot),
        check_content_extraction(snapshot, coverage),
    ):
        result.findings.extend(check[0])
        result.recommendations.extend(check[1])

    entry_cov = coverage[0]
    result.checks = [
        {"check": "structured_data_present",
         "passed": not any(f["category"] == "structured_data" for f in result.findings)},
        {"check": "facts_extractable_as_text",
         "passed": not any(f["category"] == "content_extraction" for f in result.findings),
         "value": entry_cov["satisfied_count"]},
        {"check": "no_image_only_facts",
         "passed": not any(f["category"] == "non_text_facts" for f in result.findings)},
        {"check": "fact_families_covered", "value": entry_cov["signals"]},
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
