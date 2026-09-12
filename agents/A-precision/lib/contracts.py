#!/usr/bin/env python3
"""Shared data contracts for the AI Discoverability Skill Marketplace.

Every skill in the marketplace consumes a :class:`SiteSnapshot` and returns a
:class:`SkillResult`.  Nothing else is shared between skills, which keeps the
concerns isolated and makes each skill independently testable.

Design rules enforced here:
  * Findings may only use the eight canonical benchmark categories.
  * A finding must carry concrete, reproducible evidence (URL + HTTP status +
    at least one measurement).  Findings without evidence are rejected, which
    is the structural reason this agent does not hallucinate faults.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterable

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Canonical vocabulary (must match the benchmark normalizer exactly)
# ---------------------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    "crawlability",
    "rendering",
    "content_extraction",
    "structured_data",
    "non_text_facts",
    "freshness",
    "entity_identity",
    "engagement",
)

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Categories that block machine comprehension outright.  Used for prioritisation.
BLOCKING_CATEGORIES = frozenset(
    {"crawlability", "rendering", "content_extraction", "entity_identity"}
)
SECONDARY_CATEGORIES = frozenset({"engagement", "non_text_facts", "freshness"})

# Elements whose text is site chrome rather than page content.
CHROME_TAGS = ("nav", "header", "footer")
NOISE_TAGS = ("script", "style", "template", "svg")

# Client-side injection patterns.  These let us *simulate* what a JS-enabled
# renderer would see without shipping a headless browser.
_INJECTION_PATTERNS = (
    re.compile(r"\.innerHTML\s*=\s*(`|'|\")(?P<payload>.*?)(?<!\\)\1", re.S),
    re.compile(r"insertAdjacentHTML\s*\([^,]+,\s*(`|'|\")(?P<payload>.*?)(?<!\\)\1", re.S),
    re.compile(r"document\.write\s*\(\s*(`|'|\")(?P<payload>.*?)(?<!\\)\1", re.S),
)
_TARGET_PATTERNS = (
    re.compile(r"getElementById\(\s*['\"](?P<target>[\w\-]+)['\"]\s*\)"),
    re.compile(r"querySelector\(\s*['\"]#(?P<target>[\w\-]+)['\"]\s*\)"),
)

ORG_SCHEMA_TYPES = {
    "organization", "corporation", "localbusiness", "onlinebusiness",
    "newsmediaorganization", "educationalorganization", "govermentorganization",
    "ngo", "performinggroup", "sportsorganization", "medicalorganization",
    "airline", "store", "restaurant",
}


# ---------------------------------------------------------------------------
# Page / snapshot model
# ---------------------------------------------------------------------------

@dataclass
class Page:
    """A single fetched document, plus lazily derived views of its content."""

    url: str
    status_code: int | None = None
    content_type: str = ""
    html: str = ""
    # Body of a *non-HTML* text document (robots.txt, sitemap XML).  Kept apart
    # from ``html`` on purpose: ``ok`` and ``soup`` stay HTML-only, so a plain
    # text or XML fetch can never be mistaken for a crawlable page by any
    # downstream skill that iterates ``SiteSnapshot.ok_pages``.
    text_body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int = 0
    is_entry: bool = False
    # Crawl planning metadata.  Both default to the values the original
    # breadth-first crawler implied, so nothing downstream changes when the
    # legacy crawl profile is in force.
    depth: int = 0
    discovered_from: str | None = None

    # -- basic state ------------------------------------------------------
    @property
    def ok(self) -> bool:
        return self.status_code == 200 and bool(self.html)

    @property
    def http_label(self) -> str:
        """Always rendered into evidence so a reviewer can reproduce the check."""
        return f"HTTP {self.status_code}" if self.status_code else "HTTP no-response"

    # -- parsed views -----------------------------------------------------
    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html or "", "html.parser")

    @cached_property
    def _body_soup(self) -> BeautifulSoup:
        """Copy of the document with scripts/styles removed (what a text
        extractor keeps).  ``noscript`` is removed too: its content is a
        fallback notice, not page content."""
        soup = BeautifulSoup(self.html or "", "html.parser")
        for tag in soup.find_all([*NOISE_TAGS, "noscript"]):
            tag.decompose()
        return soup

    @cached_property
    def body_text(self) -> str:
        return re.sub(r"\s+", " ", self._body_soup.get_text(" ", strip=True))

    @cached_property
    def content_text(self) -> str:
        """Body text with navigation chrome removed - the substance an AI
        answer engine would actually quote."""
        soup = BeautifulSoup(str(self._body_soup), "html.parser")
        for tag in soup.find_all(CHROME_TAGS):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    @cached_property
    def word_count(self) -> int:
        return len([w for w in self.content_text.split() if any(c.isalnum() for c in w)])

    @cached_property
    def char_count(self) -> int:
        return len(self.content_text)

    # -- structured data --------------------------------------------------
    @cached_property
    def jsonld_blocks(self) -> list[dict[str, Any]]:
        """Every ``application/ld+json`` block, parsed.  Parse failures are
        preserved (``ok=False``) so the structured-data skill can report them
        instead of silently ignoring them."""
        blocks: list[dict[str, Any]] = []
        for script in self.soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw = script.string or script.get_text() or ""
            entry: dict[str, Any] = {"raw": raw.strip(), "ok": False, "data": None, "error": None}
            try:
                entry["data"] = json.loads(raw)
                entry["ok"] = True
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                entry["error"] = f"{type(exc).__name__}: {exc}"
            blocks.append(entry)
        return blocks

    @cached_property
    def jsonld_objects(self) -> list[dict[str, Any]]:
        """Flattened list of every JSON-LD node (handles @graph and arrays)."""
        out: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                out.append(node)
                for key in ("@graph", "hasPart", "mainEntity", "itemListElement"):
                    if key in node:
                        walk(node[key])

        for block in self.jsonld_blocks:
            if block["ok"]:
                walk(block["data"])
        return out

    @cached_property
    def microdata_types(self) -> list[str]:
        types = []
        for tag in self.soup.find_all(attrs={"itemtype": True}):
            types.append(str(tag.get("itemtype")).rsplit("/", 1)[-1])
        for tag in self.soup.find_all(attrs={"typeof": True}):
            types.append(str(tag.get("typeof")))
        return types

    @cached_property
    def schema_types(self) -> list[str]:
        types = []
        for obj in self.jsonld_objects:
            t = obj.get("@type")
            if isinstance(t, list):
                types.extend(str(x) for x in t)
            elif t:
                types.append(str(t))
        return types + self.microdata_types

    # -- client-side rendering -------------------------------------------
    @cached_property
    def inline_scripts(self) -> list[str]:
        out = []
        for script in self.soup.find_all("script"):
            if script.get("src"):
                continue
            text = script.string or script.get_text() or ""
            if text.strip():
                out.append(text)
        return out

    @cached_property
    def js_payloads(self) -> list[dict[str, Any]]:
        """HTML fragments that inline JavaScript injects at runtime.

        This is the deterministic 'simulated render': we recover the markup a
        JS-enabled client would see and diff it against the raw HTML.
        """
        payloads: list[dict[str, Any]] = []
        for script in self.inline_scripts:
            targets = []
            for pattern in _TARGET_PATTERNS:
                targets.extend(m.group("target") for m in pattern.finditer(script))
            for pattern in _INJECTION_PATTERNS:
                for match in pattern.finditer(script):
                    payload = match.group("payload")
                    if len(payload.strip()) < 20:
                        continue
                    payloads.append({"targets": targets, "html": payload})
        return payloads

    @cached_property
    def js_rendered_text(self) -> str:
        """Text the page would expose *after* client-side injection."""
        if not self.js_payloads:
            return ""
        merged = " ".join(p["html"] for p in self.js_payloads)
        soup = BeautifulSoup(merged, "html.parser")
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    @cached_property
    def js_shell_signals(self) -> dict[str, bool]:
        """Signals that this document is a shell whose real DOM is built by JS.

        Shared by three skills: the rendering detector reports it, the extraction
        detector uses it as a root cause, and the engagement detector uses it to
        stay silent (navigation cannot be measured on a DOM that was never built).
        """
        soup = self.soup
        framework_ids = {"root", "app", "__next", "__nuxt", "ember-app", "svelte-app", "q-app"}
        empty_root = any(
            str(tag.get("id")).lower() in framework_ids and not tag.get_text(strip=True)
            for tag in soup.find_all(["div", "main", "section"], id=True)
        )
        filled_by_js = False
        for payload in self.js_payloads:
            for target in payload["targets"]:
                element = soup.find(id=target)
                if element is not None and not element.get_text(strip=True):
                    filled_by_js = True
        gate = any(
            re.search(r"(enable\s+javascript|requires?\s+javascript|javascript\s+is\s+required|"
                      r"turn\s+on\s+javascript|javascript\s+must\s+be\s+enabled)", notice, re.I)
            for notice in self.noscript_notices
        )
        return {"empty_framework_root": empty_root, "filled_by_js": filled_by_js, "noscript_gate": gate}

    @property
    def is_js_shell(self) -> bool:
        """True when the raw HTML is a shell: JS builds the page and almost no
        text survives without it."""
        return self.word_count < 60 and any(self.js_shell_signals.values())

    @cached_property
    def noscript_notices(self) -> list[str]:
        out = []
        for tag in self.soup.find_all("noscript"):
            text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
            if text:
                out.append(text)
        return out

    # -- links / media ----------------------------------------------------
    @cached_property
    def links(self) -> list[dict[str, str]]:
        out = []
        for a in self.soup.find_all("a", href=True):
            out.append({
                "href": a["href"].strip(),
                "text": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
                "in_chrome": bool(a.find_parent(CHROME_TAGS)),
            })
        return out

    @cached_property
    def images(self) -> list[dict[str, Any]]:
        out = []
        for img in self.soup.find_all("img"):
            src = str(img.get("src") or "")
            alt = img.get("alt")
            heading = ""
            prev = img.find_previous(["h1", "h2", "h3", "h4"])
            if prev:
                heading = re.sub(r"\s+", " ", prev.get_text(" ", strip=True))
            figcaption = ""
            figure = img.find_parent("figure")
            if figure and figure.find("figcaption"):
                figcaption = figure.find("figcaption").get_text(" ", strip=True)
            out.append({
                "src": src,
                "alt": None if alt is None else str(alt).strip(),
                "heading": heading,
                "caption": figcaption,
                "in_chrome": bool(img.find_parent(CHROME_TAGS)),
            })
        return out

    @cached_property
    def meta(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for tag in self.soup.find_all("meta"):
            key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
            if key and tag.get("content") is not None:
                out[str(key).lower()] = str(tag.get("content"))
        return out

    @cached_property
    def title(self) -> str:
        tag = self.soup.find("title")
        return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)) if tag else ""

    @cached_property
    def headings(self) -> list[tuple[str, str]]:
        return [
            (tag.name, re.sub(r"\s+", " ", tag.get_text(" ", strip=True)))
            for tag in self.soup.find_all(["h1", "h2", "h3"])
        ]


@dataclass
class SiteSnapshot:
    """Read-only view of a site, produced once by ``crawl-render-audit`` and
    shared with every downstream skill."""

    base_url: str
    entry_url: str
    pages: list[Page] = field(default_factory=list)
    robots_txt: str | None = None
    robots_status: int | None = None
    # Flat list of page URLs recovered from every sitemap document that was
    # actually read (a ``<sitemapindex>`` is followed one level into its
    # children).  Consumers that only want "does this site publish a sitemap"
    # should read ``sitemap_checked``/``sitemap_sources`` instead of testing
    # this list, because an empty list is also what an unread sitemap produces.
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_sources: list[dict[str, Any]] = field(default_factory=list)
    sitemap_lastmods: list[str] = field(default_factory=list)
    robots_sitemap_refs: list[str] = field(default_factory=list)
    # False means the check never completed (transport error), which is not the
    # same as "the site publishes no sitemap" and must not be reported as such.
    sitemap_checked: bool = False
    broken_links: list[dict[str, Any]] = field(default_factory=list)
    fetch_errors: list[dict[str, Any]] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def ok_pages(self) -> list[Page]:
        return [p for p in self.pages if p.ok]

    @property
    def entry_page(self) -> Page | None:
        for page in self.pages:
            if page.is_entry:
                return page
        return self.pages[0] if self.pages else None

    # -- identity ---------------------------------------------------------
    def content_fingerprint(self) -> list[tuple[str, int | None, str]]:
        """``(url, status, body-digest)`` per page.

        Content-derived rather than time-derived, so replaying the same captured
        site yields the same value; this is what makes the LLM response cache
        hit on a re-run and what ties an audit to the exact bytes it saw.
        """
        out: list[tuple[str, int | None, str]] = []
        for page in self.pages:
            body = hashlib.sha256((page.html or "").encode("utf-8", errors="replace")).hexdigest()[:32]
            out.append((page.url, page.status_code, body))
        return out


# ---------------------------------------------------------------------------
# Finding contract
# ---------------------------------------------------------------------------

_MEASUREMENT_RE = re.compile(
    r"\d+\s+(chars|characters|bytes|pages|images|links|words|names|blocks|mentions|references)",
    re.I,
)


def make_finding(
    *,
    category: str,
    title: str,
    severity: str,
    evidence: str,
    action: str,
    mechanism: str,
    locations: Iterable[str] = (),
    confidence: str = "high",
    detected_by: str = "",
    proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a finding, enforcing the marketplace's evidence contract."""
    if category not in CATEGORIES:
        raise ValueError(f"illegal category {category!r}; must be one of {CATEGORIES}")
    if severity not in SEVERITIES:
        raise ValueError(f"illegal severity {severity!r}")
    if not _MEASUREMENT_RE.search(evidence):
        raise ValueError(f"evidence for {category} carries no measurement: {evidence[:120]!r}")

    return {
        "id": "",  # assigned by the orchestrator once findings are ranked
        "title": title,
        "category": category,
        # ``_category`` is the pre-normalised hint the benchmark normaliser reads
        # first; it is deliberately identical to ``category``.
        "_category": category,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence,
        "locations": list(locations),
        "proof": proof or {},
        "suggested_action": {
            "summary": action,
            "priority": severity,
            "mechanism": mechanism,
        },
        "detected_by": detected_by,
    }


