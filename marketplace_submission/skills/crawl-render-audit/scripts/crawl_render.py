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

import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from lib.contracts import Page, SiteSnapshot, SkillResult, make_finding, recommendation

SKILL_ID = "crawl-render-audit"
USER_AGENT = "AIDiscoverabilityAuditor/1.0 (+read-only; respects robots.txt)"
REQUEST_TIMEOUT = 8
MAX_PAGES = 12
# A single pathological document must not consume the whole audit budget: bodies
# are streamed and cut at this size, and the crawl stops once the wall-clock
# budget is spent. Both are declared in marketplace.json -> safety.
MAX_BYTES = 5_000_000
DEFAULT_BUDGET_SECONDS = 60

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
AI_USER_AGENTS = ("gptbot", "claudebot", "anthropic-ai", "perplexitybot", "google-extended",
                  "ccbot", "bingbot", "applebot-extended", "oai-searchbot")
PRICE_RE = re.compile(r"(?:\$|€|£|₹)\s?\d[\d,.]*")


# ---------------------------------------------------------------------------
# HTTP layer (GET only, no cookies persisted, no writes)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - environment dependent
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _fetch(url: str, session: Any = None) -> Page:
    page = Page(url=url)
    start = time.monotonic()
    try:
        if session is not None:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
            page.status_code = resp.status_code
            page.headers = {k.lower(): v for k, v in resp.headers.items()}
            page.content_type = page.headers.get("content-type", "")
            if resp.status_code == 200 and ("html" in page.content_type or not page.content_type):
                chunks, total = [], 0
                for chunk in resp.iter_content(65536):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BYTES:
                        page.truncated = True
                        break
                resp._content = b"".join(chunks)
                resp._content_consumed = True
                # requests falls back to ISO-8859-1 for text/* without a charset
                # parameter (RFC 2616), which mojibakes UTF-8 pages that declare
                # their charset in a <meta> tag - very common in the wild, and it
                # silently corrupts em dashes, quotes and accented brand names.
                if "charset=" not in page.content_type.lower():
                    head = resp.content[:2048].decode("ascii", errors="ignore").lower()
                    match = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head)
                    resp.encoding = (match.group(1) if match else None) or resp.apparent_encoding or "utf-8"
                page.html = resp.text
        else:  # urllib fallback keeps the skill runnable without requests
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # noqa: S310
                page.status_code = resp.status
                page.headers = {k.lower(): v for k, v in resp.headers.items()}
                page.content_type = page.headers.get("content-type", "")
                body = resp.read()
                if page.status_code == 200:
                    page.html = body.decode("utf-8", errors="replace")
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

def _parse_robots(robots_txt: str) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    agents: list[str] = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            agents = [value.lower()]
            rules.setdefault(value.lower(), [])
        elif key == "disallow" and agents:
            for agent in agents:
                rules.setdefault(agent, []).append(value)
    return rules


