#!/usr/bin/env python3
"""Skill: entity-freshness-audit

Responsibilities (and nothing else):
  1. Is the organisation identifiable as ONE entity?  -> ``entity_identity``
  2. Are the facts still current?                     -> ``freshness``

Entity identity is decided by cross-referencing the *identity slots* a machine
reads - <title>, <h1>, JSON-LD Organization, OpenGraph, footer copyright and a
small set of explicit prose patterns ("Welcome to X", "About X", "formerly X",
"a division of X") - across every crawled page.  A finding is raised only when
three or more distinct names occupy those slots AND at least one pair is a
near-variant of another (similar but not identical), which is the fingerprint of
one company with several names rather than several legitimately different
entities being mentioned.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from lib.contracts import ORG_SCHEMA_TYPES, Page, SiteSnapshot, SkillResult, make_finding, recommendation

SKILL_ID = "entity-freshness-audit"

TITLE_SEPARATORS = (" :: ", " — ", " – ", " | ", " - ", " · ", " • ", " › ", " » ")
SEPARATOR_RE = r"\s+(?:::|—|–|\||-|·|•|›|»)\s+"
GENERIC_TITLE_SEGMENTS = {
    "about", "about us", "products", "product", "pricing", "prices", "plans", "contact",
    "contact us", "news", "blog", "docs", "documentation", "home", "services", "support",
    "careers", "faq", "team", "press", "resources", "solutions", "company", "overview",
}
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc", "gmbh", "ag", "sa",
    "bv", "srl", "sl", "pvt", "pte", "nv", "llp", "lp", "kk", "oy", "oyj", "ab", "aps",
    "pty", "ptyltd", "coltd", "sarl", "spa", "kg", "cokg", "holding", "holdings",
}
NAME_STOPWORDS = {
    "home", "about", "contact", "products", "product", "pricing", "welcome", "blog", "news",
    "documentation", "docs", "services", "overview", "menu", "login", "search", "support",
    "privacy policy", "terms of service", "us",
}
CAP_TOKEN = r"[A-Z][\w&'’.\-]*"
NAME_PHRASE = rf"{CAP_TOKEN}(?:\s+{CAP_TOKEN}){{0,4}}"

WELCOME_RE = re.compile(rf"\bWelcome to\s+({NAME_PHRASE})")
FORMERLY_RE = re.compile(rf"\b(?:formerly|previously known as|f/k/a|rebranded from)\s+({NAME_PHRASE})", re.I)
DIVISION_RE = re.compile(rf"\b(?:a division of|a subsidiary of|part of|an? .{{0,20}}company of)\s+({NAME_PHRASE})", re.I)
ABOUT_HEADING_RE = re.compile(rf"^About\s+({NAME_PHRASE})$")
COPYRIGHT_RE = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?\d{4}\s+([A-Z][^.|\n]{2,60}?)"
    r"(?:\.|,?\s*All rights|,?\s*All Rights|$)"
)
# Handles "© 2026", "© 2020-2026" and the very common "© 2020-26": the year that
# matters is the LATEST one in the range, since that is what the site claims as
# current. Reading the first year turned "© 2020-26" into six-year-old content.
COPYRIGHT_YEAR_RE = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)\s*(\d{4})(?:\s*[-–—]\s*(\d{2,4}))?", re.I)
YEAR_RE = re.compile(r"\b(19[9]\d|20[0-4]\d)\b")
FOUNDING_CONTEXT_RE = re.compile(
    r"(founded|founding|since|established|est\.|inception|incorporated|operating since|serving since|"
    r"in business since|copyright ©?\s*\d{4}\s*[-–])\D{0,25}$",
    re.I,
)
CLAIM_KEYWORDS = (
    ("pricing", ("price", "pricing", "/month", "per month", "plan", "$")),
    ("award", ("award", "winner", "recognit", "vendor", "named", "best ")),
    ("statistic", ("survey", "report", "market", "statist", "according to", "research", "data")),
    ("update stamp", ("last updated", "updated:", "effective", "as of", "revised")),
    ("press", ("press", "announce", "raises", "launch", "release")),
    ("team", ("ceo", "cto", "appointed", "team", "founder")),
)
ORG_TYPES_LOWER = {t.lower() for t in ORG_SCHEMA_TYPES}


# ---------------------------------------------------------------------------
# Entity name extraction
# ---------------------------------------------------------------------------

def _clean_candidate(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = name.strip(" \t\n\"'“”‘’,;:·|-–—")
    name = re.sub(r"[.,;:]+$", "", name)
    return name


def normalise_name(name: str) -> str:
    """Lowercase, punctuation-free, legal-suffix-free key for comparison.

    Suffixes are matched on up to three trailing tokens joined together, because
    stripping punctuation turns "B.V." into "b v" and "L.L.C." into "l l c".
    """
    text = _clean_candidate(name).lower()
    text = re.sub(r"[^\w\s&]", " ", text)
    tokens = [t for t in text.split() if t]
    changed = True
    while changed and tokens:
        changed = False
        for size in (3, 2, 1):
            if len(tokens) > size and "".join(tokens[-size:]) in LEGAL_SUFFIXES:
                del tokens[-size:]
                changed = True
                break
    return " ".join(tokens)


def _acceptable(name: str) -> bool:
    cleaned = _clean_candidate(name)
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 60:
        return False
    words = cleaned.split()
    if len(words) > 6:
        return False
    if cleaned.lower() in NAME_STOPWORDS or normalise_name(cleaned) in NAME_STOPWORDS:
        return False
    if not any(ch.isupper() for ch in cleaned):
        return False
    if not re.search(r"[A-Za-z]{2}", cleaned):
        return False
    return True


def _title_prefix(text: str) -> str:
    """The brand segment of a title.

    Titles are usually "Brand - tagline" on a home page but "Page - Brand" on a
    sub-page, so when the leading segment is a generic page word the trailing
    segment is taken instead. Without this, every sub-page title contributes a
    phantom "entity" and healthy multi-page sites look ambiguous.
    """
    parts = [part.strip() for part in re.split(SEPARATOR_RE, text) if part.strip()]
    if len(parts) > 1:
        if normalise_name(parts[0]) in GENERIC_TITLE_SEGMENTS:
            return parts[-1]
        return parts[0]
    return text.strip() if len(text.split()) <= 5 else ""


def extract_names(page: Page) -> list[tuple[str, str]]:
    """Return ``(name, slot)`` pairs found in this page's identity slots."""
    found: list[tuple[str, str]] = []

    def add(value: str, slot: str) -> None:
        cleaned = _clean_candidate(value)
        if _acceptable(cleaned):
            found.append((cleaned, slot))

    if page.title:
        add(_title_prefix(page.title), "title")

    for key, slot in (("og:site_name", "og:site_name"), ("og:title", "og:title")):
        if page.meta.get(key):
            add(_title_prefix(page.meta[key]), slot)

    h1 = page.soup.find("h1")
    if h1:
        text = re.sub(r"^\s*Welcome to\s+", "", h1.get_text(" ", strip=True))
        add(text, "h1")

    for obj in page.jsonld_objects:
        types = obj.get("@type")
        types = types if isinstance(types, list) else [types]
        if not any(str(t).lower() in ORG_TYPES_LOWER for t in types if t):
            continue  # Product/SoftwareApplication names are not entity names
        for key, slot in (("name", "json-ld name"), ("legalName", "json-ld legalName"),
                          ("alternateName", "json-ld alternateName")):
            value = obj.get(key)
            if isinstance(value, list):
                for item in value:
                    add(str(item), slot)
            elif isinstance(value, str):
                add(value, slot)

    body = page.body_text
    match = COPYRIGHT_RE.search(body)
    if match:
        add(match.group(1), "footer copyright")
    for pattern, slot in ((WELCOME_RE, "body 'Welcome to'"), (FORMERLY_RE, "body 'formerly'"),
                          (DIVISION_RE, "body 'division of'")):
        for hit in pattern.finditer(body):
            add(hit.group(1), slot)
    for _, heading in page.headings:
        hit = ABOUT_HEADING_RE.match(heading.strip())
        if hit:
            add(hit.group(1), "'About' heading")

    return found


