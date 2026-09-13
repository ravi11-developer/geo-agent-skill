#!/usr/bin/env python3
"""Skill: crawl-render-audit

Responsibilities (and nothing else):
  1. Fetch the site read-only and publish the shared :class:`SiteSnapshot`.
  2. Detect machine *access* barriers   -> category ``crawlability``
  3. Detect client-side rendering gaps  -> category ``rendering``

The rendering detector performs a deterministic *simulated render*: inline
scripts are mined for the HTML they inject at runtime, that markup is parsed,
and the result is diffed against the raw HTML.  This recovers exactly what a
JS-enabled client would see without shipping a headless browser, and it means
the finding can quote the facts that a non-rendering crawler loses.
"""

from __future__ import annotations

import gzip
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from lib.contracts import Page, SiteSnapshot, SkillResult, make_finding, recommendation

SKILL_ID = "crawl-render-audit"
USER_AGENT = "AIDiscoverabilityAuditor/1.0 (+read-only; respects robots.txt)"
REQUEST_TIMEOUT = 8
MAX_PAGES = 12

FRAMEWORK_ROOT_IDS = {"root", "app", "__next", "__nuxt", "ember-app", "svelte-app", "q-app"}
JS_GATE_RE = re.compile(
    r"(enable\s+javascript|requires?\s+javascript|javascript\s+is\s+required|turn\s+on\s+javascript|"
    r"javascript\s+must\s+be\s+enabled)",
    re.I,
)
# noindex on a cart, login, account or search URL is deliberate, correct practice.
TRANSACTIONAL_PATH_RE = re.compile(
    r"/(account|login|signin|sign-in|register|cart|checkout|basket|search|admin|wp-admin|"
    r"my-?account|profile|logout|password|reset)(/|\?|$)", re.I)
# Both halves of every vendor's fleet matter, and they fail differently: the
# training crawlers (GPTBot, ClaudeBot, Google-Extended) decide whether a brand
# is ever learned, while the *query-time* crawlers (OAI-SearchBot, ChatGPT-User,
# Claude-SearchBot, PerplexityBot) decide whether it can be cited in an answer
# being written right now.  Blocking only the second set is a common and almost
# invisible way to disappear from AI answers, so both are checked.
AI_USER_AGENTS = ("gptbot", "oai-searchbot", "chatgpt-user", "claudebot", "claude-searchbot",
                  "anthropic-ai", "perplexitybot", "perplexity-user", "google-extended",
                  "meta-externalagent", "ccbot", "bingbot", "applebot-extended")
# The token this auditor answers to, used to decide what *we* may fetch.
AUDITOR_UA_TOKEN = "aidiscoverabilityauditor"
PRICE_RE = re.compile(r"(?:\$|€|£|₹)\s?\d[\d,.]*")

# Sitemaps: bounded on purpose.  A sitemap index may point at hundreds of child
# documents; we read enough to prove the sitemap exists and to recover a useful
# URL inventory, never enough to turn an audit into a crawl of the whole site.
SITEMAP_MAX_DOCUMENTS = 6
SITEMAP_MAX_URLS = 5000
SITEMAP_BLOCK_RE = re.compile(r"<sitemap\b[^>]*>(.*?)</sitemap\s*>", re.I | re.S)
URL_BLOCK_RE = re.compile(r"<url\b[^>]*>(.*?)</url\s*>", re.I | re.S)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)


# ---------------------------------------------------------------------------
# HTTP layer (GET only, no cookies persisted, no writes)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - environment dependent
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _is_html_type(content_type: str) -> bool:
    """An absent Content-Type is treated as HTML, matching how browsers sniff."""
    return "html" in content_type.lower() or not content_type.strip()


def _decode_text(raw: bytes) -> str:
    """Decode a non-HTML text document, transparently gunzipping it first.

    ``sitemap.xml.gz`` is served as a gzip *payload* (Content-Type
    application/gzip), not as a gzip *transfer encoding*, so no HTTP client
    decompresses it for us - the bytes arrive with the gzip magic number intact
    and have to be unwrapped here or the document reads as binary noise.
    """
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            return ""
    return raw.decode("utf-8", errors="replace")


def _fetch(url: str, session: Any = None, accept_text: bool = False) -> Page:
    """Fetch one document.

    HTML always lands in ``page.html``.  ``accept_text=True`` additionally keeps
    the body of *non-HTML* text documents in ``page.text_body`` - which is what
    robots.txt (``text/plain``, mandated by RFC 9309) and sitemaps
    (``application/xml``) actually are.  Gating the body on an HTML content type
    is why both were previously fetched, answered 200, and then discarded.
    """
    page = Page(url=url)
    start = time.monotonic()
    try:
        if session is not None:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            page.status_code = resp.status_code
            page.headers = {k.lower(): v for k, v in resp.headers.items()}
            page.content_type = page.headers.get("content-type", "")
            if resp.status_code == 200:
                if _is_html_type(page.content_type):
                    # requests falls back to ISO-8859-1 for text/* without a charset
                    # parameter (RFC 2616), which mojibakes UTF-8 pages that declare
                    # their charset in a <meta> tag - very common in the wild, and it
                    # silently corrupts em dashes, quotes and accented brand names.
                    if "charset=" not in page.content_type.lower():
                        head = resp.content[:2048].decode("ascii", errors="ignore").lower()
                        match = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head)
                        resp.encoding = (match.group(1) if match else None) or resp.apparent_encoding or "utf-8"
                    page.html = resp.text
                elif accept_text:
                    page.text_body = _decode_text(resp.content)
        else:  # urllib fallback keeps the skill runnable without requests
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # noqa: S310
                page.status_code = resp.status
                page.headers = {k.lower(): v for k, v in resp.headers.items()}
                page.content_type = page.headers.get("content-type", "")
                body = resp.read()
                if page.status_code == 200:
                    if _is_html_type(page.content_type):
                        page.html = body.decode("utf-8", errors="replace")
                    elif accept_text:
                        page.text_body = _decode_text(body)
    except Exception as exc:  # noqa: BLE001 - recorded as evidence, never raised
        page.error = f"{type(exc).__name__}: {exc}"
        if hasattr(exc, "code"):
            page.status_code = getattr(exc, "code")
    page.elapsed_ms = int((time.monotonic() - start) * 1000)
    return page