def _blocked_agents(rules: dict[str, list[str]], path: str) -> list[str]:
    blocked = []
    for agent, disallows in rules.items():
        if agent != "*" and agent not in AI_USER_AGENTS:
            continue
        for rule in disallows:
            if rule == "/" or (rule and path.startswith(rule)):
                blocked.append(agent)
                break
    return blocked


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def crawl(url: str, max_pages: int = MAX_PAGES, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> SiteSnapshot:
    started = time.monotonic()
    session = _new_session()
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path_parts = [p for p in parsed.path.strip("/").split("/") if p and "." not in p]
    scope_prefix = "/" + path_parts[0] + "/" if path_parts else "/"

    snapshot = SiteSnapshot(base_url=base, entry_url=url)

    robots = _fetch(urljoin(base + "/", "robots.txt"), session)
    snapshot.robots_status = robots.status_code
    if robots.status_code == 200:
        snapshot.robots_txt = robots.html or ""

    sitemap = _fetch(urljoin(base + "/", "sitemap.xml"), session)
    if sitemap.status_code == 200 and sitemap.html:
        snapshot.sitemap_urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap.html)

    robots_rules = _parse_robots(snapshot.robots_txt) if snapshot.robots_txt else {}

    queue = [url]
    seen = {url}
    while queue and len(snapshot.pages) < max_pages:
        if time.monotonic() - started > budget_seconds:
            snapshot.notes["stopped_early"] = "wall-clock budget reached"
            break
        current = queue.pop(0)
        if robots_rules and _blocked_agents(robots_rules, urlparse(current).path):
            snapshot.notes.setdefault("skipped_by_robots", []).append(current)
            continue

        page = _fetch(current, session)
        if page.error and page.status_code is None and current == url:
            time.sleep(0.2)  # one retry: a transient transport error is not a defect
            page = _fetch(current, session)
        page.is_entry = current == url
        snapshot.pages.append(page)

        if page.error:
            snapshot.fetch_errors.append({"url": current, "error": page.error})
        elif page.status_code and page.status_code >= 400:
            snapshot.broken_links.append({"url": current, "status": page.status_code})

        if not page.ok:
            continue

        for link in page.links:
            href = link["href"]
            if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            absolute = urljoin(current, href).split("#")[0]
            target = urlparse(absolute)
            if target.netloc != parsed.netloc or absolute in seen:
                continue
            if scope_prefix != "/" and not target.path.startswith(scope_prefix.rstrip("/")):
                continue
            if re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|css|js|xml|ico)$", target.path, re.I):
                continue
            seen.add(absolute)
            queue.append(absolute)

    snapshot.notes["pages_fetched"] = len(snapshot.pages)
    snapshot.notes["pages_ok"] = len(snapshot.ok_pages)
    snapshot.notes["scope_prefix"] = scope_prefix
    return snapshot


# ---------------------------------------------------------------------------
# Check 1: crawlability  (fires only on hard, quotable access barriers)
# ---------------------------------------------------------------------------

def check_crawlability(snapshot: SiteSnapshot) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    recs: list[dict] = []
    entry = snapshot.entry_page

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

    # robots.txt blocking the audited path for * or a known AI agent
    if snapshot.robots_txt:
        rules = _parse_robots(snapshot.robots_txt)
        blocked = _blocked_agents(rules, urlparse(snapshot.entry_url).path)
        if blocked:
            findings.append(make_finding(
                category="crawlability",
                title="robots.txt blocks AI crawlers from the audited path",
                severity="high",
                evidence=(
                    f"{snapshot.base_url}/robots.txt ({snapshot.robots_status}) disallows "
                    f"{urlparse(snapshot.entry_url).path} for {len(blocked)} user-agent blocks "
                    f"({', '.join(sorted(blocked))}); {len(snapshot.ok_pages)} pages of the site are affected. "
                    f"Verified against {snapshot.entry_url} ({entry.http_label})."
                ),
                action=(
                    "Narrow the Disallow rules in robots.txt so public marketing and documentation paths stay "
                    "crawlable for AI user agents (GPTBot, ClaudeBot, PerplexityBot, Google-Extended), keeping "
                    "blanket blocks scoped to private or transactional paths, so the HTML stays machine-readable "
                    "and assistants can discover and cite it."
                ),
                mechanism="AI assistants honour robots.txt at fetch time; a disallowed path is never retrieved, so the brand cannot be cited even when the content is excellent.",
                locations=[f"{snapshot.base_url}/robots.txt"],
                detected_by=SKILL_ID,
                proof={"blocked_agents": blocked},
            ))

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
    if not snapshot.sitemap_urls:
        recs.append(recommendation(
            "Publish an XML sitemap with lastmod dates",
            "No /sitemap.xml was served. A sitemap with accurate <lastmod> values gives crawlers a cheap "
            "freshness signal and a complete URL inventory, which improves how quickly new facts are picked up.",
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

    snapshot = crawl(context["url"], max_pages=context.get("max_pages", MAX_PAGES),
                     budget_seconds=context.get("budget_seconds", DEFAULT_BUDGET_SECONDS))
    result.artifacts["snapshot"] = snapshot

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
    ]
    result.runtime_seconds = time.monotonic() - started
    return result