def build_entity_profile(snapshot: SiteSnapshot) -> dict[str, Any]:
    slots: dict[str, set[str]] = defaultdict(set)
    surface: dict[str, str] = {}
    pages: dict[str, set[str]] = defaultdict(set)

    for page in snapshot.ok_pages:
        for name, slot in extract_names(page):
            key = normalise_name(name)
            if not key:
                continue
            slots[key].add(slot)
            pages[key].add(page.url)
            surface.setdefault(key, _clean_candidate(name))

    keys = sorted(slots)
    if not keys:
        return {"names": {}, "distinct": 0, "variant_pairs": [], "canonical": "", "variants": []}

    # The canonical name is the *medoid* of the name set - the spelling closest to
    # all the others - chosen from names that actually behave like site identity
    # (they sit in a strong slot, or recur across slots or pages). Picking the
    # most-frequent name instead would let a footer variant such as "PeakCloud
    # Inc." become the anchor and hide the conflict; picking any name at all
    # would let a one-off sub-page title become the anchor on real sites.
    strong_slots = {"json-ld name", "json-ld legalName", "og:site_name", "footer copyright"}
    squashed_all = {k: k.replace(" ", "") for k in keys}

    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, squashed_all[a], squashed_all[b]).ratio()

    anchors = [k for k in keys
               if (slots[k] & strong_slots) or len(slots[k]) >= 2 or len(pages[k]) >= 2] or keys
    canonical = max(anchors, key=lambda k: (
        sum(similarity(k, other) for other in keys if other != k), len(slots[k]), len(pages[k]), -len(k)))

    # Only names that are plausibly *the same organisation spelled differently*
    # count towards ambiguity. On a real multi-page site, sub-page titles and
    # marketing headlines ("Online Rent Agreement", "Scaling Render Services")
    # are page topics, not competing entity names, and anchoring on the canonical
    # name is what tells the two apart.
    squashed = {k: k.replace(" ", "") for k in keys}
    variants = []
    for key in keys:
        if key == canonical:
            continue
        if len(squashed[key]) > len(squashed[canonical]) * 2.2 + 4:
            continue  # a phrase, not a name variant
        ratio = SequenceMatcher(None, squashed[canonical], squashed[key]).ratio()
        shares_stem = squashed[key][:4] == squashed[canonical][:4] and len(squashed[canonical]) >= 4
        if ratio >= 0.5 or shares_stem:
            variants.append((canonical, key, round(ratio, 3)))

    return {
        "names": {k: {"surface": surface[k], "slots": sorted(slots[k]), "pages": sorted(pages[k])} for k in keys},
        "distinct": len(keys),
        "variant_pairs": sorted(variants, key=lambda p: -p[2]),
        "variants": [v[1] for v in variants],
        "canonical": canonical,
    }