@dataclass
class SkillResult:
    """What every marketplace skill returns."""

    skill: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    runtime_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "checks": self.checks,
            "error": self.error,
            "runtime_seconds": round(self.runtime_seconds, 3),
        }


def recommendation(title: str, detail: str, category: str, effort: str = "medium") -> dict[str, Any]:
    """Proactive advice: never counted as a defect, always mechanism-explained."""
    return {"title": title, "detail": detail, "category": category, "effort": effort}


# ---------------------------------------------------------------------------
# Semantic layer contracts (optional hybrid LLM path)
# ---------------------------------------------------------------------------
#
# Everything below is inert unless the LLM feature layer is switched on.  It is
# defined here, next to the deterministic contracts, because the same rule
# applies to both: a result that cannot cite reproducible evidence does not
# exist.  For the semantic layer the evidence is an *evidence id* pointing at a
# section of normalised page text, which is why the ids have to be stable.

# The category semantic results use.  It is intentionally NOT one of the eight
# deterministic CATEGORIES: promotion into `findings` is gated, and until a
# result is promoted it lives under `observations` where it cannot affect any
# score-bearing field.
SEMANTIC_CATEGORY = "engagement_sentiment"

# Where the analysed text came from.  This is the distinction that stops the
# report from claiming to know what customers feel: brand copy can only support
# a statement about tone or predicted friction.
SOURCE_KINDS: tuple[str, ...] = (
    "brand_copy",        # marketing / support / policy copy written by the site
    "customer_voice",    # reviews, testimonials, survey text, support tickets
    "system_message",    # validation errors, empty states, transactional notices
    "unknown",
)

