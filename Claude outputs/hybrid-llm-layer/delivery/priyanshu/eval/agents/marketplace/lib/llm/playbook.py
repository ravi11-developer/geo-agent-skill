#!/usr/bin/env python3
"""Local remediation playbook.

Short, mechanism-first excerpts per finding category and per semantic aspect.
They are handed to the suggestion prompt as *verified context* so the model
grounds its advice in this project's house guidance instead of improvising
generic SEO folklore, and so the output stays consistent between runs and
between providers.

Everything here is local data: no network call, no external service, and the
whole file ships inside the marketplace package.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Deterministic categories
# ---------------------------------------------------------------------------

CATEGORY_PLAYBOOK: dict[str, dict[str, Any]] = {
    "crawlability": {
        "mechanism": "Retrieval is the first stage of every AI answer pipeline. A URL that "
                     "answers non-200, is disallowed in robots.txt, or carries noindex is "
                     "never added to the candidate set, so nothing downstream can cite it.",
        "levers": [
            "return 200 with a server-rendered body on the canonical URL",
            "scope Disallow rules to private and transactional paths only",
            "keep AI user agents (GPTBot, ClaudeBot, PerplexityBot, Google-Extended) unblocked on public paths",
            "remove noindex from pages that are meant to be answerable",
            "exempt crawler user agents from WAF or bot rules that answer 4xx",
        ],
        "owner": "engineering",
        "acceptance": "curl -sI the URL as each AI user agent and confirm 200 plus an indexable robots directive",
    },
    "rendering": {
        "mechanism": "Most retrieval pipelines index the raw HTTP response. Content assembled "
                     "by client-side JavaScript after load is invisible to them even though a "
                     "browser shows it.",
        "levers": [
            "server-side render or statically pre-render the pages that carry business facts",
            "ship the primary copy in the initial HTML and hydrate on top of it",
            "keep the JS-only layer for interaction, not for facts",
            "verify with JavaScript disabled, or by reading the raw response body",
        ],
        "owner": "engineering",
        "acceptance": "view-source (not devtools) on the page shows the key facts as text",
    },
    "content_extraction": {
        "mechanism": "An answer engine quotes short, self-contained statements. Facts that are "
                     "implied, spread across a long block, or absent as text cannot be quoted, "
                     "so the page loses to a competitor that states them plainly.",
        "levers": [
            "state each fact family (what it is, who it is for, what it costs, where it ships, how to get support) in plain sentences",
            "give each fact its own heading or list item so it can be extracted independently",
            "put the answer in the first sentence under the heading, not in the last paragraph",
        ],
        "owner": "content",
        "acceptance": "each fact can be copied out as one sentence without needing surrounding context",
    },
    "structured_data": {
        "mechanism": "JSON-LD is the machine-readable copy of the page's facts. Missing or "
                     "invalid markup forces every consumer back to text heuristics, which are "
                     "lossier and disagree with each other.",
        "levers": [
            "add Organization on the home page and Product/Offer or Service on detail pages",
            "fix parse errors: one invalid block discards the whole script element",
            "keep the markup consistent with the visible copy",
            "add sameAs links to authoritative profiles so the entity graph joins up",
        ],
        "owner": "engineering",
        "acceptance": "the JSON-LD parses and its fields match the rendered copy field by field",
    },
    "non_text_facts": {
        "mechanism": "Text inside an image is invisible to text-based retrieval. A price, a "
                     "specification table or a delivery window that only exists in a graphic is "
                     "a fact the site cannot be cited for.",
        "levers": [
            "repeat the fact as HTML text next to the image",
            "use descriptive alt text that carries the fact, not the file name",
            "convert specification graphics into real tables",
        ],
        "owner": "content",
        "acceptance": "with images blocked, the page still states every fact",
    },
    "freshness": {
        "mechanism": "Assistants weight recency. Undated claims and visibly stale dates both "
                     "reduce the confidence a system has in repeating a fact.",
        "levers": [
            "date factual claims with a visible and machine-readable dateModified",
            "refresh or retire figures that name a past year",
            "avoid a copyright year as the only freshness signal",
        ],
        "owner": "content",
        "acceptance": "each factual claim carries a date that matches its dateModified",
    },
    "entity_identity": {
        "mechanism": "Systems merge facts by entity. When a brand appears under several names "
                     "across title, JSON-LD, OpenGraph and body copy, the facts split across "
                     "several weak entities instead of accumulating on one strong one.",
        "levers": [
            "choose one canonical legal or trading name and use it verbatim everywhere",
            "make Organization.name in JSON-LD identical to the visible name",
            "add sameAs links to authoritative external profiles",
        ],
        "owner": "marketing",
        "acceptance": "title, H1, JSON-LD name, OpenGraph site_name and the footer all read the same string",
    },
    "engagement": {
        "mechanism": "Internal links are how crawlers discover pages and how a visitor who "
                     "arrived from a citation continues. Without them each page is a dead end.",
        "levers": [
            "persistent header navigation on every page",
            "footer with site-wide and policy links",
            "BreadcrumbList markup on sub-pages",
            "in-copy cross-links between related products, pricing and documentation",
        ],
        "owner": "design",
        "acceptance": "from any page, Home / About / Products / Pricing / Contact are reachable in one click",
    },
}

# ---------------------------------------------------------------------------
# Semantic aspects
# ---------------------------------------------------------------------------

ASPECT_PLAYBOOK: dict[str, dict[str, Any]] = {
    "value_proposition": {
        "lever": "say what it is, who it is for and what changes for them, in the first two sentences",
        "acceptance": "a reader can restate the offer in one sentence without scrolling",
    },
    "product_clarity": {
        "lever": "separate what the product does from what it is made of and who it suits",
        "acceptance": "each attribute is extractable on its own",
    },
    "pricing_transparency": {
        "lever": "state the number, the unit, the billing period and what is excluded",
        "acceptance": "the total cost of one common case can be computed from the page",
    },
    "shipping": {
        "lever": "give the destination, the window, the cost and the cut-off explicitly",
        "acceptance": "a buyer can tell whether their order arrives in time",
    },
    "returns": {
        "lever": "separate eligibility, deadline, exclusions and the request action",
        "acceptance": "each condition and the next action can be extracted independently",
    },
    "support": {
        "lever": "name the channel, the hours and the expected response time",
        "acceptance": "a visitor knows how and when they will get an answer",
    },
    "trust_credibility": {
        "lever": "attach evidence to each claim: a certificate, a date, a named source",
        "acceptance": "no superlative stands without something checkable next to it",
    },
    "cta_clarity": {
        "lever": "name the outcome of the click, not the gesture",
        "acceptance": "the label alone tells the reader what happens next",
    },
    "forms_validation": {
        "lever": "label every field and say what a valid value looks like before submission",
        "acceptance": "a first-time visitor can complete the form without a failed attempt",
    },
    "error_messages": {
        "lever": "describe what happened and the recovery step; never blame the visitor",
        "acceptance": "the message names an action the reader can take right now",
    },
    "navigation": {
        "lever": "keep a stable primary menu and cross-link related pages in the copy",
        "acceptance": "no page is a dead end from a cold entry",
    },
    "reviews_testimonials": {
        "lever": "attribute quotes and date them; mark them up as machine-readable Review",
        "acceptance": "each quote has an author and a date",
    },
    "tone_consistency": {
        "lever": "keep the voice of support and policy pages continuous with the sales pages",
        "acceptance": "a reader moving from a product page to a returns page notices no change of speaker",
    },
    "product_quality": {
        "lever": "back quality claims with specifications, materials or test results",
        "acceptance": "a quality claim is traceable to a stated fact",
    },
    "error_recovery": {
        "lever": "always offer the next step alongside the failure",
        "acceptance": "a dead end never appears without an exit",
    },
}


def excerpt_for_category(category: str) -> dict[str, Any]:
    """Playbook excerpt for one deterministic category (never raises)."""
    entry = CATEGORY_PLAYBOOK.get(category)
    if not entry:
        return {"mechanism": "", "levers": [], "owner": "engineering", "acceptance": ""}
    return dict(entry)


def excerpt_for_aspect(aspect: str) -> dict[str, Any]:
    return dict(ASPECT_PLAYBOOK.get(aspect, {"lever": "", "acceptance": ""}))


def owners() -> tuple[str, ...]:
    return ("content", "engineering", "design", "marketing", "seo")
