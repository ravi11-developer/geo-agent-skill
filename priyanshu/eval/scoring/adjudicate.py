#!/usr/bin/env python3
"""Gold-free adjudication for real-web sites.

Real sites have no ground truth, so this module builds one mechanically. For each
captured site it re-derives, from the stored bytes, whether each of the eight
categories is present - and, crucially, it is allowed to answer **abstain**.

    yes      concrete evidence of the defect in the stored HTML
    no       concrete evidence against it
    abstain  the evidence is genuinely ambiguous

Only unambiguous labels score: an agent finding that contradicts a ``no`` is a
false positive, a missed ``yes`` is a false negative, and everything landing in an
``abstain`` is reported separately as *unverified* rather than being guessed at.

Independence: this is a deliberately separate implementation from every agent -
stdlib ``html.parser`` instead of BeautifulSoup, byte-level regex instead of DOM
walks, Jaccard token overlap instead of SequenceMatcher for name similarity. It is
still written by the same hand as the marketplace agent, so it cannot be treated
as impartial ground truth; ``validate_against_gold()`` measures its accuracy on
the 16 labelled synthetic sites so its error rate is stated rather than assumed,
and ``review_sample()`` emits findings for human spot-checking.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

CATEGORIES = ("crawlability", "rendering", "content_extraction", "structured_data",
              "non_text_facts", "freshness", "entity_identity", "engagement")

CHROME = {"nav", "header", "footer"}
SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}
# Whole tokens only. Substring matching flagged "global-map" (map),
# "mobilemenu_close" (menu) and "static-banner" (stat) as fact-bearing images on
# real sites, which was the adjudicator's largest source of over-labelling.
FACT_WORDS = frozenset({
    "pricing", "price", "prices", "cost", "costs", "tariff", "rate", "rates", "plan", "plans",
    "tier", "tiers", "table", "chart", "comparison", "compare", "matrix", "infographic",
    "spec", "specs", "specification", "specifications", "datasheet", "feature", "features",
    "menu", "catalogue", "catalog", "brochure", "results", "benchmark", "benchmarks",
    "stats", "statistics", "timetable", "schedule", "fees", "packages", "partner", "partners",
    "partnership", "partnerships", "customer", "customers", "client", "clients", "award",
    "awards", "contact", "roadmap",
})
DECORATIVE = ("icon", "favicon", "avatar", "sprite", "spacer", "pixel", "banner-bg", "arrow",
              "chevron", "close", "hamburger", "menu-toggle", "screenreader", "screen-reader",
              "font-", "social", "share", "thumb", "placeholder", "loader", "spinner",
              "bullet", "divider", "badge-")
GENERIC_ALT = {"", "image", "img", "photo", "picture", "graphic", "logo", "banner", "chart",
               "products", "product", "pricing", "features", "results", "partners", "info",
               "contact", "table", "menu"}
PRICE_RE = re.compile(r"(?:\$|€|£|₹)\s?\d|\bper\s+(?:month|year|user|seat)\b|/\s?(?:mo|month|yr|year)\b", re.I)
CONTACT_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}|\+?\d[\d\s().-]{8,}\d")
FACTS_RE = re.compile(r"\b(founded|headquarter\w*|employees|customers|clients|established|revenue|"
                      r"certified|uptime|sla|iso\s*\d{4,5}|soc\s*2)\b|\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\s*%", re.I)
YEAR_RE = re.compile(r"\b(19[9]\d|20[0-4]\d)\b")
FOUNDING_RE = re.compile(r"(founded|founding|since|established|est\.|inception|incorporated)\D{0,25}$", re.I)
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(\d{4})(?:\s*[-–—]\s*(\d{2,4}))?", re.I)


def _copyright_years(text: str) -> list[int]:
    """Latest year of each copyright statement ("(c) 2020-26" -> 2026)."""
    years = []
    for match in COPYRIGHT_RE.finditer(text):
        start = int(match.group(1))
        end_raw = match.group(2)
        year = start
        if end_raw:
            end = int(end_raw)
            if end < 100:
                end += (start // 100) * 100
            year = max(start, end)
        years.append(year)
    return years
EMPTY_ROOT_RE = re.compile(rb"""<(div|main|section)[^>]*\bid\s*=\s*["'](root|app|__next|__nuxt)["'][^>]*>\s*</\1>""", re.I)
INNER_HTML_RE = re.compile(r"""getElementById\(\s*['"]([\w\-]+)['"]\s*\)[^;]{0,400}?\.innerHTML\s*=""", re.S)
JS_GATE_RE = re.compile(r"(enable\s+javascript|requires?\s+javascript|javascript\s+is\s+required|"
                        r"turn\s+on\s+javascript|javascript\s+must\s+be\s+enabled)", re.I)
LEGAL_SUFFIX_RE = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|ag|sa|bv|nv|"
                             r"pvt|pte|llp|lp|oy|ab|aps|pty|sarl|spa)\b\.?", re.I)