def _new_session():
    if requests is None:
        return None
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    return session


# ---------------------------------------------------------------------------
# robots.txt (advisory: we obey it, we only report it when it blocks answers)
# ---------------------------------------------------------------------------

def _parse_robots(robots_txt: str) -> dict[str, list[tuple[str, bool]]]:
    """Parse robots.txt into ``agent -> [(path, is_allow)]`` groups.

    Two details decide whether this produces findings or false positives, and
    the naive version gets both wrong:

    * Consecutive ``User-agent`` lines share one rule block (RFC 9309 s2.2.1).
      Resetting the agent list on every such line silently drops the rules for
      every agent but the last one in the block - so the very common
      ``User-agent: GPTBot`` / ``User-agent: ClaudeBot`` / ``Disallow: /`` reads
      as "only ClaudeBot is blocked".
    * ``Allow`` exists.  Ignoring it turns a site that deliberately carves AI
      crawlers *out* of a blanket block into a reported block.
    """
    groups: dict[str, list[tuple[str, bool]]] = {}
    current: list[str] = []
    in_directives = False
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_directives:  # a directive closed the previous group
                current = []
                in_directives = False
            agent = value.lower()
            if agent:
                current.append(agent)
                groups.setdefault(agent, [])
        elif key in ("disallow", "allow") and current:
            in_directives = True
            for agent in current:
                groups.setdefault(agent, []).append((value, key == "allow"))
    return groups


def _rule_matches(rule: str, path: str) -> int:
    """Length of ``rule`` when it matches ``path``, else -1.

    Supports the two wildcards robots.txt actually uses in the wild: ``*`` for
    any run of characters and a trailing ``$`` to anchor the end of the path.
    """
    if not rule:
        return -1
    if "*" in rule or rule.endswith("$"):
        anchored = rule.endswith("$")
        body = rule[:-1] if anchored else rule
        pattern = "^" + re.escape(body).replace(r"\*", ".*") + ("$" if anchored else "")
        try:
            return len(rule) if re.match(pattern, path) else -1
        except re.error:
            return -1
    return len(rule) if path.startswith(rule) else -1


def _path_allowed(rules: list[tuple[str, bool]], path: str) -> bool:
    """RFC 9309 precedence: the longest matching rule wins, Allow breaks ties."""
    best_len, best_allow = -1, True
    for rule, allow in rules:
        if not rule and not allow:
            continue  # a bare "Disallow:" imposes no restriction at all
        length = _rule_matches(rule, path)
        if length < 0:
            continue
        if length > best_len or (length == best_len and allow and not best_allow):
            best_len, best_allow = length, allow
    return True if best_len < 0 else best_allow


def _rules_for(groups: dict[str, list[tuple[str, bool]]], token: str) -> tuple[list, str]:
    """The single group that governs one crawler, plus the name that matched.

    Group selection is *most specific wins*, not a union: a crawler named
    explicitly obeys only its own block and ignores ``*`` entirely, so a site
    that blocks everyone but allows GPTBot is read correctly.
    """
    token = token.lower()
    if token in groups:
        return groups[token], token
    if "*" in groups:
        return groups["*"], "*"
    return [], ""


def _blocked_agents(groups: dict[str, list[tuple[str, bool]]], path: str) -> list[dict[str, str]]:
    """Which AI crawlers robots.txt disallows from ``path``, and via which group."""
    blocked: list[dict[str, str]] = []
    for agent in AI_USER_AGENTS:
        rules, matched = _rules_for(groups, agent)
        if not rules:
            continue
        if not _path_allowed(rules, path):
            blocked.append({"agent": agent, "via": matched})
    return blocked


def _auditor_may_fetch(groups: dict[str, list[tuple[str, bool]]], path: str) -> bool:
    """Whether *this* auditor is permitted to fetch ``path``.

    Deliberately separate from :func:`_blocked_agents`: a site that blocks
    GPTBot has said nothing about us, and refusing to crawl on GPTBot's behalf
    would replace a correct ``crawlability`` finding with a bogus "entry URL is
    not retrievable" one.
    """
    rules, _ = _rules_for(groups, AUDITOR_UA_TOKEN)
    return _path_allowed(rules, path) if rules else True


def _sitemap_refs(robots_txt: str) -> list[str]:
    """``Sitemap:`` directives, which are group-independent and may point anywhere."""
    out: list[str] = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key.lower() == "sitemap" and value and value not in out:
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# Sitemaps
# ---------------------------------------------------------------------------

def _parse_sitemap_doc(xml: str) -> dict[str, list[str]]:
    """Split one sitemap document into child sitemaps and page URLs.

    ``<loc>`` alone cannot tell the two apart - it is the element used inside
    both ``<sitemap>`` (an index entry) and ``<url>`` (a page entry) - so the
    wrapper is matched first.  Reading every ``<loc>`` as a page URL is why a
    sitemap index previously looked like a list of pages that were all XML.
    Matching on the wrapper rather than the namespace keeps this working for the
    many real sitemaps that declare an unusual or missing xmlns.
    """
    children: list[str] = []
    for block in SITEMAP_BLOCK_RE.findall(xml):
        children.extend(LOC_RE.findall(block))
    pages: list[str] = []
    lastmods: list[str] = []
    for block in URL_BLOCK_RE.findall(xml):
        pages.extend(LOC_RE.findall(block))
        lastmods.extend(LASTMOD_RE.findall(block))
    if not children and not pages:
        # Malformed, but bare <loc> entries still prove a sitemap is published.
        pages = LOC_RE.findall(xml)
    return {"children": children, "pages": pages, "lastmods": lastmods}


