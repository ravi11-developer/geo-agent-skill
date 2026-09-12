#!/usr/bin/env python3
"""Build the evidence pack the semantic analyser is allowed to see.

Python stays authoritative for every fact.  This module turns a
:class:`~lib.contracts.SiteSnapshot` into a :class:`~lib.contracts.EvidencePack`:
normalised, boilerplate-free text sections, each with a stable ``evidence_id``,
a CSS-ish selector and a source classification.  Raw HTML never leaves the
deterministic side of the system.

What is deliberately removed before anything is serialised:

* scripts, styles, comments, templates, inline SVG and tracking markup;
* cookie / consent banners;
* navigation, header and footer chrome;
* blocks that repeat across pages (site-wide boilerplate);
* near-duplicate sections within a page;
* anything that survives the credential and PII scan in :mod:`lib.llm.redaction`.

Hidden content is dropped too, unless the fact that it is hidden is itself the
evidence - in which case the section is kept and flagged ``hidden=True``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from ..contracts import (
    EvidencePack,
    EvidenceSection,
    Page,
    PageEvidence,
    SiteSnapshot,
)
from .cache import snapshot_hash
from .config import CrawlBudget
from .redaction import redact_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DROP_TAGS = ("script", "style", "template", "svg", "noscript", "iframe", "canvas", "form")
CHROME_TAGS = ("nav", "header", "footer")

CONSENT_RE = re.compile(
    r"(cookie|consent|gdpr|ccpa|privacy[-_ ]?(banner|notice)|onetrust|cookiebot|truste)", re.I)
CONSENT_TEXT_RE = re.compile(
    r"(we use cookies|accept all cookies|manage (your )?(cookie|consent) preferences|"
    r"this (site|website) uses cookies|by continuing to browse)", re.I)
TRACKING_RE = re.compile(r"(gtm|googletagmanager|analytics|pixel|hotjar|clarity|fbq|doubleclick)", re.I)

CUSTOMER_VOICE_RE = re.compile(
    r"(review|testimonial|rating|customer[-_ ]?(say|story|voice|feedback)|what our|"
    r"trustpilot|verified buyer|q\s*&\s*a)", re.I)
SYSTEM_MESSAGE_RE = re.compile(
    r"(error|invalid|required field|please enter|try again|not found|failed|"
    r"we could(?:n't| not)|something went wrong)", re.I)

# Page-type classification from URL path, checked in order (first match wins).
PAGE_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pricing", re.compile(r"/(pricing|plans?|packages?|tariff|rate-?card)(/|$|\.)", re.I)),
    ("shipping_returns", re.compile(r"/(shipping|delivery|returns?|refunds?|exchange|cancellation)(/|$|\.)", re.I)),
    ("faq", re.compile(r"/(faq|faqs|help-?cent(er|re)|questions)(/|$|\.)", re.I)),
    ("support", re.compile(r"/(support|help|service|customer-?care|troubleshoot)(/|$|\.)", re.I)),
    ("contact", re.compile(r"/(contact|reach-?us|get-?in-?touch|locations?|stores?)(/|$|\.)", re.I)),
    ("about", re.compile(r"/(about|who-we-are|our-story|company|team|mission)(/|$|\.)", re.I)),
    ("legal", re.compile(r"/(privacy|terms|legal|cookie-?policy|disclaimer|imprint)(/|$|\.)", re.I)),
    ("blog", re.compile(r"/(blog|news|articles?|insights?|press|stories|resources?)(/|$|\.)", re.I)),
    ("product", re.compile(r"/(products?|item|sku|shop/[^/]+/[^/]|p/|dp/)(/|$|\.|[^/]+$)", re.I)),
    ("category", re.compile(r"/(category|categories|collections?|catalog|shop|services?|solutions?)(/|$|\.)", re.I)),
)

# Ranking weights for semantic page selection.
PAGE_TYPE_VALUE = {
    "home": 6.0, "pricing": 5.5, "shipping_returns": 5.0, "returns": 5.0,
    "faq": 4.5, "support": 4.5, "product": 4.0, "about": 3.5, "contact": 3.0,
    "category": 3.0, "blog": 2.0, "landing": 2.0, "legal": 0.5, "other": 1.0,
}
# How many pages of each type are worth analysing before the marginal value drops.
PAGE_TYPE_QUOTA = {
    "home": 1, "about": 1, "contact": 1, "pricing": 1, "category": 3, "product": 6,
    "faq": 2, "shipping_returns": 3, "support": 3, "blog": 3, "legal": 1,
    "landing": 3, "other": 3,
}

MIN_SECTION_CHARS = 120
MAX_SECTION_CHARS = 3200        # ~800 tokens
MERGE_TARGET_CHARS = 1200       # ~300 tokens
MAX_SECTIONS_PER_PAGE = 12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _text_digest(text: str) -> str:
    return hashlib.sha256(_norm_ws(text).lower().encode("utf-8", "replace")).hexdigest()[:16]


def _attrs(tag: Any) -> dict:
    """Attributes of a node, for any node type BeautifulSoup can hand back.

    ``find_all(True)`` also yields doctypes and processing instructions, whose
    ``attrs`` is ``None``.  Real-world HTML hits this within the first few
    hundred pages, and building an evidence pack must never be what breaks an
    audit, so every attribute access below goes through here.
    """
    attrs = getattr(tag, "attrs", None)
    return attrs if isinstance(attrs, dict) else {}


def _is_hidden(tag: Any) -> bool:
    attrs = _attrs(tag)
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    style = str(attrs.get("style", "")).lower().replace(" ", "")
    return "display:none" in style or "visibility:hidden" in style


def _attr_blob(tag: Any) -> str:
    attrs = _attrs(tag)
    parts = [str(attrs.get("id") or "")]
    classes = attrs.get("class") or []
    parts.extend(str(c) for c in (classes if isinstance(classes, list) else [classes]))
    parts.append(str(attrs.get("data-testid") or ""))
    parts.append(str(attrs.get("role") or ""))
    return " ".join(parts)


def _selector_for(tag: Any) -> str:
    """Short, human-checkable selector.  Not guaranteed unique - it is a pointer
    for a reviewer, and the validator checks it exists rather than resolving it."""
    if tag is None:
        return "body"
    name = str(getattr(tag, "name", "") or "body")
    attrs = _attrs(tag)
    ident = attrs.get("id")
    if ident:
        return f"{name}#{ident}"
    classes = attrs.get("class") or []
    if classes:
        first = str(classes[0] if isinstance(classes, list) else classes)
        if first:
            return f"{name}.{first}"
    return name


def _language_of(page: Page) -> str:
    """Best available language hint, defaulting to English rather than failing."""
    declared = page.meta.get("content-language")
    if not declared:
        try:
            html_tag = page.soup.find("html")
            declared = _attrs(html_tag).get("lang") if html_tag is not None else None
        except Exception:  # noqa: BLE001 - a language hint is never worth an exception
            declared = None
    return str(declared or "en").split("-")[0][:8] or "en"


def classify_source_kind(heading: str, text: str, selector: str) -> str:
    blob = f"{heading} {selector}"
    if CUSTOMER_VOICE_RE.search(blob):
        return "customer_voice"
    if SYSTEM_MESSAGE_RE.search(heading) or (len(text) < 200 and SYSTEM_MESSAGE_RE.search(text)):
        return "system_message"
    return "brand_copy"


def classify_page(page: Page, entry_url: str) -> str:
    """Deterministic page-type classification.

    URL first (cheapest and most reliable), then title, then DOM signals.  The
    LLM is only consulted for pages this returns ``other`` for, and only when
    ambiguous-page classification is switched on - it never overrides a
    confident deterministic answer.
    """
    path = urlparse(page.url).path or "/"
    entry_path = urlparse(entry_url).path or "/"
    if path.rstrip("/") in ("", entry_path.rstrip("/")) and page.is_entry:
        return "home"
    for page_type, pattern in PAGE_TYPE_PATTERNS:
        if pattern.search(path):
            return page_type
    title = (page.title or "").lower()
    for page_type, pattern in PAGE_TYPE_PATTERNS:
        if pattern.search("/" + title.replace(" ", "-")):
            return page_type
    if any("product" in str(t).lower() or "offer" in str(t).lower() for t in page.schema_types):
        return "product"
    if any("faqpage" in str(t).lower() for t in page.schema_types):
        return "faq"
    if path.rstrip("/") == "":
        return "home"
    return "other"


def template_id(page: Page) -> str:
    """Structural fingerprint used to spot duplicate templates.

    Built from the tag skeleton and heading shape rather than the copy, so two
    product pages with different words but the same layout collapse to one
    template and only a couple of samples are analysed.
    """
    soup = page.soup
    skeleton: list[str] = []
    for tag in soup.find_all(["section", "article", "main", "aside", "ul", "ol", "table", "h1", "h2", "h3", "form"]):
        classes = tag.get("class") or []
        first = str(classes[0]) if classes else ""
        skeleton.append(f"{tag.name}.{first}")
    heading_shape = "/".join(level for level, _ in page.headings[:12])
    return hashlib.sha256(("|".join(skeleton[:60]) + "#" + heading_shape).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

def _content_root(page: Page) -> Any:
    """Strip everything that is not page substance and return the root node."""
    soup = BeautifulSoup(page.html or "", "html.parser")

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(list(DROP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(CHROME_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        blob = _attr_blob(tag)
        if CONSENT_RE.search(blob) or TRACKING_RE.search(blob):
            tag.decompose()
            continue
        if _is_hidden(tag):
            # Hidden content is not evidence about what a reader sees.  The
            # deterministic skills already report facts that only exist in
            # hidden or JS-injected markup, so dropping it here cannot lose a
            # finding - it only stops the model inventing one.
            tag.decompose()

    for tag in soup.find_all(["div", "section", "aside", "p"]):
        if CONSENT_TEXT_RE.search(_norm_ws(tag.get_text(" ", strip=True))[:400]):
            tag.decompose()

    return soup.find("main") or soup.find("article") or soup.find("body") or soup


def _raw_blocks(page: Page) -> list[tuple[str, str, str]]:
    """``(selector, heading, text)`` for each candidate block, in document order."""
    root = _content_root(page)
    blocks: list[tuple[str, str, str]] = []

    headings = root.find_all(["h1", "h2", "h3"]) if hasattr(root, "find_all") else []
    if headings:
        for heading in headings:
            texts: list[str] = []
            for sibling in heading.next_siblings:
                if getattr(sibling, "name", None) in ("h1", "h2", "h3"):
                    break
                text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                if text:
                    texts.append(text)
            container = heading.find_parent(["section", "article", "div", "main"]) or heading.parent
            body = _norm_ws(" ".join(texts))
            if not body:
                # A heading with no siblings of its own: fall back to the block
                # it sits in, minus the heading itself.
                parent_text = _norm_ws(container.get_text(" ", strip=True)) if container else ""
                body = parent_text
            blocks.append((_selector_for(container), _norm_ws(heading.get_text(" ", strip=True)), body))
    else:
        for tag in (root.find_all(["section", "article", "div", "p"], recursive=True) if hasattr(root, "find_all") else []):
            if tag.find(["section", "article", "div"]):
                continue
            text = _norm_ws(tag.get_text(" ", strip=True))
            if text:
                blocks.append((_selector_for(tag), "", text))
        if not blocks and hasattr(root, "get_text"):
            blocks.append(("body", "", _norm_ws(root.get_text(" ", strip=True))))
    return blocks


def _merge_and_split(blocks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Bring blocks into the useful size band (~300-800 tokens)."""
    merged: list[tuple[str, str, str]] = []
    for selector, heading, text in blocks:
        if not text:
            continue
        if merged and len(text) < MIN_SECTION_CHARS and len(merged[-1][2]) < MERGE_TARGET_CHARS:
            prev_sel, prev_head, prev_text = merged[-1]
            merged[-1] = (prev_sel, prev_head, f"{prev_text} {text}".strip())
            continue
        merged.append((selector, heading, text))

    out: list[tuple[str, str, str]] = []
    for selector, heading, text in merged:
        if len(text) <= MAX_SECTION_CHARS:
            out.append((selector, heading, text))
            continue
        # Split on sentence boundaries so a quote is never cut mid-sentence -
        # the validator has to be able to find the quote in the source text.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunk = ""
        for sentence in sentences:
            if len(chunk) + len(sentence) + 1 > MAX_SECTION_CHARS and chunk:
                out.append((selector, heading, chunk.strip()))
                chunk = sentence
            else:
                chunk = f"{chunk} {sentence}".strip()
        if chunk:
            out.append((selector, heading, chunk.strip()))
    return out


