#!/usr/bin/env python3
"""Skill: offsite-discoverability-audit

Responsibility (and nothing else): **the signals that decide whether a model
can identify this brand against the rest of the web**, rather than whether it
can read this site.  Every other skill in the marketplace reasons about one
document; this one reasons about how that document anchors itself to the
wider web (handout appendices B, D and E).

It emits **recommendations only, never findings.**  That is a deliberate
evidence decision, not timidity.  A read-only crawl of one origin cannot
observe the wider web, so it cannot *prove* that a brand is uncorroborated -
only that the site publishes nothing a corroborating system could anchor to.
Turning an unobservable claim into a defect would break the marketplace's own
evidence contract (``lib/contracts.make_finding`` demands a reproducible
measurement), and would fire on healthy sites: ``site-010`` carries valid
Organization JSON-LD with no ``sameAs`` and is not defective.

So the absence of an anchor is reported as an opportunity, which is exactly
what the handout asks for under "suggestions may go beyond the detected
problems".
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

from lib.contracts import SiteSnapshot, SkillResult, recommendation


def _general(rec: dict) -> dict:
    """Mark advice that would apply to many sites, not just this one.

    The orchestrator ranks proactive advice by (specificity, effort) and caps
    the list, so advice grounded in a measurement about *this* site must not be
    evicted by cheaper general advice.  Everything this skill emits is general
    by nature: it is derived from what the site does *not* publish.
    """
    rec["rank_hint"] = 1
    return rec

SKILL_ID = "offsite-discoverability-audit"

# Identity providers a retrieval system can actually reconcile a brand against.
ANCHOR_HOSTS = (
    "wikipedia.org", "wikidata.org", "linkedin.com", "crunchbase.com",
    "github.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
    "youtube.com", "bloomberg.com", "opencorporates.com",
)
CORROBORATION_RE = re.compile(
    r"\b(press|newsroom|news|media kit|media|award|awards|recognition|"
    r"in the news|coverage|featured in|case stud|testimonial|customer stor)\w*\b", re.I
)
QA_RE = re.compile(r"\b(faq|frequently asked|q&a|common questions|questions we)\b", re.I)
CONTACT_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CONTACT_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,5}[\s.-]?\d{4,6}")
# High-leverage schema types for answer engines, beyond Organization/Product.
ANSWER_SCHEMA = ("FAQPage", "HowTo", "QAPage", "Speakable", "BreadcrumbList", "Article", "NewsArticle")
# A fact a model can quote: a number, a price, a percentage or a date.
QUOTABLE_RE = re.compile(
    r"(?:[$£€₹]\s?\d[\d,.]*|\b\d[\d,.]*\s?%|\b\d{4}\b|\b\d[\d,.]*\s?(?:users|customers|clients|"
    r"employees|countries|years|projects|offices)\b)", re.I
)


def _organization_objects(snapshot: SiteSnapshot) -> list[dict[str, Any]]:
    out = []
    for page in snapshot.ok_pages:
        for obj in page.jsonld_objects:
            types = obj.get("@type", "")
            types = [types] if isinstance(types, str) else (types if isinstance(types, list) else [])
            if any(str(t) in ("Organization", "Corporation", "LocalBusiness", "Brand", "NGO",
                              "EducationalOrganization", "GovernmentOrganization") for t in types):
                out.append(obj)
    return out


def _same_as_targets(orgs: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for obj in orgs:
        value = obj.get("sameAs")
        if isinstance(value, str):
            targets.append(value)
        elif isinstance(value, list):
            targets.extend(str(v) for v in value)
    return [t for t in targets if t.strip()]


def _external_anchor_links(snapshot: SiteSnapshot, host: str) -> list[str]:
    found = []
    for page in snapshot.ok_pages:
        for link in page.links:
            href = link.get("href", "")
            netloc = urlparse(href).netloc.lower()
            if netloc and netloc != host and any(a in netloc for a in ANCHOR_HOSTS):
                found.append(href)
    return found


def analyse(snapshot: SiteSnapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (recommendations, checks). Never returns findings - see module docstring."""
    host = urlparse(snapshot.entry_url).netloc.lower()
    pages = snapshot.ok_pages
    all_text = " ".join(p.content_text for p in pages)

    orgs = _organization_objects(snapshot)
    same_as = _same_as_targets(orgs)
    anchor_links = _external_anchor_links(snapshot, host)
    anchored = len(same_as) + len(anchor_links)

    schema_types = {str(t) for p in pages for t in p.schema_types}
    corroboration_hits = CORROBORATION_RE.findall(all_text)
    quotable_pages = [p for p in pages if QUOTABLE_RE.search(p.content_text)]
    has_qa = bool(QA_RE.search(all_text)) or "FAQPage" in schema_types
    has_email = bool(CONTACT_EMAIL_RE.search(all_text))
    has_phone = bool(CONTACT_PHONE_RE.search(all_text))

    recs: list[dict[str, Any]] = []

    if anchored == 0:
        recs.append(_general(recommendation(
            "Publish independently verifiable identity signals",
            "No sameAs property and no link to an external identity provider was found across "
            f"{len(pages)} crawled pages. An answer engine reconciling this brand has nothing to "
            "join it to. Add sameAs to the Organization JSON-LD pointing at the profiles that "
            "already exist (LinkedIn, Wikidata, Crunchbase, GitHub), so independent records "
            "resolve to one entity.",
            "entity_identity", effort="low")))
    elif orgs and not same_as and anchor_links:
        recs.append(_general(recommendation(
            "Move existing profile links into Organization sameAs",
            f"{len(anchor_links)} external profile link(s) appear in page markup but none are "
            "declared in the Organization JSON-LD sameAs array. A crawler reading only the "
            "structured data cannot see them. Mirror the links into sameAs.",
            "entity_identity", effort="low")))

    if not corroboration_hits:
        recs.append(_general(recommendation(
            "Add recent press releases, awards, or news",
            f"No press, newsroom, award or media-coverage surface was detected in {len(pages)} "
            "crawled pages. A claim that appears in only one place is treated as weaker than one "
            "repeated by independent sources. Publishing dated announcements gives external "
            "outlets something concrete to cite back, which is what builds corroboration.",
            "freshness", effort="medium")))

    if not has_qa:
        recs.append(_general(recommendation(
            "Add FAQ or question-and-answer content with FAQPage markup",
            "No question-and-answer content and no FAQPage structured data was found. Assistants "
            "answer questions, so prose already shaped as a direct question and a short, "
            "self-contained answer is far easier to quote verbatim than a paragraph a model has "
            "to summarise.",
            "structured_data", effort="medium")))

    missing_schema = [t for t in ANSWER_SCHEMA if t not in schema_types]
    if len(missing_schema) >= 5 and orgs:
        recs.append(_general(recommendation(
            "Extend structured data beyond Organization",
            f"Only {sorted(schema_types) or 'no'} schema type(s) are published; "
            f"{', '.join(missing_schema[:4])} are absent. Each additional type gives a retrieval "
            "system a different question it can answer from this site directly.",
            "structured_data", effort="medium")))

    if pages and len(quotable_pages) * 2 < len(pages):
        recs.append(_general(recommendation(
            "State concrete, quotable facts in page text",
            f"Only {len(quotable_pages)} of {len(pages)} crawled pages contain a specific figure, "
            "price, date or quantity. Pages that assert nothing measurable give a model nothing "
            "to quote, so they are rarely selected as a source even when they rank.",
            "content_extraction", effort="medium")))

    if not (has_email or has_phone):
        recs.append(_general(recommendation(
            "Publish contact details as readable text",
            f"No email address or phone number was found in the readable text of {len(pages)} "
            "pages. Contact details are a standard corroboration signal used to tell "
            "same-named organisations apart; when they exist only in an image or a script-built "
            "widget they cannot serve that purpose.",
            "content_extraction", effort="low")))

    checks = [
        {"check": "external_identity_anchors", "passed": anchored > 0, "value": anchored},
        {"check": "sameas_declared", "passed": bool(same_as), "value": len(same_as)},
        {"check": "corroboration_surface_present", "passed": bool(corroboration_hits),
         "value": len(corroboration_hits)},
        {"check": "direct_answer_content_present", "passed": has_qa, "value": has_qa},
        {"check": "quotable_pages", "value": f"{len(quotable_pages)}/{len(pages)}"},
        {"check": "contact_in_text", "passed": has_email or has_phone,
         "value": {"email": has_email, "phone": has_phone}},
        {"check": "answer_schema_types_present",
         "value": sorted(schema_types & set(ANSWER_SCHEMA))},
    ]
    return recs, checks


def run(context: dict[str, Any]) -> SkillResult:
    started = time.monotonic()
    result = SkillResult(skill=SKILL_ID)

    snapshot: SiteSnapshot | None = context.get("artifacts", {}).get("snapshot")
    if snapshot is None:
        result.error = "no snapshot in context"
        result.runtime_seconds = time.monotonic() - started
        return result
    if not snapshot.ok_pages:
        result.error = "no retrievable pages in snapshot"
        result.runtime_seconds = time.monotonic() - started
        return result

    recs, checks = analyse(snapshot)
    result.recommendations.extend(recs)
    result.checks = checks
    result.artifacts["offsite_profile"] = {c["check"]: c.get("value") for c in checks}
    result.runtime_seconds = time.monotonic() - started
    return result
