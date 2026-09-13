---
name: crawl-render-audit
description: Fetches a site read-only, publishes the shared site snapshot, and detects machine-access barriers - unreachable pages, robots.txt or noindex blocks, and business content that only exists after client-side JavaScript execution.
version: 1.0.0
license: MIT
kind: audit
provides: [crawlability, rendering]
allowed-tools: [http-get]
safety: read-only; GET only; obeys robots.txt and Crawl-delay; max 150 pages per run; <= 6 concurrent requests
---

# Crawl & Render Audit

## Responsibility

Two questions, and nothing else:

1. **Can a machine fetch the pages at all?** -> `crawlability`
2. **Is the substance there before JavaScript runs?** -> `rendering`

It also produces the `snapshot` artifact (pages, headers, robots, sitemap, broken
links) that every other skill in the marketplace consumes, so one crawl serves the
whole audit.

## Simulated render

Instead of shipping a headless browser, this skill mines inline scripts for the
markup they inject (`innerHTML = ...`, `insertAdjacentHTML(...)`, `document.write(...)`)
and for the elements they target (`getElementById`, `querySelector('#id')`). Parsing
that payload reconstructs what a JS-enabled client would see, and diffing it against
the raw HTML yields the exact facts a non-rendering crawler loses - which is what the
finding quotes. Deterministic, dependency-free, and reproducible.

## Detection rules

`rendering` (severity **high**) fires when at least one holds:

- an element that is **empty in the raw HTML** is filled at runtime by inline JS
  targeting that element's id (the payload is quoted, with its character count);
- a framework root (`#root`, `#app`, `#__next`, `#__nuxt`, ...) exists and has no
  server-rendered children;
- a `<noscript>` gate says "enable JavaScript" **and** the page carries fewer than
  60 words of extractable text.

`crawlability` fires only on hard, quotable barriers:

- entry URL does not return HTTP 200 with an HTML body -> **critical**;
- `robots.txt` disallows the audited path for a known AI crawler -> **high**. Both
  halves of each vendor's fleet are checked, because they fail differently: the
  training crawlers (GPTBot, ClaudeBot, Google-Extended, CCBot) decide whether a
  brand is ever learned, while the query-time crawlers (OAI-SearchBot, ChatGPT-User,
  Claude-SearchBot, PerplexityBot) decide whether it can be cited in an answer being
  written right now. Blocking only the second set removes a brand from AI answers
  while leaving ordinary search traffic untouched, which is why it is usually
  unintentional;
- `noindex` in a robots meta tag or `X-Robots-Tag` header on a content page -> **high**.

## Precision guards

- A JS-populated element that **already** contains server-rendered text is not a gap.
- A missing `robots.txt` or `sitemap.xml` is a *recommendation*, never a finding, and
  is only claimed once the server has actually answered - `snapshot.sitemap_checked`
  separates "no sitemap" from "we never found out".
- robots.txt group selection is most-specific-wins and `Allow` is honoured, so a site
  that carves AI crawlers out of a blanket block is not reported as blocking them.
- What this skill *obeys* is separate from what it *reports*: it honours only the rules
  written for its own user agent, so a site that blocks GPTBot is still crawled and
  reported as a robots defect rather than as an unreachable site.
- Sub-page 404s are recorded in the snapshot (`broken_links`) and left for the
  report's telemetry; they are not reported as a crawlability defect, because a
  broken link is a link-hygiene issue, not a barrier to the audited document.
