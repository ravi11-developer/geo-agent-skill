---
name: crawl-render-audit
description: Fetches a site read-only, publishes the shared site snapshot, and detects machine-access barriers - unreachable pages, robots.txt or noindex blocks, and business content that only exists after client-side JavaScript runs. Use as the first skill in an AI-discoverability audit; it produces the snapshot every other skill reads.
version: 1.1.0
license: MIT
kind: audit
provides: [crawlability, rendering]
consumes: []
produces: [snapshot]
allowed-tools: [http-get]
safety: read-only; GET only; obeys robots.txt; <= 12 pages and 8s per request
---

# Crawl & Render Audit

## When to use

First skill of any audit. Answers two questions and nothing else:

1. Can a machine fetch the pages at all? -> `crawlability`
2. Is the substance there before JavaScript runs? -> `rendering`

It also produces the `snapshot` (pages, headers, robots, sitemap, broken links)
that every other skill consumes, so one crawl serves the whole audit.

## Run it

```bash
python scripts/crawl_render.py https://example.com        # standalone
```
As a skill: `run(context) -> SkillResult`, with `context["url"]`.

## What it emits

| category | severity | fires when |
|---|---|---|
| `crawlability` | critical | entry URL does not return HTTP 200 with an HTML body |
| `crawlability` | high | robots.txt disallows the audited path for `*` or a named AI agent |
| `crawlability` | high | `noindex` on a content page (transactional paths excluded) |
| `rendering` | high | the raw HTML is a shell, or prices exist only inside injected markup |

## Simulated render (no headless browser)

Inline scripts are mined for the markup they inject (`innerHTML`,
`insertAdjacentHTML`, `document.write`) and the elements they target
(`getElementById`, `querySelector('#id')`). Parsing that payload reconstructs what a
JS-enabled client would see; diffing it against the raw HTML yields the exact facts a
non-rendering crawler loses. Deterministic, dependency-free, quotable in evidence.

## Precision guards

- A JS-filled element on a content-rich page is **not** a defect: real sites inject
  cart drawers, cookie banners and captcha errors on every load. The bar is a page
  thin enough to be a shell, or prices in the payload that the served text never states.
- Missing `robots.txt` or `sitemap.xml` is a recommendation, not a finding.
- `noindex` on cart, login, account or search URLs is correct practice and ignored.

## See also

- `references/detection-rules.md` - thresholds, regexes and the reasoning behind each gate
- `references/false-positive-log.md` - real sites that broke earlier versions and the fix