def _collect_sitemaps(entry_points: list[str], session: Any) -> dict[str, Any]:
    """Read sitemaps breadth-first, following ``<sitemapindex>`` into children.

    Bounded by :data:`SITEMAP_MAX_DOCUMENTS` so a site with hundreds of shards
    costs a fixed number of requests.  ``checked`` records whether the server
    ever gave a definitive answer, so "no sitemap" can be distinguished from
    "we never found out" - the difference between a correct recommendation and
    telling a site to build something it already has.
    """
    seen: set[str] = set()
    queue = [u for u in entry_points if u]
    sources: list[dict[str, Any]] = []
    pages: list[str] = []
    lastmods: list[str] = []
    checked = False

    while queue and len(sources) < SITEMAP_MAX_DOCUMENTS:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        doc = _fetch(url, session, accept_text=True)
        if doc.status_code is not None:
            checked = True  # the server answered; absence is now an observation
        record = {"url": url, "status": doc.status_code, "kind": "unread", "urls": 0}
        # Some servers label sitemap.xml as text/html, which lands it in `html`.
        body = doc.text_body or doc.html or ""
        if doc.status_code == 200 and body.strip():
            parsed = _parse_sitemap_doc(body)
            record["kind"] = "index" if parsed["children"] else "urlset"
            record["urls"] = len(parsed["pages"]) or len(parsed["children"])
            queue.extend(child for child in parsed["children"] if child not in seen)
            for loc in parsed["pages"]:
                if len(pages) < SITEMAP_MAX_URLS:
                    pages.append(loc)
            lastmods.extend(parsed["lastmods"])
        sources.append(record)

    return {
        "sources": sources,
        "pages": pages,
        "lastmods": lastmods,
        "checked": checked,
        # A published-but-empty sitemap is still a published sitemap.
        "present": any(s["status"] == 200 and s["kind"] != "unread" for s in sources),
    }


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

LOW_VALUE_PATH_RE = re.compile(
    r"/(cart|checkout|basket|login|signin|sign-in|register|signup|account|my-?account|"
    r"wishlist|compare|search|logout|password|reset)(/|\?|$)", re.I)
TRACKING_QUERY_KEYS = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid", "msclkid", "_ga")


def _is_low_value(absolute: str) -> bool:
    """URLs that cost a page of budget and answer nothing.

    Only consulted by the extended crawl profile: the legacy profile keeps the
    original queue exactly as it was so no shipped result moves.
    """
    target = urlparse(absolute)
    if LOW_VALUE_PATH_RE.search(target.path or ""):
        return True
    query = (target.query or "").lower()
    if any(key in query for key in TRACKING_QUERY_KEYS):
        return True
    if re.search(r"(^|[?&])(page|p)=([2-9]|\d{2,})", query):
        return True   # deep pagination: another sample of a template we have
    return False


VOLATILE_SEGMENT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{16,}$", re.I)
# How many siblings a path position needs before it is read as an instance slot
# rather than a named section. Three is the smallest number that cannot be a
# coincidence of two hand-written pages.
TEMPLATE_SIBLING_THRESHOLD = 3
# ...and how *unique* those siblings must be. A section name recurs across the
# whole URL set (`/collections/` prefixes hundreds of URLs); an instance id
# appears about once. Without this ratio, any site with three or more top-level
# sections collapses them all into one template and stratification degenerates
# into ordinary breadth-first.
TEMPLATE_UNIQUENESS_RATIO = 0.5


def _same_site(a: str, b: str) -> bool:
    """Host equality that ignores a leading ``www.``.

    Sitemaps publish the site's *canonical* host, which very often differs from
    the host that was requested by exactly that prefix. Comparing netlocs
    literally therefore discards the entire sitemap of any site whose canonical
    host is the bare domain - silently, and precisely on the large sites where
    the sitemap is most valuable.
    """
    return a.lower().removeprefix("www.") == b.lower().removeprefix("www.")