ANALYSIS_TYPES: tuple[str, ...] = (
    "content_tone",                # how the site's own copy reads
    "predicted_visitor_friction",  # what a reader would plausibly struggle with
    "customer_sentiment",          # only ever valid for customer_voice input
)

# Source kinds that may support a claim about actual customer sentiment.
CUSTOMER_SENTIMENT_SOURCES = frozenset({"customer_voice"})

ASPECTS: tuple[str, ...] = (
    "value_proposition", "product_clarity", "pricing_transparency", "product_quality",
    "shipping", "returns", "support", "trust_credibility", "cta_clarity",
    "forms_validation", "error_messages", "navigation", "reviews_testimonials",
    "tone_consistency",
)

SENTIMENTS: tuple[str, ...] = ("positive", "neutral", "negative", "mixed", "unknown")

EMOTIONS: tuple[str, ...] = (
    "trust", "reassurance", "clarity", "confusion", "frustration", "pressure",
    "anxiety", "urgency", "uncertainty", "blame", "confidence",
)

PAGE_TYPES: tuple[str, ...] = (
    "home", "about", "contact", "category", "product", "pricing", "faq",
    "shipping_returns", "support", "blog", "legal", "landing", "other",
)

_OBSERVATION_ID_RE = re.compile(r"^SEM-\d{3,}$")
_EVIDENCE_ID_RE = re.compile(r"^P\d{3,}-S\d{3,}$")