SEPARATORS_RE = r"\s+(?:::|—|–|\||-|·|•|›|»)\s+"
GENERIC_SEGMENTS = {"about", "about us", "products", "pricing", "contact", "contact us", "news",
                    "blog", "docs", "documentation", "home", "services", "support", "careers",
                    "faq", "team", "press", "resources", "solutions", "company", "overview"}


# ---------------------------------------------------------------------------
# Minimal independent parser (stdlib only)
# ---------------------------------------------------------------------------

class PageModel(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.text_parts: list[str] = []
        self.all_text_parts: list[str] = []
        self.chrome_depth = 0
        self.skip_depth = 0
        self.images: list[dict] = []
        self.links: list[dict] = []
        self.metas: dict[str, str] = {}
        self.title = ""
        self.h1 = ""
        self.headings: list[str] = []
        self.nav_count = 0
        self.jsonld: list[str] = []
        self.noscript: list[str] = []
        self.has_footer = False
        self.has_microdata = False
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._last_heading = ""

    # -- helpers
    def _attr(self, attrs, name):
        for key, value in attrs:
            if key.lower() == name:
                return value or ""
        return None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.stack.append(tag)
        if tag in SKIP_TEXT:
            self.skip_depth += 1
        if tag in CHROME:
            self.chrome_depth += 1
        if tag == "nav":
            self.nav_count += 1
        if tag == "footer":
            self.has_footer = True
        if self._attr(attrs, "role") == "navigation":
            self.nav_count += 1
        if self._attr(attrs, "itemtype") or self._attr(attrs, "typeof"):
            self.has_microdata = True
        if tag == "script" and (self._attr(attrs, "type") or "").lower().find("ld+json") >= 0:
            self._capture = "jsonld"
            self._buffer = []
        elif tag == "script":
            self._capture = "script"
            self._buffer = []
        elif tag == "noscript":
            self._capture = "noscript"
            self._buffer = []
        elif tag == "title":
            self._capture = "title"
            self._buffer = []
        elif tag in ("h1", "h2", "h3"):
            self._capture = "heading"
            self._buffer = []
        elif tag == "img":
            self.images.append({
                "src": self._attr(attrs, "src") or "",
                "alt": self._attr(attrs, "alt"),
                "heading": self._last_heading,
                "in_chrome": self.chrome_depth > 0,
                "section_words": 0,
            })
        elif tag == "a":
            self.links.append({"href": self._attr(attrs, "href") or "", "in_chrome": self.chrome_depth > 0})
        elif tag == "meta":
            key = (self._attr(attrs, "name") or self._attr(attrs, "property") or "").lower()
            if key:
                self.metas[key] = self._attr(attrs, "content") or ""

    def handle_endtag(self, tag):
        tag = tag.lower()
        text = "".join(self._buffer).strip()
        if self._capture == "jsonld" and tag == "script":
            self.jsonld.append(text)
        elif self._capture == "script" and tag == "script":
            self.scripts_text = getattr(self, "scripts_text", "") + "\n" + text
        elif self._capture == "noscript" and tag == "noscript":
            self.noscript.append(text)
        elif self._capture == "title" and tag == "title":
            self.title = re.sub(r"\s+", " ", text)
        elif self._capture == "heading" and tag in ("h1", "h2", "h3"):
            clean = re.sub(r"\s+", " ", text)
            self.headings.append(clean)
            self._last_heading = clean
            if tag == "h1" and not self.h1:
                self.h1 = clean
        if self._capture and tag in ("script", "noscript", "title", "h1", "h2", "h3"):
            self._capture = None
            self._buffer = []
        if tag in SKIP_TEXT and self.skip_depth:
            self.skip_depth -= 1
        if tag in CHROME and self.chrome_depth:
            self.chrome_depth -= 1
        while self.stack and self.stack[-1] != tag:
            self.stack.pop()
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)
            return
        if self.skip_depth:
            return
        if self.images and self._last_heading and self.images[-1]["heading"] == self._last_heading:
            self.images[-1]["section_words"] += len(data.split())
        self.all_text_parts.append(data)
        if self.chrome_depth == 0:
            self.text_parts.append(data)

    # -- derived
    @property
    def content_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()

    @property
    def full_text(self) -> str:
        """Visible text including nav/header/footer. Dates live in footers, so
        recency has to be read from the whole page, not just the body."""
        return re.sub(r"\s+", " ", " ".join(self.all_text_parts)).strip()

    @property
    def words(self) -> int:
        return len([w for w in self.content_text.split() if any(c.isalnum() for c in w)])