def check_entity_identity(snapshot: SiteSnapshot, profile: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []

    names = profile["names"]
    family = [profile["canonical"], *profile.get("variants", [])]
    if len(family) >= 3 and profile["variant_pairs"]:
        listed = "; ".join(
            f"\"{names[key]['surface']}\" ({', '.join(names[key]['slots'][:3])})" for key in family[:6]
        )
        a, b, ratio = profile["variant_pairs"][0]
        entry = snapshot.entry_page
        total_chars = sum(page.char_count for page in snapshot.ok_pages)
        findings.append(make_finding(
            category="entity_identity",
            title="The organisation is referred to by several conflicting names",
            severity="high",
            evidence=(
                f"Scanned {total_chars} characters of extractable text across {len(snapshot.ok_pages)} crawled "
                f"pages from {entry.url if entry else snapshot.entry_url} "
                f"({entry.http_label if entry else 'HTTP 200'}): {len(family)} spellings of the same "
                f"organisation occupy the identity slots: {listed}. "
                f"\"{names[a]['surface']}\" and \"{names[b]['surface']}\" are {int(ratio * 100)}% string-similar, "
                f"i.e. variants of one another rather than separate companies, and {len(profile['variant_pairs'])} "
                f"such near-duplicate pairs were measured in total."
            ),
            action=(
                "Pick one canonical legal name and use it verbatim in the <title>, <h1>, footer copyright and the "
                "JSON-LD Organization `name` property on every HTML page, demoting every other spelling to "
                "`alternateName` in that same Organization block, and add `sameAs` links to the company's "
                "LinkedIn and Crunchbase profiles. One machine-readable name lets AI systems and crawlers "
                "resolve every page to a single entity they can trust, extract and cite."
            ),
            mechanism=(
                "Assistants resolve a page to an entity before they cite it. Competing names split the evidence "
                "across several weak candidate entities, so the brand is described vaguely, attributed to the "
                "wrong name, or dropped from the answer."
            ),
            locations=sorted({url for meta in names.values() for url in meta["pages"]})[:3],
            detected_by=SKILL_ID,
            proof={"canonical": profile["canonical"],
                   "variant_count": len(family),
                   "names": [names[key]["surface"] for key in family],
                   "variant_pairs": profile["variant_pairs"][:5]},
        ))
        recs.append(recommendation(
            "Update JSON-LD to carry the canonical name and list variants as alternateName",
            "Keep exactly one `name` in the Organization block and move every retired spelling to "
            "`alternateName`, so historical references still resolve to the same entity instead of competing "
            "with it.",
            "entity_identity", "medium",
        ))
        recs.append(recommendation(
            "Corroborate the canonical company name off-site",
            "Add `sameAs` links to LinkedIn, Crunchbase and Wikidata, and use the identical spelling in those "
            "profiles. External agreement is what lets an AI system trust that the variants are one company.",
            "entity_identity", "low",
        ))
    elif len(family) == 2 and profile["variant_pairs"]:
        a, b, ratio = profile["variant_pairs"][0]
        recs.append(recommendation(
            "Use one spelling of the company name in every identity slot",
            f"Two closely related spellings were observed (\"{names[a]['surface']}\" and \"{names[b]['surface']}\", "
            f"{int(ratio * 100)}% similar). This is not yet ambiguous, but standardising the shorter form as "
            "`alternateName` in Organization JSON-LD keeps entity resolution unambiguous as the site grows.",
            "entity_identity", "low",
        ))
    return findings, recs


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def _classify_claim(context: str) -> str:
    lowered = context.lower()
    for label, keywords in CLAIM_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return label
    return "undated claim"


def collect_dates(page: Page, current_year: int) -> dict[str, Any]:
    text = page.body_text
    stale: list[dict[str, Any]] = []
    recent: list[int] = []

    for match in YEAR_RE.finditer(text):
        year = int(match.group(1))
        before = text[max(0, match.start() - 40):match.start()]
        after = text[match.end():match.end() + 40]
        if FOUNDING_CONTEXT_RE.search(before):
            continue  # a founding year is a fact, not staleness
        if year >= current_year:
            recent.append(year)
        elif year <= current_year - 2:
            stale.append({
                "year": year,
                "context": re.sub(r"\s+", " ", (before[-45:] + match.group(1) + after[:25]).strip()),
                "kind": _classify_claim(before[-60:] + after[:30]),
            })

    copyright_year = None
    for match in COPYRIGHT_YEAR_RE.finditer(text):
        start = int(match.group(1))
        end_raw = match.group(2)
        year = start
        if end_raw:
            end = int(end_raw)
            if end < 100:  # "2020-26" -> 2026
                end += (start // 100) * 100
            year = max(start, end)
        if 1990 <= year <= current_year + 1:
            copyright_year = max(copyright_year or 0, year)

    return {
        "url": page.url,
        "stale": stale,
        "recent_years": sorted(set(recent)),
        "copyright_year": copyright_year,
    }


def check_freshness(snapshot: SiteSnapshot, dates: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []
    current_year = datetime.now(timezone.utc).year

    stale_mentions = [m for d in dates for m in d["stale"]]
    recent_years = sorted({y for d in dates for y in d["recent_years"]})
    copyright_years = [d["copyright_year"] for d in dates if d["copyright_year"]]
    newest_copyright = max(copyright_years) if copyright_years else None
    oldest_copyright = min(copyright_years) if copyright_years else None

    # Staleness is decided per page, and a page only counts as stale when it
    # carries no current-year date of its own. That distinction matters on real
    # sites, where one cached template or campaign page shows an old copyright
    # while the content around it is current - noise, not a defect - whereas a
    # page whose every date is years old is genuinely stale.
    # If anywhere on the site carries a current-year date, the site is being
    # maintained: an old copyright line is then hygiene, not stale content, and
    # is demoted to a recommendation. Content pages whose every date is years old
    # still count as stale, which is what a reader actually gets wrong facts from.
    site_is_maintained = bool(recent_years)
    stale_pages = [
        d for d in dates
        if not d["recent_years"] and (
            len(d["stale"]) >= 3
            or (not site_is_maintained and d["copyright_year"] and d["copyright_year"] <= current_year - 2)
        )
    ]
    stale_copyright_only = [
        d for d in dates
        if d not in stale_pages and d["copyright_year"] and d["copyright_year"] <= current_year - 2
    ]
    copyright_stale = any(
        d["copyright_year"] and d["copyright_year"] <= current_year - 2 for d in stale_pages
    )
    triggered = bool(stale_pages)
    if not triggered:
        if stale_copyright_only:
            year_shown = min(d["copyright_year"] for d in stale_copyright_only)
            recs.append(recommendation(
                "Generate the copyright year from the server clock",
                f"The site publishes current-year content, but the footer on "
                f"{len(stale_copyright_only)} crawled pages still reads {year_shown}. A stale copyright is a "
                "cheap negative freshness signal for both readers and retrieval systems; rendering the year "
                "dynamically removes it permanently.",
                "freshness", "low",
            ))
        if stale_mentions and not copyright_years:
            recs.append(recommendation(
                "Date-stamp the factual sections",
                f"{len(stale_mentions)} dated claims were found but the site publishes no copyright year or "
                "`dateModified`. Adding visible 'last reviewed' stamps and Article/WebPage `dateModified` lets AI "
                "systems tell current facts from archived ones instead of discounting all of them.",
                "freshness", "low",
            ))
        return findings, recs

    kinds: dict[str, int] = defaultdict(int)
    for mention in stale_mentions:
        kinds[mention["kind"]] += 1
    years = sorted({m["year"] for m in stale_mentions})
    severity = "high" if len(stale_mentions) >= 8 else "medium"
    entry = snapshot.entry_page
    sample = "; ".join(f"\"{m['context'][:70]}\"" for m in stale_mentions[:3])
    kind_summary = ", ".join(f"{label} x{count}" for label, count in sorted(kinds.items(), key=lambda kv: -kv[1])[:4])

    total_chars = sum(page.char_count for page in snapshot.ok_pages)
    evidence = (
        f"Across {total_chars} characters of body text, {len(stale_mentions)} dated claims on "
        f"{entry.url if entry else snapshot.entry_url} "
        f"({entry.http_label if entry else 'HTTP 200'}) reference {years[0]}-{years[-1]}, i.e. "
        f"{current_year - years[-1]}+ years before the current year {current_year}; the spread covers "
        f"{kind_summary}. Examples: {sample}."
    )
    if copyright_stale:
        stale_copyright = min(d["copyright_year"] for d in stale_pages if d["copyright_year"])
        evidence += (
            f" {len(stale_pages)} of {len(dates)} crawled pages carry no current-year date at all, and the "
            f"copyright on them still reads {stale_copyright}, {current_year - stale_copyright} years stale."
        )
    if not recent_years:
        evidence += " No date at or after last year appears anywhere in the crawled text."
    else:
        evidence += f" Only {len(recent_years)} current-era year references were found ({recent_years})."

    findings.append(make_finding(
        category="freshness",
        title="Published facts are visibly out of date",
        severity=severity,
        evidence=evidence,
        action=(
            "Refresh the dated blocks in priority order - pricing tables first, then statistics, awards, team and "
            "press - stamp each with a visible 'Last updated' date plus a machine-readable `dateModified` in the "
            "page's JSON-LD, and generate the footer copyright year from the server clock so the HTML never "
            "contradicts the content. Label genuinely historical facts as such ('award received in 2019'). "
            "Recency that AI systems can extract is what keeps the page eligible to be cited rather than skipped."
        ),
        mechanism=(
            "Recency is a ranking and trust signal for AI answer engines: when the newest date on a page is years "
            "old, retrieval systems down-rank it and assistants prefer a competitor's current page, even if your "
            "facts are still accurate."
        ),
        locations=[d["url"] for d in stale_pages][:3] or [d["url"] for d in dates if d["stale"]][:3],
        detected_by=SKILL_ID,
        proof={"stale_mentions": len(stale_mentions), "years": years,
               "stale_pages": [d["url"] for d in stale_pages],
               "copyright_year": newest_copyright, "oldest_copyright": oldest_copyright,
               "recent_years": recent_years},
    ))
    recs.append(recommendation(
        "Put the dated pages on a review cadence",
        "Pricing, team and press pages drift fastest. Give each an owner and a quarterly review date, and update "
        "the copyright year and industry statistics from a single source so they cannot disagree with each other.",
        "freshness", "medium",
    ))
    recs.append(recommendation(
        "Add dateModified and publish recent news",
        "Expose `dateModified` in each page's JSON-LD and `lastmod` in the sitemap, and publish recent press "
        "releases, awards or product news. Both are explicit recency signals that retrieval systems read directly.",
        "freshness", "low",
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

    current_year = datetime.now(timezone.utc).year
    profile = build_entity_profile(snapshot)
    dates = [collect_dates(p, current_year) for p in snapshot.ok_pages]
    result.artifacts["entity_profile"] = profile

    entity_findings, entity_recs = check_entity_identity(snapshot, profile)
    fresh_findings, fresh_recs = check_freshness(snapshot, dates)
    result.findings.extend(entity_findings + fresh_findings)
    result.recommendations.extend(entity_recs + fresh_recs)

    result.checks = [
        {"check": "single_canonical_entity_name", "passed": not entity_findings,
         "value": len([profile["canonical"], *profile.get("variants", [])]),
         "canonical": profile["canonical"]},
        {"check": "content_is_current", "passed": not fresh_findings,
         "value": sum(len(d["stale"]) for d in dates)},
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