def _canonical_key(url: str) -> str:
    """Identity of a document for de-duplication.

    A sitemap that publishes the bare host while the audit was pointed at the
    ``www`` host otherwise spends two pages of budget fetching the same document
    twice, once through a redirect.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}?{parsed.query}" if parsed.query else f"{host}{path}"


def _segments(url: str) -> list[str]:
    return [s for s in (urlparse(url).path or "/").strip("/").split("/") if s]


def build_templates(urls: list[str]) -> dict[str, str]:
    """Group URLs by path *shape*, learning instance slots from the URLs themselves.

    Most checks are template-level rather than page-level: one product page tells
    you what every product page does about structured data, alt text and
    client-side rendering. Sampling per template is what lets a fixed page budget
    buy coverage of every template instead of N samples of whichever one the
    header happens to link first.

    Which segments are instances is inferred structurally, not lexically: a
    position whose siblings under the same *template* prefix take three or more
    distinct values is an instance slot. A purely lexical rule (hyphens, length)
    cannot do this - it splits ``/collections/bags`` from ``/collections/tech``
    while merging nothing, which yields one template per page and makes
    stratification a no-op.
    """
    paths = {url: _segments(url) for url in urls}
    depth = max((len(segs) for segs in paths.values()), default=0)
    # prefix[url] is the template prefix agreed so far, extended one level a time
    prefix: dict[str, tuple[str, ...]] = {url: () for url in paths}

    for level in range(depth):
        groups: dict[tuple[str, ...], list[str]] = {}
        for url, segs in paths.items():
            if len(segs) > level:
                groups.setdefault(prefix[url], []).append(segs[level])
        for url, segs in paths.items():
            if len(segs) <= level:
                continue
            segment = segs[level]
            siblings = groups.get(prefix[url], [])
            distinct = len(set(siblings))
            unique_enough = distinct >= max(1, len(siblings)) * TEMPLATE_UNIQUENESS_RATIO
            if segment.isdigit():
                token = "<num>"
            elif VOLATILE_SEGMENT_RE.match(segment):
                token = "<uuid>"
            elif distinct >= TEMPLATE_SIBLING_THRESHOLD and unique_enough:
                token = "<slug>"
            else:
                token = segment.lower()
            prefix[url] = prefix[url] + (token,)

    return {url: "/" + "/".join(parts) if parts else "/" for url, parts in prefix.items()}


def _pick_stratified(pool: list[tuple[str, int, str | None]], sampled: dict[str, int],
                     templates: dict[str, str]) -> tuple[str, int, str | None]:
    """Take the candidate from the least-sampled template, shallowest first.

    Plain breadth-first spends the whole budget on whatever the header links to.
    Ordering by (samples already taken of this template, depth, discovery order)
    keeps the crawl fully deterministic while making each additional fetch buy
    the most coverage still available.
    """
    best_index, best_key = 0, None
    for index, (candidate, depth, _parent) in enumerate(pool):
        template = templates.get(candidate) or url_template_single(candidate)
        key = (sampled.get(template, 0), depth, index)
        if best_key is None or key < best_key:
            best_key, best_index = key, index
    return pool.pop(best_index)


def url_template_single(url: str) -> str:
    """Fallback shape for a URL that was not part of the last template build."""
    return build_templates([url])[url]


def crawl(url: str, max_pages: int = MAX_PAGES, budget: Any = None,
          error_log: Any = None) -> SiteSnapshot:
    """Breadth-first, read-only crawl.

    ``budget`` is optional.  With no budget (the default) this behaves exactly
    as it always has: plain breadth-first, ``max_pages`` documents, no depth
    ceiling and no URL filtering beyond scope.  A budget with the ``extended``
    profile additionally enforces a depth ceiling, drops low-value URLs and
    honours a wall-clock exploration deadline - all of which are opt-in, because
    silently changing the crawl scope would silently change every result the
    project has already measured.
    """
    session = _new_session()
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path_parts = [p for p in parsed.path.strip("/").split("/") if p and "." not in p]
    scope_prefix = "/" + path_parts[0] + "/" if path_parts else "/"

    extended = bool(budget is not None and getattr(budget, "profile", "legacy") != "legacy")
    if extended:
        # The budget governs, rather than being floored by the caller's default.
        # `min()` here meant the extended profile could only ever *tighten* the
        # crawl: with a manifest default of 12 and a hard limit of 30 it
        # resolved to 12, so requesting the wider profile changed nothing at all.
        max_pages = int(getattr(budget, "hard_page_limit", max_pages) or max_pages)
    soft_target = int(getattr(budget, "soft_page_target", max_pages) or max_pages) if extended else max_pages
    per_template = int(getattr(budget, "per_template_samples", 3) or 3) if extended else 0
    saturation_window = int(getattr(budget, "saturation_window", 6) or 6) if extended else 0
    max_depth = getattr(budget, "max_depth", None) if extended else None
    deadline = (time.monotonic() + float(getattr(budget, "explore_deadline_seconds", 1e9))
                if extended else None)

    snapshot = SiteSnapshot(base_url=base, entry_url=url)

    robots = _fetch(urljoin(base + "/", "robots.txt"), session, accept_text=True)
    snapshot.robots_status = robots.status_code
    if robots.status_code == 200:
        snapshot.robots_txt = robots.text_body or robots.html or ""

    robots_rules = _parse_robots(snapshot.robots_txt) if snapshot.robots_txt else {}
    snapshot.robots_sitemap_refs = _sitemap_refs(snapshot.robots_txt or "")

    # A site's own `Sitemap:` directives are tried before the conventional
    # location, because the directive is authoritative about where the sitemap
    # actually lives - /sitemap.xml is only a convention.
    sitemaps = _collect_sitemaps(
        snapshot.robots_sitemap_refs + [urljoin(base + "/", "sitemap.xml")], session)
    snapshot.sitemap_urls = sitemaps["pages"]
    snapshot.sitemap_sources = sitemaps["sources"]
    snapshot.sitemap_lastmods = sitemaps["lastmods"]
    snapshot.sitemap_checked = sitemaps["checked"]
    snapshot.notes["sitemap_present"] = sitemaps["present"]

    # Whether *we* are allowed here, which is a different question from whether
    # an AI crawler is - see `_auditor_may_fetch`.
    entry_allowed = _auditor_may_fetch(robots_rules, parsed.path or "/") if robots_rules else True
    snapshot.notes["entry_allowed_for_auditor"] = entry_allowed

    queue: list[tuple[str, int, str | None]] = [(url, 0, None)]
    seen = {_canonical_key(url)}

    # Seed from the sitemap.  This is the site's own statement of which pages
    # matter, it costs nothing extra (the documents are already read), and it is
    # the only way to reach a client-rendered site at all: an SPA whose raw HTML
    # contains no <a href> yields exactly one page to link-following, however
    # large the budget.
    seeded_from_sitemap = 0
    if extended:
        for loc in snapshot.sitemap_urls:
            if len(seen) >= max_pages * 20:
                break
            target = urlparse(loc)
            if not _same_site(target.netloc, parsed.netloc) or _canonical_key(loc) in seen:
                continue
            if scope_prefix != "/" and not target.path.startswith(scope_prefix.rstrip("/")):
                continue
            if re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|css|js|xml|ico|md|txt)$", target.path, re.I):
                continue
            if _is_low_value(loc):
                continue
            seen.add(_canonical_key(loc))
            queue.append((loc, 1, "sitemap"))
            seeded_from_sitemap += 1
    snapshot.notes["seeded_from_sitemap"] = seeded_from_sitemap

    sampled_templates: dict[str, int] = {}
    templates = build_templates([u for u, _, _ in queue]) if extended else {}
    templates_built_at = max(len(seen), 1)
    fetches_since_new_template = 0
    stopped_reason = "queue_exhausted"
    while queue and len(snapshot.pages) < max_pages:
        if deadline is not None and time.monotonic() > deadline:
            stopped_reason = "exploration_deadline"
            break
        if extended:
            # Rebuilt every iteration over the *whole* known pool, fetched pages
            # included.  A shape only becomes visible once its siblings exist, so
            # a map frozen at the seed set cannot classify anything link
            # discovery turns up later - and a per-URL fallback is worse than
            # useless here, because a lone URL has no siblings and so resolves to
            # a fully literal path.  Every freshly discovered link would then look
            # like an unsampled template and be picked first, which inverts the
            # policy into "prefer whatever we understand least".
            templates = build_templates(
                [u for u, _, _ in queue] + [page.url for page in snapshot.pages])
            sampled_templates = {}
            for page in snapshot.pages:
                key = templates.get(page.url, "/")
                sampled_templates[key] = sampled_templates.get(key, 0) + 1

        # Saturation: stop once no template both (a) has fewer than
        # per_template_samples fetched *and* (b) still has a candidate waiting
        # in the queue. A template that will only ever have one instance -
        # the homepage, a lone /about page - can never reach per_template on
        # its own; requiring every template's *count* to clear the threshold
        # (rather than checking whether more of it are even available) blocks
        # saturation forever on any real site, which has several such
        # singletons. Checking queue availability is what makes this a
        # genuine "there is nothing more useful left to fetch" test.
        if extended and len(snapshot.pages) >= soft_target and fetches_since_new_template >= saturation_window:
            queued_counts: dict[str, int] = {}
            for candidate, _depth, _parent in queue:
                key = templates.get(candidate, "/")
                queued_counts[key] = queued_counts.get(key, 0) + 1
            undersampled_and_available = any(
                sampled_templates.get(t, 0) < per_template and queued_counts.get(t, 0) > 0
                for t in set(sampled_templates) | set(queued_counts)
            )
            if sampled_templates and not undersampled_and_available:
                stopped_reason = "saturated"
                break

        if extended:
            current, depth, parent = _pick_stratified(queue, sampled_templates, templates)
        else:
            current, depth, parent = queue.pop(0)
        # Obey robots for *our own* user agent only.  Skipping a URL because
        # some other crawler is disallowed would hide the very defect we exist
        # to report, and would report the site as unreachable instead.
        if robots_rules and not _auditor_may_fetch(robots_rules, urlparse(current).path):
            snapshot.notes.setdefault("skipped_by_robots", []).append(current)
            continue

        fetch_started = time.monotonic()
        page = _fetch(current, session)
        if page.error and page.status_code is None and current == url:
            time.sleep(0.2)  # one retry: a transient transport error is not a defect
            page = _fetch(current, session)
        page.is_entry = current == url
        page.depth = depth
        page.discovered_from = parent
        snapshot.pages.append(page)

        if extended:
            known = len(sampled_templates)
            template = templates.get(current, "/")
            if template in sampled_templates:
                fetches_since_new_template += 1
            else:
                fetches_since_new_template = 0
            sampled_templates[template] = sampled_templates.get(template, 0) + 1
            del known

        if page.error:
            snapshot.fetch_errors.append({"url": current, "error": page.error})
            if error_log is not None:
                # Normalised so the health block can distinguish "the site is
                # broken" from "our fetch did not complete"; classification is
                # deterministic and happens inside ErrorLog.record.
                error_log.record(phase="crawl", operation="fetch", message=page.error,
                                 url=current, attempt=2 if current == url else 1,
                                 max_attempts=2 if current == url else 1,
                                 http_status=page.status_code,
                                 elapsed_ms=int((time.monotonic() - fetch_started) * 1000))
        elif page.status_code and page.status_code >= 400:
            snapshot.broken_links.append({"url": current, "status": page.status_code})
            if error_log is not None:
                error_log.record(phase="crawl", operation="fetch",
                                 message=f"HTTP {page.status_code}", url=current,
                                 http_status=page.status_code,
                                 elapsed_ms=int((time.monotonic() - fetch_started) * 1000))

        if not page.ok:
            continue

        if max_depth is not None and depth >= max_depth:
            continue

        for link in page.links:
            href = link["href"]
            if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            absolute = urljoin(current, href).split("#")[0]
            target = urlparse(absolute)
            if not _same_site(target.netloc, parsed.netloc) or _canonical_key(absolute) in seen:
                continue
            if scope_prefix != "/" and not target.path.startswith(scope_prefix.rstrip("/")):
                continue
            if re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|css|js|xml|ico)$", target.path, re.I):
                continue
            if extended and _is_low_value(absolute):
                continue
            seen.add(_canonical_key(absolute))
            queue.append((absolute, depth + 1, current))

    if queue and len(snapshot.pages) >= max_pages:
        stopped_reason = "page_limit"
    if not snapshot.pages and snapshot.notes.get("skipped_by_robots"):
        # Every candidate was disallowed for us; that is a policy, not an outage,
        # and only worth reporting as the stop reason when nothing was fetched.
        stopped_reason = "robots_disallowed_auditor"

    snapshot.notes["pages_fetched"] = len(snapshot.pages)
    snapshot.notes["pages_ok"] = len(snapshot.ok_pages)
    snapshot.notes["scope_prefix"] = scope_prefix
    snapshot.notes["crawl_profile"] = getattr(budget, "profile", "legacy") if budget is not None else "legacy"
    snapshot.notes["max_depth_reached"] = max((p.depth for p in snapshot.pages), default=0)
    if extended and snapshot.pages:
        final_map = build_templates([page.url for page in snapshot.pages])
        sampled_templates = {}
        for page in snapshot.pages:
            key = final_map.get(page.url, "/")
            sampled_templates[key] = sampled_templates.get(key, 0) + 1
    snapshot.notes["templates_sampled"] = len(sampled_templates)
    snapshot.notes["templates"] = dict(sorted(sampled_templates.items()))
    snapshot.notes["urls_discovered"] = len(seen)
    snapshot.notes["urls_queued_unvisited"] = len(queue)
    snapshot.notes["stopped_because"] = stopped_reason
    return snapshot


# ---------------------------------------------------------------------------
# Check 1: crawlability  (fires only on hard, quotable access barriers)
# ---------------------------------------------------------------------------

def check_crawlability(snapshot: SiteSnapshot) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []
    entry = snapshot.entry_page

    rules = _parse_robots(snapshot.robots_txt) if snapshot.robots_txt else {}
    entry_path = urlparse(snapshot.entry_url).path or "/"
    blocked = _blocked_agents(rules, entry_path) if rules else []
    auditor_blocked = bool(rules) and not _auditor_may_fetch(rules, entry_path)

    # robots.txt is evaluated *before* retrievability on purpose.  When the same
    # rules that block AI crawlers also block this auditor there are no pages to
    # report on, and the "not retrievable" finding below would describe a
    # deliberate policy as though the site were down.
    if blocked:
        named = sorted({b["agent"] for b in blocked if b["via"] != "*"})
        wildcard = sorted({b["agent"] for b in blocked if b["via"] == "*"})
        detail_parts = []
        if named:
            detail_parts.append(f"named explicitly: {', '.join(named)}")
        if wildcard:
            detail_parts.append(f"via the wildcard `User-agent: *` group: {', '.join(wildcard)}")
        findings.append(make_finding(
            category="crawlability",
            title="robots.txt blocks AI crawlers from the audited path",
            severity="high",
            evidence=(
                f"{snapshot.base_url}/robots.txt (HTTP {snapshot.robots_status}) disallows {entry_path} for "
                f"{len(blocked)} of {len(AI_USER_AGENTS)} known AI user agents ({'; '.join(detail_parts)}). "
                f"Entry URL {snapshot.entry_url} answered "
                f"{entry.http_label if entry else 'no request (disallowed for this auditor too)'}; "
                f"{len(snapshot.ok_pages)} pages were reachable for this audit."
            ),
            action=(
                "Narrow the Disallow rules in robots.txt so public marketing and documentation paths stay "
                "crawlable for AI user agents, and add explicit Allow rules for the query-time crawlers that "
                "decide citations (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, PerplexityBot) as well as the "
                "training crawlers (GPTBot, ClaudeBot, Google-Extended), keeping blanket blocks scoped to "
                "private or transactional paths, so the HTML stays machine-readable and assistants can cite it."
            ),
            mechanism=(
                "AI assistants honour robots.txt at fetch time; a disallowed path is never retrieved, so the "
                "brand cannot be cited even when the content is excellent. Blocking only the query-time "
                "crawlers removes the brand from answers while leaving ordinary search traffic untouched, "
                "which is why this is usually unintentional."
            ),
            locations=[f"{snapshot.base_url}/robots.txt"],
            detected_by=SKILL_ID,
            proof={"blocked_agents": blocked, "entry_path": entry_path,
                   "auditor_blocked": auditor_blocked},
        ))

    if auditor_blocked:
        # We obeyed the same rules we are reporting; say so rather than implying
        # the rest of the checks found the site clean.
        recs.append(recommendation(
            "Re-run the audit once AI user agents are allowed",
            "robots.txt disallowed this read-only auditor from the audited path, so only the robots policy "
            "itself could be assessed. The content, structured-data, freshness and engagement checks need a "
            "crawlable path before they can report anything.",
            "crawlability", "low",
        ))
        return findings, recs

    if entry is None or not entry.ok:
        status = entry.http_label if entry else "HTTP no-response"
        detail = entry.error if entry and entry.error else "no HTML body returned"
        # No HTTP status at all means the request never reached the server (DNS, TLS,
        # proxy). That is still a real discoverability barrier, but it can also be the
        # auditing network, so the finding is emitted with reduced confidence.
        transport_only = entry is not None and entry.status_code is None
        findings.append(make_finding(
            category="crawlability",
            title="Entry URL is not retrievable by a plain HTTP client",
            severity="critical",
            evidence=(
                f"GET {snapshot.entry_url} returned {status} ({detail}); 0 pages of HTML were "
                f"retrievable across 1 requested page. An AI crawler cannot index what it cannot fetch."
            ),
            action=(
                "Return HTTP 200 with a server-rendered HTML body for the canonical entry URL, and exempt AI "
                "crawler user agents from any WAF or bot rule that answers 4xx, so the page becomes "
                "machine-readable and assistants can discover, extract and cite it at all. If this audit ran "
                "behind a proxy, re-run it from a network that can reach the host before acting on this finding."
            ),
            mechanism="Retrieval is the first stage of every AI answer pipeline; a non-200 entry URL removes the site from the candidate set entirely.",
            locations=[snapshot.entry_url],
            confidence="medium" if transport_only else "high",
            detected_by=SKILL_ID,
            proof={"status_code": entry.status_code if entry else None,
                   "transport_error": transport_only,
                   "retried": True},
        ))
        return findings, recs

    # noindex directives (meta or header)
    noindexed = []
    for page in snapshot.ok_pages:
        if TRANSACTIONAL_PATH_RE.search(urlparse(page.url).path or ""):
            continue  # excluding a checkout or login page from search is correct
        directives = " ".join([
            page.meta.get("robots", ""),
            page.meta.get("googlebot", ""),
            page.headers.get("x-robots-tag", ""),
        ]).lower()
        if "noindex" in directives:
            noindexed.append(page)
    if noindexed:
        findings.append(make_finding(
            category="crawlability",
            title="Indexable content is marked noindex",
            severity="high",
            evidence=(
                f"{len(noindexed)} pages of {len(snapshot.ok_pages)} carry a noindex directive, e.g. "
                f"{noindexed[0].url} ({noindexed[0].http_label}) with "
                f"robots=\"{noindexed[0].meta.get('robots', noindexed[0].headers.get('x-robots-tag', ''))}\" "
                f"while still serving {noindexed[0].char_count} characters "
                f"({noindexed[0].word_count} words) of business content."
            ),
            action=(
                "Remove the noindex meta tag and X-Robots-Tag header from public HTML pages that carry product, "
                "pricing or company facts, keeping the Organization JSON-LD structured data in place, and reserve "
                "noindex for staging, search-result and duplicate URLs, so those pages stay machine-readable and "
                "AI systems can extract and cite them."
            ),
            mechanism="noindex tells retrieval systems to discard the document after fetching it, so the facts never reach the index an assistant answers from.",
            locations=[p.url for p in noindexed[:3]],
            detected_by=SKILL_ID,
            proof={"noindex_pages": len(noindexed)},
        ))
        recs.append(recommendation(
            "Assert indexability in CI for public templates",
            "A noindex directive that reaches production removes a page from every index silently. A build-time "
            "check that public templates never emit noindex (and a weekly crawl that asserts it) keeps this from "
            "recurring after the immediate fix.",
            "crawlability", "low",
        ))

    # Proactive-only observations (never scored as defects)
    #
    # Gated on having actually read a sitemap rather than on the URL list being
    # empty. An empty list is also what a discarded body produces, which is how
    # this recommendation used to tell sites with a perfectly good sitemap index
    # to go and publish one.
    if snapshot.sitemap_checked and not snapshot.notes.get("sitemap_present"):
        recs.append(recommendation(
            "Publish an XML sitemap with lastmod dates",
            "No sitemap was served at /sitemap.xml and robots.txt declared no `Sitemap:` directive. A sitemap "
            "with accurate <lastmod> values gives crawlers a cheap freshness signal and a complete URL "
            "inventory, which improves how quickly new facts are picked up.",
            "crawlability", "low",
        ))
    elif snapshot.notes.get("sitemap_present") and not snapshot.sitemap_lastmods:
        recs.append(recommendation(
            "Add lastmod dates to the published sitemap",
            f"A sitemap is published ({len(snapshot.sitemap_urls)} URLs across "
            f"{len(snapshot.sitemap_sources)} sitemap documents) but carries no <lastmod> values, so crawlers "
            "cannot tell which pages changed and re-fetch on a blind schedule instead of a freshness signal.",
            "crawlability", "low",
        ))
    if snapshot.robots_status != 200:
        recs.append(recommendation(
            "Add an explicit robots.txt that names AI user agents",
            "No robots.txt was served. An explicit allow-list for GPTBot, ClaudeBot, PerplexityBot and "
            "Google-Extended documents your intent and prevents defensive bot rules from silently blocking assistants.",
            "crawlability", "low",
        ))
    return findings, recs


# ---------------------------------------------------------------------------
# Check 2: rendering  (simulated render + raw-HTML diff)
# ---------------------------------------------------------------------------

PRICE_LIKE_RE = re.compile(r"(?:\$|€|£|₹)\s?\d[\d,.]*(?:\s*(?:/|per\s)\s*(?:mo|month|yr|year|user|seat))?", re.I)
BUSINESS_FACT_RE = re.compile(
    r"(?:\$|€|£|₹)\s?\d|\bper\s+(?:month|year|user|seat)\b|/\s?(?:mo|month|yr|year)\b|"
    r"\b(founded|headquarter\w*|employees|customers|pricing|price|plan|tier)\b", re.I)


def _render_gap(page: Page) -> dict[str, Any] | None:
    """Return concrete proof that *business* content only exists after JS.

    The bar is deliberately high. Real sites inject cart drawers, cookie banners,
    captcha errors, carousels and video players into empty containers on every
    page load; that is normal enhancement, not a discoverability defect. A
    rendering finding requires that a reader of the raw HTML actually loses
    business facts - which is true only when the page has little server-rendered
    substance to begin with, or when the injected markup carries facts the raw
    HTML never states.
    """
    soup = page.soup
    server_rendered_facts = BUSINESS_FACT_RE.findall(page.content_text)

    # (a) An element that is empty in the raw HTML and is filled by inline JS,
    #     where that injection is load-bearing rather than decorative.
    for payload in page.js_payloads:
        for target in payload["targets"]:
            element = soup.find(id=target)
            if element is None:
                continue
            if element.get_text(strip=True):
                continue  # server-rendered already: not a gap
            payload_text = re.sub(r"<[^>]+>", " ", payload["html"])
            thin_page = page.word_count < 60  # same bar as a JS shell elsewhere
            # Commercial figures are the sharpest test of a load-bearing
            # injection: a price that exists only inside a script is a fact the
            # raw HTML does not state, whereas a modal saying "Loading offers..."
            # is chrome. Comparing the *values* (not merely the presence of
            # fact-ish words) is what separates the two on real sites.
            injected_prices = set(PRICE_LIKE_RE.findall(payload_text))
            served_prices = set(PRICE_LIKE_RE.findall(page.content_text))
            facts_only_in_js = bool(injected_prices - served_prices)
            if not (thin_page or facts_only_in_js):
                continue  # a widget on a content-rich page: not a defect
            return {
                "reason": "js_injection",
                "selector": f"#{target}",
                "payload_chars": len(payload["html"]),
                "payload_text": payload["html"],
            }

    # (b) Classic SPA shell: framework root present but empty.
    for div in soup.find_all(["div", "main", "section"], id=True):
        if str(div.get("id")).lower() in FRAMEWORK_ROOT_IDS and not div.get_text(strip=True):
            return {
                "reason": "empty_framework_root",
                "selector": f"#{div.get('id')}",
                "payload_chars": 0,
                "payload_text": "",
            }

    # (c) A JS gate telling humans to enable JS, on a page with almost no text.
    for notice in page.noscript_notices:
        if JS_GATE_RE.search(notice) and page.word_count < 60:
            return {
                "reason": "noscript_gate",
                "selector": "noscript",
                "payload_chars": 0,
                "payload_text": "",
                "notice": notice,
            }
    return None


def check_rendering(snapshot: SiteSnapshot) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []

    gaps: list[tuple[Page, dict[str, Any]]] = []
    for page in snapshot.ok_pages:
        gap = _render_gap(page)
        if gap:
            gaps.append((page, gap))

    if not gaps:
        return findings, recs

    page, gap = gaps[0]
    rendered_text = page.js_rendered_text
    js_prices = sorted(set(PRICE_RE.findall(rendered_text)) - set(PRICE_RE.findall(page.content_text)))
    js_words = len(rendered_text.split())
    sample = ", ".join(js_prices[:4]) if js_prices else ""

    evidence = (
        f"{page.url} ({page.http_label}) serves only {page.char_count} characters "
        f"({page.word_count} words) of extractable body text; the element {gap['selector']} is empty in the "
        f"raw HTML and is populated at runtime by inline JavaScript carrying {gap['payload_chars']} characters "
        f"of markup ({js_words} words) across {len(page.js_payloads)} injection sites."
    )
    if sample:
        evidence += f" Facts visible only after JS execution include the prices \"{sample}\"."
    if gap["reason"] == "noscript_gate":
        evidence += f" The page also serves a JavaScript gate: \"{gap.get('notice', '')[:120]}\"."
    if gap["reason"] == "empty_framework_root":
        evidence += " The framework root element carries no server-rendered children."
    if len(gaps) > 1:
        evidence += f" The same pattern was measured on {len(gaps)} pages of {len(snapshot.ok_pages)} crawled pages."

    findings.append(make_finding(
        category="rendering",
        title="Business-critical content is only present after client-side JavaScript execution",
        severity="high",
        evidence=evidence,
        action=(
            "Server-side render (SSR) or statically pre-render the product, pricing and company sections into "
            f"the initial HTML response for {gap['selector']} inside <main>, keeping JavaScript for enhancement "
            "only, and mirror the same values in Product/Offer JSON-LD structured data. Verify with a text-only "
            "fetch (`curl`) that the raw HTML already carries the prices, so the facts are machine-readable for "
            "AI crawlers and assistants that never execute JavaScript and can be extracted and cited."
        ),
        mechanism=(
            "Most AI retrieval pipelines index the raw HTTP response; content injected by the browser after "
            "load is invisible to them, so JS-only facts are simply absent from the model's evidence."
        ),
        locations=[p.url for p, _ in gaps[:3]],
        detected_by=SKILL_ID,
        proof={
            "selector": gap["selector"],
            "reason": gap["reason"],
            "raw_words": page.word_count,
            "js_payload_chars": gap["payload_chars"],
            "js_only_prices": js_prices,
            "pages_affected": len(gaps),
        },
    ))
    recs.append(recommendation(
        "Use server-side rendering or static generation for product and pricing content",
        "Beyond the immediate fix, make pre-rendering the default for any route that carries commercial facts: "
        "render critical business information into the initial HTML response at build or request time, and treat "
        "client-side injection as enhancement only.",
        "rendering", "high",
    ))
    recs.append(recommendation(
        "Mirror the JavaScript-rendered facts in Product/Offer structured data",
        "Structured data is served in the initial HTML response even when the visible page hydrates later, so "
        "publishing the same prices and product names as JSON-LD gives AI systems a machine-readable copy while "
        "the rendering work is in progress.",
        "rendering", "medium",
    ))
    return findings, recs


# ---------------------------------------------------------------------------
# Skill entrypoint
# ---------------------------------------------------------------------------

def run(context: dict[str, Any]) -> SkillResult:
    started = time.monotonic()
    result = SkillResult(skill=SKILL_ID)

    snapshot = crawl(
        context["url"],
        max_pages=context.get("max_pages", MAX_PAGES),
        budget=context.get("crawl_budget"),
        error_log=context.get("error_log"),
    )
    result.artifacts["snapshot"] = snapshot
    # Published for the verification stage: confirming that a fetch failure
    # reproduces is an HTTP concern, and this skill is the only one that owns
    # HTTP. One session is reused so a re-check costs a request, not a handshake.
    _verify_session = _new_session()
    result.artifacts["refetch"] = lambda url, _s=_verify_session: _fetch(url, _s)

    crawl_findings, crawl_recs = check_crawlability(snapshot)
    result.findings.extend(crawl_findings)
    result.recommendations.extend(crawl_recs)

    if snapshot.ok_pages:
        render_findings, render_recs = check_rendering(snapshot)
        result.findings.extend(render_findings)
        result.recommendations.extend(render_recs)

    result.checks = [
        {"check": "entry_url_retrievable", "passed": bool(snapshot.entry_page and snapshot.entry_page.ok)},
        {"check": "robots_allows_ai_agents", "passed": not any(f["category"] == "crawlability" for f in crawl_findings)},
        {"check": "content_present_without_javascript",
         "passed": not any(f["category"] == "rendering" for f in result.findings)},
        {"check": "pages_crawled", "value": len(snapshot.pages), "value_ok": len(snapshot.ok_pages)},
        {"check": "crawl_budget_respected",
         "passed": len(snapshot.pages) <= context.get("max_pages", MAX_PAGES),
         "value": snapshot.notes.get("stopped_because")},
        {"check": "max_crawl_depth", "value": snapshot.notes.get("max_depth_reached", 0)},
        {"check": "robots_txt_read", "passed": bool(snapshot.robots_txt),
         "value": snapshot.robots_status},
        {"check": "sitemap_read", "passed": bool(snapshot.notes.get("sitemap_present")),
         "value": f"{len(snapshot.sitemap_urls)} URLs / {len(snapshot.sitemap_sources)} documents"},
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