def parse_page(raw: bytes, content_type: str = "") -> PageModel:
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        encoding = match.group(1)
    else:
        meta = re.search(rb"""<meta[^>]+charset=["']?([\w-]+)""", raw[:2048], re.I)
        if meta:
            encoding = meta.group(1).decode("ascii", errors="ignore")
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    model = PageModel()
    try:
        model.feed(text)
    except Exception:  # noqa: BLE001 - malformed markup must not break adjudication
        pass
    model.raw = raw
    model.decoded = text
    return model


# ---------------------------------------------------------------------------
# Per-category adjudication
# ---------------------------------------------------------------------------

def _fact_images(page: PageModel, strict: bool = True) -> list[dict]:
    """Fact-bearing images.

    ``strict`` keys on the filename alone, which is reliable but blind to hashed
    or CDN filenames. The loose pass also accepts the nearest heading, which
    catches those but depends on section attribution a streaming parser cannot do
    exactly - so the loose pass is used only to decide when to abstain.
    """
    out = []
    for image in page.images:
        if image["in_chrome"]:
            continue
        base = (image["src"] or "").lower().rsplit("/", 1)[-1]
        if base.startswith("logo") or any(hint in base for hint in DECORATIVE):
            continue
        # Filename only. A streaming parser cannot reliably tell which heading an
        # image sits under, and inheriting a distant heading was labelling
        # ordinary photos as fact-bearing.
        source = base.lower() if strict else f"{base} {image['heading']}".lower()
        tokens = set(re.split(r"[^a-z0-9]+", source)) - {""}
        if not (tokens & FACT_WORDS):
            continue
        alt = (image["alt"] or "").strip()
        if len(alt.split()) >= 4 and alt.lower() not in GENERIC_ALT:
            continue
        if image["section_words"] >= 30:
            continue
        out.append(image)
    return out


def _signals(page: PageModel) -> int:
    text = page.content_text
    return sum(bool(rx.search(text)) for rx in (PRICE_RE, CONTACT_RE, FACTS_RE))


def _is_shell(page: PageModel) -> bool:
    if EMPTY_ROOT_RE.search(page.raw):
        return page.words < 60
    scripts = getattr(page, "scripts_text", "")
    for target in INNER_HTML_RE.findall(scripts or ""):
        if re.search(rb"id\s*=\s*[\"']" + re.escape(target).encode() + rb"[\"'][^>]*>\s*<", page.raw, re.I):
            return page.words < 60
    if any(JS_GATE_RE.search(n) for n in page.noscript) and page.words < 60:
        return True
    return False


