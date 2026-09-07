# Brand AI-Readiness Audit

A read-only Agent Skill Marketplace for auditing websites for AI discoverability (on-site and off-site) and on-site engagement.

## How It Works

The orchestrator fetches the target homepage, discovers internal links, classifies them by page type (category, product, info, other), and samples a bounded set of representative pages (default: 15). It then pipes the page list to five specialist audit scripts, collects their findings, de-duplicates, assigns sequential IDs, sorts by severity, and emits a single JSON report.

```
orchestrator.py
  ├── crawl_audit.py         (robots.txt + AI-crawler blocking, HTTPS, sitemaps, redirects,
  │                           canonical, noindex/nofollow, JS-rendering, page size, mixed content)
  ├── semantic_audit.py      (title, meta, JSON-LD validation, OG/Twitter Cards, entity identity,
  │                           sameAs, FAQ/speakable schema, quotability, E-E-A-T, duplicates)
  ├── freshness_audit.py     (copyright year, Last-Modified, date meta, phone/email/address
  │                           consistency, authority outlinks, email readability — Appendix F)
  ├── engagement_audit.py    (headings + hierarchy + multiple H1, navigation, breadcrumbs,
  │                           broken links, CTAs, viewport, word count, lang, hreflang,
  │                           FAQ content patterns, form accessibility)
  └── offsite_audit.py       (sameAs anchoring, brand disambiguation, content quotability,
                              Organisation schema quality, press/media mentions, contact info,
                              AI-optimised schema types, direct-answer content — Appendix B, D, E)
```

## Skills

- `audit-orchestrator`: **Entrypoint.** Discovers pages, composes all specialist checks, and emits one fixed-schema report.
- `crawl-render-audit`: Reachability, robots/sitemap, HTTPS, AI-crawler blocking, redirect chains, canonical correctness, noindex/nofollow, raw HTML content, page size, mixed content, and image alt text.
- `semantic-entity-audit`: Explicit facts, page semantics, structured data (JSON-LD) syntax and value validation, OpenGraph, Twitter Cards, entity identity/ambiguity, sameAs disambiguation, FAQ/speakable schema, quotability, E-E-A-T signals, duplicate content.
- `freshness-corroboration`: Freshness signals (copyright, Last-Modified, meta dates), internal date consistency, cross-page fact consistency (phone, email, address), outbound authority links, email readability (Appendix F).
- `engagement-audit`: First-visit orientation, heading hierarchy (H1 uniqueness, level skipping), navigation health, breadcrumbs, broken links (internal + external), CTAs, viewport, word count, lang attribute, hreflang, FAQ content patterns, form accessibility.
- `offsite-discoverability-audit`: Off-site discoverability signals — sameAs entity anchoring, brand name ambiguity risk, content quotability, Organisation JSON-LD completeness, press/media mentions, contact information accessibility, AI-optimised schema types, direct-answer content patterns. *Covers Appendices B, D, and E.*

## Shared Library

All scripts import from `skills/lib/shared.py` which provides:
- `fetch()` — HTTP fetcher with retry and redirect tracking
- `FullParser` / `parse_page()` — comprehensive single-pass HTML parser
- `make_finding()` — standards-compliant finding builder
- Common regex patterns (`PHONE_RE`, `EMAIL_RE`, `COPYRIGHT_RE`, `ADDRESS_RE`, `BRAND_TOKEN_RE`, `QUESTION_RE`)

## Usage

```bash
python3 skills/audit-orchestrator/scripts/orchestrator.py https://example.com
```

## Output

The output is a single JSON report with:
- `site`, `audited_at`, `pages_sampled`, `sample_breakdown`
- `summary` (total, critical, high, medium, low counts)
- `findings[]` — each with `id` (F-001…), `title`, `severity`, `confidence`, `type` (defect/recommendation), `evidence`, `affected_urls`, and `suggested_action`

## What Each Skill Checks (Summary)

| Skill | Key Checks |
|---|---|
| crawl-render-audit | robots.txt, AI-crawler blocks (GPTBot etc.), HTTPS, sitemap, noindex/nofollow, redirect chains, canonical, JS rendering, thin text, image alt, hidden content, iframes, page size, mixed content, sitemap coverage, orphan pages |
| semantic-entity-audit | Title, meta description, JSON-LD syntax + value accuracy, OpenGraph, Twitter Cards, page intent, authorship (E-E-A-T), JSON-LD entity consistency, brand name consistency, body text terminology, sameAs links, FAQ schema, speakable schema, quotability, brand ambiguity risk, duplicate titles, duplicate descriptions |
| freshness-corroboration | Copyright year, Last-Modified header, publication/modification date meta, phone consistency, email consistency, address consistency, outbound authority links, email newsletter readability (Appendix F) |
| engagement-audit | H1 presence + uniqueness, heading hierarchy (no level skipping), sparse text, word count, dead-end pages, internal link count, breadcrumbs, CTAs, broken internal links, broken external links, viewport meta, lang attribute, hreflang, FAQ content without schema, form input labels |
| offsite-discoverability-audit | sameAs presence + profile coverage, brand disambiguation risk, content quotability (facts per page), Organisation JSON-LD completeness, press/media mentions, homepage contact info, FAQPage/HowTo/Speakable/QAPage schema presence, direct-answer content patterns |

## Safety

Recommend-only. No skill modifies a live website. Audits are read-only, respect `robots.txt`, avoid authenticated areas, destructive actions, and rate abuse.

## Runtime Design

The skills are intentionally provider-neutral. Each specialist accepts a JSON page list via stdin and emits findings as a JSON array to stdout. The orchestrator composes them, handles sampling, de-duplication, sequential ID assignment, and severity-sorted output.

All scripts use Python standard library only (zero external dependencies). Audit runtime is bounded to < 5 minutes for a typical website via page sampling (default budget: 15 pages).