def is_evidence_id(value: str) -> bool:
    return bool(_EVIDENCE_ID_RE.match(str(value or "")))


def is_observation_id(value: str) -> bool:
    return bool(_OBSERVATION_ID_RE.match(str(value or "")))


@dataclass
class EvidenceSection:
    """One quotable chunk of normalised page text.

    ``evidence_id`` is the only handle the model is ever given; validation
    refuses any reference to an id that was not issued here, which is what makes
    invented citations structurally impossible rather than merely unlikely.
    """

    evidence_id: str
    selector: str
    heading: str
    text: str
    source_kind: str = "brand_copy"
    language: str = "en"
    hidden: bool = False
    word_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "selector": self.selector,
            "heading": self.heading,
            "source_kind": self.source_kind,
            "language": self.language,
            "text": self.text,
        }


@dataclass
class PageEvidence:
    page_id: str
    url: str
    page_type: str = "other"
    depth: int = 0
    status_code: int | None = None
    title: str = ""
    template_id: str = ""
    sections: list[EvidenceSection] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "url": self.url,
            "page_type": self.page_type,
            "depth": self.depth,
            "status_code": self.status_code,
            "title": self.title,
            "sections": [section.as_dict() for section in self.sections],
        }


@dataclass
class EvidencePack:
    """The normalised, boilerplate-free view handed to the semantic analyser.

    Raw HTML never leaves the deterministic side of the system: the model sees
    this structure and nothing else.
    """

    site_url: str
    snapshot_id: str
    pages: list[PageEvidence] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    # -- lookups used by the validator -----------------------------------
    @cached_property
    def sections_by_id(self) -> dict[str, EvidenceSection]:
        return {s.evidence_id: s for page in self.pages for s in page.sections}

    @cached_property
    def page_of_section(self) -> dict[str, str]:
        return {s.evidence_id: page.page_id for page in self.pages for s in page.sections}

    @cached_property
    def pages_by_id(self) -> dict[str, PageEvidence]:
        return {page.page_id: page for page in self.pages}

    @cached_property
    def known_urls(self) -> frozenset[str]:
        return frozenset(page.url for page in self.pages)

    @property
    def section_count(self) -> int:
        return sum(len(page.sections) for page in self.pages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_url": self.site_url,
            "snapshot_id": self.snapshot_id,
            "pages": [page.as_dict() for page in self.pages],
        }