def _names(page: PageModel) -> set[str]:
    found = set()

    def add(value: str) -> None:
        cleaned = re.sub(r"\s+", " ", (value or "")).strip(" \t\"'“”‘’,;:|-–—")
        if not cleaned or len(cleaned) > 60 or len(cleaned.split()) > 6:
            return
        if not any(c.isupper() for c in cleaned):
            return
        key = LEGAL_SUFFIX_RE.sub("", cleaned.lower())
        key = re.sub(r"[^\w\s]", " ", key)
        key = re.sub(r"\s+", " ", key).strip()
        if key and key not in GENERIC_SEGMENTS and len(key) > 2:
            found.add(key)

    if page.title:
        parts = [p.strip() for p in re.split(SEPARATORS_RE, page.title) if p.strip()]
        if len(parts) > 1:
            add(parts[-1] if parts[0].lower() in GENERIC_SEGMENTS else parts[0])
        elif len(page.title.split()) <= 5:
            add(page.title)
    if page.h1:
        add(re.sub(r"^\s*Welcome to\s+", "", page.h1, flags=re.I))
    if page.metas.get("og:site_name"):
        add(page.metas["og:site_name"])
    match = COPYRIGHT_RE.search(page.decoded)
    if match:
        tail = page.decoded[match.end():match.end() + 70]
        tail = re.split(r"\.|All rights|<", tail)[0]
        add(tail)
    for block in page.jsonld:
        try:
            data = json.loads(block)
        except Exception:  # noqa: BLE001
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(str(t).lower() in ("organization", "corporation", "localbusiness") for t in types if t):
                for key in ("name", "legalName", "alternateName"):
                    value = node.get(key)
                    if isinstance(value, str):
                        add(value)
    return found


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _similar(a: str, b: str) -> float:
    """Token overlap OR character similarity of the squashed spellings.

    Token overlap alone scores "CloudPeak Technologies" against "Cloud Peak Tech"
    at zero, because respacing a name changes every token. Real rebrand variants
    are usually one or the other, so the measure takes the stronger signal.
    """
    return max(_jaccard(a, b),
               SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio())


