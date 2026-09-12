# Detection rules: crawl & render

## Fetching

- GET only, `allow_redirects=True`, 8s timeout, <= 12 pages per run.
- One retry for a transport error with no HTTP status (DNS/TLS/proxy blips); a finding
  raised from a retry-failed fetch is emitted with `confidence: "medium"`.
- Charset: the header parameter wins; otherwise `<meta charset>` from the first 2KB;
  otherwise chardet, then UTF-8. **Never** the requests default of ISO-8859-1 for
  `text/html` without a charset - that mojibakes UTF-8 pages that declare charset in
  markup, which corrupts em dashes, quotes and accented brand names.
- HTML bodies land in `page.html`; **non-HTML text bodies land in `page.text_body`**
  and are kept only when the caller passes `accept_text=True`. The split is what keeps
  `page.ok` and `page.soup` HTML-only, so a `text/plain` or XML fetch can never reach a
  downstream skill as if it were a crawlable page.
- robots.txt is `text/plain` (mandated by RFC 9309) and sitemaps are `application/xml`.
  Gating the stored body on an HTML content type therefore discards both - they answer
  200 and arrive empty. That is a **silent** failure: every robots-derived check simply
  stops firing, and "no sitemap was served" becomes indistinguishable from "we threw the
  sitemap away". Both are fetched with `accept_text=True` for this reason.

## robots.txt parsing

- Groups follow RFC 9309 s2.2.1: consecutive `User-agent` lines **share** one rule
  block. Resetting the agent list on each line drops every agent but the last, so
  `User-agent: GPTBot` / `User-agent: ClaudeBot` / `Disallow: /` reads as blocking only
  ClaudeBot. Regression fixture: `site-017-robots-blocks-ai`.
- `Allow` is honoured. Precedence is longest-match-wins, `Allow` breaking ties, with
  `*` and a trailing `$` supported. Ignoring `Allow` turns a site that deliberately
  carves AI crawlers *out* of a blanket block into a false positive.
- Group selection is **most specific wins, not a union**: a crawler named explicitly
  obeys only its own block and ignores `*`. A site that blocks everyone but allows
  GPTBot is therefore read correctly.
- A bare `Disallow:` with an empty value imposes no restriction.
- **What we obey is not what we report.** `_auditor_may_fetch` resolves rules for this
  auditor's own token; `_blocked_agents` reports on the AI crawlers. Skipping URLs
  because GPTBot is disallowed would replace a correct `crawlability` finding with a
  bogus "entry URL is not retrievable" one, so the two are kept separate.

## Sitemaps

- Entry points, in order: every `Sitemap:` directive in robots.txt (authoritative about
  where the sitemap actually lives), then the conventional `/sitemap.xml`.
- `<sitemapindex>` is followed into its children, breadth-first, bounded by
  `SITEMAP_MAX_DOCUMENTS` (6) and `SITEMAP_MAX_URLS` (5000).
- Index and urlset are told apart by the **wrapper** (`<sitemap>` vs `<url>`), never by
  `<loc>` alone - `<loc>` appears in both, so reading every `<loc>` as a page URL makes
  a sitemap index look like a list of pages that happen to be XML.
- `.gz` sitemaps carry gzip as a *payload*, not a transfer encoding, so no HTTP client
  unwraps them; the gzip magic number is detected and decompressed here.
- `snapshot.sitemap_checked` records whether the server gave a definitive answer at all.
  "No sitemap" and "we never found out" are different claims and only the first may be
  reported. Regression fixture: `site-018-sitemap-index-healthy`.
- BFS stays on the entry host, inside the entry path prefix, and skips
  `.pdf|.zip|images|.css|.js|.xml|.ico`.

## crawlability

Fires only on hard, quotable barriers:

| trigger | severity | evidence carries |
|---|---|---|
| entry URL not 200 + HTML | critical | status, error text, pages retrieved |
| `robots.txt` disallows the entry path for any known AI crawler - training (GPTBot, ClaudeBot, Google-Extended, CCBot, anthropic-ai, Applebot-Extended, meta-externalagent) or query-time (OAI-SearchBot, ChatGPT-User, Claude-SearchBot, PerplexityBot, Perplexity-User) | high | robots URL and status, each blocked agent and whether it was named explicitly or caught by `*`, entry status, pages reachable |
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