def repeated_block_digests(pages: list[Page], min_pages: int = 2) -> set[str]:
    """Digests of blocks that appear on several pages: site-wide boilerplate."""
    counts: dict[str, set[str]] = {}
    for page in pages:
        try:
            blocks = _raw_blocks(page)
        except Exception:  # noqa: BLE001 - a page we cannot parse votes on nothing
            continue
        for _, _, text in blocks:
            if len(text) < 40:
                continue
            counts.setdefault(_text_digest(text), set()).add(page.url)
    threshold = max(min_pages, 2)
    if len(pages) >= 4:
        threshold = max(threshold, len(pages) // 2)
    return {dig for dig, urls in counts.items() if len(urls) >= threshold}


# ---------------------------------------------------------------------------
# Pack construction
# ---------------------------------------------------------------------------

def build_page_evidence(page: Page, page_index: int, entry_url: str,
                        boilerplate: set[str]) -> PageEvidence:
    evidence = PageEvidence(
        page_id=f"P{page_index:03d}",
        url=page.url,
        page_type=classify_page(page, entry_url),
        depth=getattr(page, "depth", 0),
        status_code=page.status_code,
        title=redact_text(page.title)[0],
        template_id=template_id(page),
    )

    seen_local: set[str] = set()
    index = 0
    for selector, heading, text in _merge_and_split(_raw_blocks(page)):
        digest = _text_digest(text)
        if digest in boilerplate or digest in seen_local:
            continue
        if len(text) < MIN_SECTION_CHARS and not heading:
            continue
        seen_local.add(digest)
        index += 1
        if index > MAX_SECTIONS_PER_PAGE:
            break
        clean_text, _ = redact_text(text, keep_emails=False)
        clean_heading, _ = redact_text(heading, keep_emails=False)
        evidence.sections.append(EvidenceSection(
            evidence_id=f"{evidence.page_id}-S{index:03d}",
            selector=selector,
            heading=clean_heading,
            text=clean_text,
            source_kind=classify_source_kind(clean_heading, clean_text, selector),
            language=_language_of(page),
            hidden=False,
            word_count=len(clean_text.split()),
        ))
    return evidence


def rank_pages(pages: list[PageEvidence], template_counts: dict[str, int]) -> list[tuple[float, PageEvidence]]:
    """Score every crawled page for semantic-analysis value.

    priority = business importance + page-type coverage value + unique-template
    value + semantic-information value - duplicate penalty - depth penalty -
    tracking/query penalty.  The crawler decides *which URLs exist*; this only
    decides which of those already-fetched pages are worth spending tokens on.
    """
    seen_types: dict[str, int] = {}
    seen_templates: dict[str, int] = {}
    ranked: list[tuple[float, PageEvidence]] = []

    for page in pages:
        score = PAGE_TYPE_VALUE.get(page.page_type, 1.0)

        quota = PAGE_TYPE_QUOTA.get(page.page_type, 3)
        already = seen_types.get(page.page_type, 0)
        if already >= quota:
            score -= 2.5 * (already - quota + 1)
        seen_types[page.page_type] = already + 1

        template_seen = seen_templates.get(page.template_id, 0)
        if template_counts.get(page.template_id, 1) > 1 and template_seen >= 2:
            score -= 3.0          # third sample of the same template adds nothing
        seen_templates[page.template_id] = template_seen + 1

        words = sum(section.word_count for section in page.sections)
        score += min(2.0, words / 400.0)          # semantic information value
        if not page.sections:
            score -= 4.0
        score -= 0.75 * max(0, page.depth)        # depth penalty
        parsed = urlparse(page.url)
        if parsed.query:
            score -= 1.0                          # tracking / faceted URL penalty
        ranked.append((round(score, 3), page))

    ranked.sort(key=lambda pair: (-pair[0], pair[1].page_id))
    return ranked


def select_semantic_pages(pack: EvidencePack, budget: CrawlBudget) -> list[PageEvidence]:
    """Pick the 8-12 (never more than ``max_semantic_pages``) pages to analyse."""
    template_counts: dict[str, int] = {}
    for page in pack.pages:
        template_counts[page.template_id] = template_counts.get(page.template_id, 0) + 1

    ranked = rank_pages(pack.pages, template_counts)
    chosen = [page for score, page in ranked if page.sections][: budget.max_semantic_pages]
    if len(chosen) > budget.semantic_page_target:
        # Keep the target size unless the extra pages are genuinely distinct
        # page types, which is where the coverage value actually lies.
        head = chosen[: budget.semantic_page_target]
        covered = {page.page_type for page in head}
        for page in chosen[budget.semantic_page_target:]:
            if page.page_type not in covered and len(head) < budget.max_semantic_pages:
                head.append(page)
                covered.add(page.page_type)
        chosen = head
    return chosen


def build_evidence_pack(snapshot: SiteSnapshot,
                        budget: CrawlBudget | None = None) -> EvidencePack:
    """Turn a crawl into the normalised evidence pack.

    Only pages that were actually retrieved contribute sections: a page the
    crawler could not fetch is an audit limitation, not evidence about the site.
    """
    budget = budget or CrawlBudget.legacy()
    ok_pages = snapshot.ok_pages
    boilerplate = repeated_block_digests(ok_pages) if len(ok_pages) > 1 else set()

    pages: list[PageEvidence] = []
    skipped: list[str] = []
    for index, page in enumerate(ok_pages, start=1):
        try:
            pages.append(build_page_evidence(page, index, snapshot.entry_url, boilerplate))
        except Exception as exc:  # noqa: BLE001
            # One malformed document must not cost the whole pack. The page is
            # dropped and recorded; the remaining pages keep their ids, which is
            # why the id comes from the loop index and not from len(pages).
            skipped.append(f"{page.url}: {type(exc).__name__}")

    pack = EvidencePack(
        site_url=snapshot.entry_url,
        snapshot_id=snapshot_hash(snapshot.content_fingerprint()),
        pages=pages,
        notes={
            "pages_crawled": len(snapshot.pages),
            "pages_retrievable": len(ok_pages),
            "boilerplate_blocks_removed": len(boilerplate),
            "pages_skipped": skipped,
        },
    )
    return pack


def pack_for_pages(pack: EvidencePack, pages: Iterable[PageEvidence]) -> dict[str, Any]:
    """Serialise a subset of the pack for one request."""
    subset = list(pages)
    return {
        "site_url": pack.site_url,
        "snapshot_id": pack.snapshot_id,
        "pages": [page.as_dict() for page in subset],
    }