def adjudicate_site(site_dir: str, capture: dict) -> dict:
    """Return {category: 'yes'|'no'|'abstain'} plus the measurements behind it."""
    pages = []
    for entry in capture.get("pages", []):
        path = os.path.join(site_dir, entry["file"])
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            pages.append((entry, parse_page(fh.read(), entry.get("content_type", ""))))
    if not pages:
        return {"labels": {c: "abstain" for c in CATEGORIES}, "measurements": {"pages": 0}}

    entry_page = next((p for e, p in pages if e.get("is_entry")), pages[0][1])
    models = [p for _, p in pages]
    year = datetime.now(timezone.utc).year
    labels: dict[str, str] = {}
    measurements: dict[str, object] = {"pages": len(pages)}

    # crawlability -----------------------------------------------------
    robots_path = os.path.join(site_dir, "robots.txt")
    robots_blocks = False
    if os.path.exists(robots_path):
        with open(robots_path, encoding="utf-8", errors="replace") as fh:
            robots_txt = fh.read()
        agent_all = re.split(r"(?im)^user-agent:", robots_txt)
        for block in agent_all[1:]:
            head, _, body = block.partition("\n")
            if head.strip() == "*" or "gptbot" in head.lower() or "claudebot" in head.lower():
                if re.search(r"(?im)^\s*disallow:\s*/\s*$", body.split("user-agent:")[0]):
                    robots_blocks = True
    # noindex on a cart, login, account or search URL is correct practice, not a
    # discoverability defect, so it does not count against the site.
    transactional = re.compile(r"/(account|login|signin|sign-in|register|cart|checkout|basket|"
                               r"search|admin|wp-admin|my-?account|profile|logout)(/|\?|$)", re.I)
    noindexed = any(
        "noindex" in (m.metas.get("robots", "") + m.metas.get("googlebot", "")).lower()
        and not transactional.search(urlparse(e.get("final_url", "")).path or "")
        for e, m in pages
    )
    measurements["noindex"] = noindexed
    measurements["robots_blocks_all"] = robots_blocks
    labels["crawlability"] = "yes" if (noindexed or robots_blocks) else "no"

    # structured_data --------------------------------------------------
    blocks = [b for m in models for b in m.jsonld]
    broken = 0
    for block in blocks:
        try:
            json.loads(block)
        except Exception:  # noqa: BLE001
            broken += 1
    has_micro = any(m.has_microdata for m in models)
    measurements["jsonld_blocks"] = len(blocks)
    measurements["jsonld_broken"] = broken
    if broken or (not blocks and not has_micro):
        labels["structured_data"] = "yes"
    else:
        labels["structured_data"] = "no"

    # rendering --------------------------------------------------------
    shells = [m for m in models if _is_shell(m)]
    measurements["shell_pages"] = len(shells)
    measurements["entry_words"] = entry_page.words
    if shells:
        labels["rendering"] = "yes"
    elif entry_page.words >= 120:
        labels["rendering"] = "no"
    else:
        labels["rendering"] = "abstain"

    # content_extraction -----------------------------------------------
    entry_signals = _signals(entry_page)
    entry_images = len(_fact_images(entry_page, strict=False))
    measurements["entry_signals"] = entry_signals
    measurements["entry_fact_images"] = entry_images
    if entry_page.words < 60 and entry_signals <= 1 and (shells or entry_images >= 3):
        labels["content_extraction"] = "yes"
    elif entry_page.words >= 150 and entry_signals >= 2:
        labels["content_extraction"] = "no"
    else:
        labels["content_extraction"] = "abstain"

    # non_text_facts ---------------------------------------------------
    fact_images = sum(len(_fact_images(m)) for m in models)
    loose_images = sum(len(_fact_images(m, strict=False)) for m in models)
    measurements["fact_images"] = fact_images
    measurements["fact_images_loose"] = loose_images
    if fact_images >= 2:
        labels["non_text_facts"] = "yes"
    elif loose_images == 0:
        labels["non_text_facts"] = "no"
    else:
        labels["non_text_facts"] = "abstain"

    # freshness ---------------------------------------------------------
    # Years are read from *visible* text only. Scanning the raw document counted
    # version numbers, timestamps and ids inside inline scripts, which made
    # text-light modern sites look years out of date.
    stale, recent, copyrights, stale_pages = [], set(), [], 0
    for model in models:
        text = model.full_text
        page_stale, page_recent = [], set()
        for match in YEAR_RE.finditer(text):
            value = int(match.group(1))
            before = text[max(0, match.start() - 40):match.start()]
            if FOUNDING_RE.search(before):
                continue
            if value >= year:
                page_recent.add(value)
            elif value <= year - 2:
                page_stale.append(value)
        page_copyrights = _copyright_years(text)
        newest = max(page_copyrights) if page_copyrights else None
        copyrights.extend(page_copyrights)
        stale.extend(page_stale)
        recent |= page_recent
        if not page_recent and ((newest and newest <= year - 2) or len(page_stale) >= 3):
            stale_pages += 1
    newest_copyright = max(copyrights) if copyrights else None
    measurements.update({"stale_mentions": len(stale), "recent_years": sorted(recent),
                         "copyright_year": newest_copyright, "stale_pages": stale_pages})
    if stale_pages:
        labels["freshness"] = "yes"
    elif recent or (newest_copyright and newest_copyright >= year - 1):
        labels["freshness"] = "no"
    else:
        labels["freshness"] = "abstain"

    # entity_identity ---------------------------------------------------
    # Names are counted only when they are spellings of ONE organisation. The
    # anchor is the name recurring across pages (or the most central one); a
    # sub-page title such as "Online Rent Agreement" is a page topic, not a
    # competing entity name, and anchoring is what separates the two.
    counts: dict[str, int] = {}
    for model in models:
        for name in _names(model):
            counts[name] = counts.get(name, 0) + 1
    names = sorted(counts)
    if not names:
        labels["entity_identity"] = "abstain"
        measurements["names"] = []
    else:
        anchors = [n for n in names if counts[n] >= 2] or names
        canonical = max(anchors, key=lambda n: (sum(_similar(n, other) for other in names if other != n),
                                                counts[n], -len(n)))
        family = [canonical]
        for name in names:
            if name == canonical:
                continue
            if len(name.replace(" ", "")) > len(canonical.replace(" ", "")) * 2.2 + 4:
                continue
            if _similar(name, canonical) >= 0.5 or name.replace(" ", "")[:4] == canonical.replace(" ", "")[:4]:
                family.append(name)
        measurements["names"] = names
        measurements["canonical"] = canonical
        measurements["family"] = family
        measurements["variant_pairs"] = len(family) - 1
        # "no" is only claimed when the site speaks with exactly one name. Two
        # unrelated names may be a parent company, a product brand, or a conflict
        # this adjudicator cannot resolve from markup alone - all of which are
        # abstentions, not clean bills of health.
        if len(family) >= 3:
            labels["entity_identity"] = "yes"
        elif len(names) == 1:
            labels["entity_identity"] = "no"
        else:
            labels["entity_identity"] = "abstain"

    # engagement ---------------------------------------------------------
    measurable = [m for m in models if not _is_shell(m)]
    def internal_links(model: PageModel) -> int:
        count = 0
        for link in model.links:
            href = link["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            if href.startswith("/") or not urlparse(href).netloc:
                count += 1
        return count
    if not measurable:
        labels["engagement"] = "abstain"
    else:
        nav_counts = [m.nav_count for m in measurable]
        link_counts = [internal_links(m) for m in measurable]
        measurements["nav_counts"] = nav_counts
        measurements["internal_links"] = link_counts
        if all(n == 0 for n in nav_counts) and all(c < 2 for c in link_counts):
            labels["engagement"] = "yes"
        elif any(n >= 1 for n in nav_counts) and any(c >= 3 for c in link_counts):
            labels["engagement"] = "no"
        else:
            labels["engagement"] = "abstain"

    return {"labels": labels, "measurements": measurements}


# ---------------------------------------------------------------------------
# Scoring an agent report against adjudicated labels
# ---------------------------------------------------------------------------

def score_report(flagged: set[str], labels: dict[str, str]) -> dict:
    tp = fp = fn = tn = 0
    unverified = []
    for category in CATEGORIES:
        label = labels.get(category, "abstain")
        hit = category in flagged
        if label == "yes":
            tp += hit
            fn += not hit
        elif label == "no":
            fp += hit
            tn += not hit
        elif hit:
            unverified.append(category)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "unverified": unverified, "precision": precision, "recall": recall}


def validate_against_gold(gold_dir: str, adjudications: dict[str, dict]) -> dict:
    """How accurate is the adjudicator itself, on the labelled synthetic sites?"""
    agree = disagree = abstained = 0
    mistakes = []
    for filename in sorted(os.listdir(gold_dir)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(gold_dir, filename), encoding="utf-8") as fh:
            gold = json.load(fh)
        site_id = gold["site_id"]
        if site_id not in adjudications:
            continue
        expected = {f["category"] for f in gold.get("expected_findings", [])}
        labels = adjudications[site_id]["labels"]
        for category in CATEGORIES:
            label = labels.get(category, "abstain")
            truth = category in expected
            if label == "abstain":
                abstained += 1
            elif (label == "yes") == truth:
                agree += 1
            else:
                disagree += 1
                mistakes.append({"site": site_id, "category": category,
                                 "adjudicator": label, "gold": "yes" if truth else "no"})
    total = agree + disagree
    return {"agreement": round(agree / total, 4) if total else None,
            "agree": agree, "disagree": disagree, "abstained": abstained, "mistakes": mistakes}
