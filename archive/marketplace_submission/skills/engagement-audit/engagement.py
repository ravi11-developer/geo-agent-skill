#!/usr/bin/env python3
"""Skill: engagement-audit

Responsibility (and nothing else): on-site orientation and continuation.
Category emitted: ``engagement``.

A visitor - or an agent that followed a citation into a deep page - needs to
know where they are and where to go next.  This skill measures the navigation
landmarks and the internal link graph, and raises a defect only when those
affordances are absent altogether.  Cosmetic SEO habits (generic anchor text,
underscore URLs, several H1s) are reported as proactive advice, never as
defects, because they do not stop a human or a model from continuing.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from lib.contracts import Page, SiteSnapshot, SkillResult, make_finding, recommendation

SKILL_ID = "engagement-audit"

GENERIC_ANCHORS = {"click here", "read more", "learn more", "here", "more", "link", "this page"}
BREADCRUMB_HINTS = ("breadcrumb", "breadcrumbs")
CTA_RE = re.compile(
    r"\b(contact|get in touch|book a|request a|start (?:free|your)|sign up|talk to|demo|quote|pricing)\b", re.I
)


def page_navigation(page: Page, host: str) -> dict[str, Any]:
    soup = page.soup
    nav_landmarks = len(soup.find_all("nav")) + len(soup.find_all(attrs={"role": "navigation"}))

    internal, external, generic = set(), set(), []
    for link in page.links:
        href = link["href"]
        if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(page.url, href).split("#")[0]
        if urlparse(absolute).netloc == host:
            internal.add(absolute)
        else:
            external.add(absolute)
        if link["text"].strip().lower().rstrip(".!") in GENERIC_ANCHORS:
            generic.append(link["text"].strip())

    breadcrumbs = bool(
        soup.find(attrs={"aria-label": re.compile("|".join(BREADCRUMB_HINTS), re.I)})
        or soup.find(class_=re.compile("|".join(BREADCRUMB_HINTS), re.I))
        or any("breadcrumb" in str(t).lower() for t in page.schema_types)
    )

    return {
        "url": page.url,
        "nav_landmarks": nav_landmarks,
        "internal_links": len(internal),
        "external_links": len(external),
        "has_header": bool(soup.find("header")),
        "has_footer": bool(soup.find("footer")),
        "breadcrumbs": breadcrumbs,
        "generic_anchors": generic,
        "has_cta": bool(CTA_RE.search(page.content_text)),
        "js_shell": page.is_js_shell,
        "chars": page.char_count,
        "words": page.word_count,
    }


def check_engagement(snapshot: SiteSnapshot, profiles: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []
    if not profiles:
        return findings, recs

    # Pages whose DOM is built entirely by JavaScript cannot be judged on
    # navigation: the menu may well exist after hydration. crawl-render-audit
    # already reports that as a rendering defect, so this skill abstains rather
    # than reporting a second, unverifiable defect for the same root cause.
    measurable = [p for p in profiles if not p["js_shell"]]
    if not measurable:
        recs.append(recommendation(
            "Re-check navigation once the initial HTML is server-rendered",
            "Every crawled page is a JavaScript shell, so navigation landmarks and internal links could not be "
            "measured from the HTML response. Fix the rendering gap first, then re-run this audit; crawlers "
            "build their link graph from the served HTML, so client-side-only navigation is invisible to them.",
            "engagement", "medium",
        ))
        return findings, recs

    stranded = [p for p in measurable if p["nav_landmarks"] == 0 and p["internal_links"] < 2]
    entry_profile = measurable[0]

    if len(stranded) == len(measurable) and entry_profile in stranded:
        worst = entry_profile
        landmarks = sum(1 for key in ("has_header", "has_footer", "breadcrumbs") if worst[key])
        findings.append(make_finding(
            category="engagement",
            title="The site offers no navigation or internal linking for orientation",
            severity="high",
            evidence=(
                f"{worst['url']} (HTTP 200) serves {worst['chars']} characters of content but contains "
                f"0 <nav> landmarks, {worst['internal_links']} internal links and "
                f"{worst['external_links']} external links; header/footer/breadcrumb landmarks present: "
                f"{landmarks} of 3. The same pattern holds on {len(stranded)} of {len(measurable)} measurable pages, "
                f"so every page is a dead end for a visitor who arrives from a search result or an AI citation."
            ),
            action=(
                "Add a persistent header <nav> linking Home, About, Products, Pricing and Contact on every HTML "
                "page, a footer with the same site-wide links plus policy pages, and breadcrumbs marked up as "
                "machine-readable BreadcrumbList JSON-LD on sub-pages. Cross-link related products and articles "
                "in body copy so visitors and AI crawlers can discover, extract and cite the rest of the site "
                "from any entry point."
            ),
            mechanism=(
                "Internal links are how crawlers discover pages and how AI systems infer site structure and topical "
                "relationships; without them each page is an isolated document, and a visitor who lands mid-site "
                "has no path to a conversion step."
            ),
            locations=[p["url"] for p in stranded[:3]],
            detected_by=SKILL_ID,
            proof={"pages_without_navigation": len(stranded), "pages_checked": len(measurable)},
        ))
        recs.append(recommendation(
            "Add breadcrumb navigation for user and bot orientation",
            "Breadcrumbs marked up as BreadcrumbList tell a visitor who arrived from an AI citation where they "
            "are, and give crawlers an explicit hierarchy to follow.",
            "engagement", "low",
        ))
        recs.append(recommendation(
            "Add internal links between related content pages",
            "Beyond the menu, link products to their pricing and documentation from inside the body copy. The "
            "internal link graph is how crawlers discover pages and how AI systems infer which topics belong "
            "together.",
            "engagement", "medium",
        ))
        return findings, recs

    # --- proactive-only observations ------------------------------------
    profiles = measurable
    if not any(p["breadcrumbs"] for p in profiles) and len(profiles) > 1:
        recs.append(recommendation(
            "Add breadcrumb navigation with BreadcrumbList markup",
            "No breadcrumbs were found. Breadcrumbs give a visitor arriving from an AI citation immediate context "
            "about where they are, and give crawlers an explicit hierarchy for the site.",
            "engagement", "low",
        ))
    if not any(p["has_footer"] for p in profiles):
        recs.append(recommendation(
            "Add a site-wide footer with secondary navigation",
            "No <footer> landmark was found. A footer carrying company identity, contact details and links to "
            "policy pages is a low-effort orientation anchor that also strengthens internal linking.",
            "engagement", "low",
        ))
    if not any(p["has_cta"] for p in profiles):
        recs.append(recommendation(
            "Give every page an explicit next step",
            "No contact, demo or pricing call-to-action was detected in the body copy. A single clear next step "
            "converts the attention that an AI citation sends to the page.",
            "engagement", "medium",
        ))
    generic = [text for p in profiles for text in p["generic_anchors"]]
    if len(generic) >= 2:
        recs.append(recommendation(
            "Make internal anchor text descriptive",
            f"{len(generic)} internal links use generic anchor text (e.g. \"{generic[0]}\"). Descriptive anchors "
            "such as 'AcmeKube pricing' tell both readers and models what the destination covers. This is a "
            "refinement, not a defect: the links themselves work.",
            "engagement", "low",
        ))
    return findings, recs


def run(context: dict[str, Any]) -> SkillResult:
    started = time.monotonic()
    result = SkillResult(skill=SKILL_ID)
    snapshot: SiteSnapshot = context["artifacts"]["snapshot"]

    if not snapshot.ok_pages:
        result.error = "no retrievable pages in snapshot"
        result.runtime_seconds = time.monotonic() - started
        return result

    host = urlparse(snapshot.entry_url).netloc
    profiles = [page_navigation(p, host) for p in snapshot.ok_pages]
    result.artifacts["navigation_profile"] = profiles

    findings, recs = check_engagement(snapshot, profiles)
    result.findings.extend(findings)
    result.recommendations.extend(recs)
    result.checks = [
        {"check": "navigation_landmarks_present", "passed": not findings,
         "value": profiles[0]["nav_landmarks"]},
        {"check": "internal_links_present", "value": profiles[0]["internal_links"]},
        {"check": "breadcrumbs_present", "value": any(p["breadcrumbs"] for p in profiles)},
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
