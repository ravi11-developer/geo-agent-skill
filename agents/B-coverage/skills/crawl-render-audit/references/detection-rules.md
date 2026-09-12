# Detection rules: crawl & render

## Fetching

- GET only, `allow_redirects=True`, 8s timeout, <= 12 pages per run.
- One retry for a transport error with no HTTP status (DNS/TLS/proxy blips); a finding
  raised from a retry-failed fetch is emitted with `confidence: "medium"`.
- Charset: the header parameter wins; otherwise `<meta charset>` from the first 2KB;
  otherwise chardet, then UTF-8. **Never** the requests default of ISO-8859-1 for
  `text/html` without a charset - that mojibakes UTF-8 pages that declare charset in
  markup, which corrupts em dashes, quotes and accented brand names.
- BFS stays on the entry host, inside the entry path prefix, and skips
  `.pdf|.zip|images|.css|.js|.xml|.ico`.

## crawlability

Fires only on hard, quotable barriers:

| trigger | severity | evidence carries |
|---|---|---|
| entry URL not 200 + HTML | critical | status, error text, pages retrieved |
| `robots.txt` `Disallow: /` (or covering the path) for `*`, GPTBot, ClaudeBot, PerplexityBot, Google-Extended, CCBot | high | robots URL, matching agent blocks, pages affected |
| `noindex` in `meta[name=robots|googlebot]` or `X-Robots-Tag` | high | page URL, directive text, page size |

Excluded from the noindex rule (correct practice, not a defect):
`/account|login|signin|register|cart|checkout|basket|search|admin|wp-admin|profile|logout|password|reset`.

Sub-page 404s are recorded in `snapshot.broken_links` and surface in telemetry; a
broken link is link hygiene, not a barrier to the audited document.

## rendering

A gap requires **one** of:

1. **Load-bearing injection** - an element that is empty in the raw HTML is filled by
   inline JS targeting its id, *and* either the page carries < 60 words of extractable
   text, or the injected markup contains price-like values (`$1,299`, `€49/mo`,
   `per month`) that do not appear in the served text.
2. **Empty framework root** - `#root`, `#app`, `#__next`, `#__nuxt`, `#ember-app`,
   `#svelte-app`, `#q-app` present with no server-rendered children.
3. **JS gate** - a `<noscript>` telling the user to enable JavaScript on a page with
   < 60 words of extractable text.

Comparing price *values* rather than the presence of fact-shaped words is what
separates a pricing widget from a "Loading offers..." modal.