def make_observation(
    *,
    observation_id: str,
    title: str,
    aspect: str,
    sentiment: str,
    emotion: str,
    severity: str,
    confidence: float,
    evidence: str,
    evidence_refs: Iterable[str],
    action_summary: str,
    validation: str,
    source_kind: str = "brand_copy",
    analysis_type: str = "content_tone",
    pages: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a semantic observation in the marketplace's canonical shape.

    Enforces the vocabulary at construction time.  It does *not* enforce that
    the evidence ids exist - that is the validator's job, because it needs the
    evidence pack to answer it, and the two checks are deliberately separate so
    a malformed model response fails at the first gate rather than the last.
    """
    if aspect not in ASPECTS:
        raise ValueError(f"illegal aspect {aspect!r}")
    if sentiment not in SENTIMENTS:
        raise ValueError(f"illegal sentiment {sentiment!r}")
    if emotion not in EMOTIONS:
        raise ValueError(f"illegal emotion {emotion!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"illegal severity {severity!r}")
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"illegal source_kind {source_kind!r}")
    if analysis_type not in ANALYSIS_TYPES:
        raise ValueError(f"illegal analysis_type {analysis_type!r}")
    if analysis_type == "customer_sentiment" and source_kind not in CUSTOMER_SENTIMENT_SOURCES:
        raise ValueError(
            "customer_sentiment may only be claimed for customer_voice input; "
            f"got source_kind={source_kind!r}"
        )
    return {
        "id": observation_id,
        "title": title,
        "category": SEMANTIC_CATEGORY,
        "source_kind": source_kind,
        "analysis_type": analysis_type,
        "aspect": aspect,
        "sentiment": sentiment,
        "emotion": emotion,
        "severity": severity,
        "confidence": round(float(confidence), 4),
        "evidence": evidence,
        "evidence_refs": list(evidence_refs),
        "pages": list(pages),
        "suggested_action": {
            "summary": action_summary,
            "priority": severity,
            "validation": validation,
        },
        "finding_source": "llm_semantic",
        "validation_status": "observed",
    }
