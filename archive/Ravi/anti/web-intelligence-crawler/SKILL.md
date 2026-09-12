---
name: web-intelligence-crawler
description: >
  Crawls a public website responsibly (respecting robots.txt) and performs a
  comprehensive 50-check SEO audit. Produces a structured JSON report with
  severity-rated findings, evidence, and suggested actions. Trigger this skill
  when the user asks for: SEO audit, site audit, check SEO health, analyze
  website SEO, competitor SEO analysis, or technical SEO review.
---

# SEO Audit Crawler

## Purpose

Accept a public website URL, responsibly crawl its allowed pages, run 50 SEO
checks across 10 categories, and produce a structured JSON audit report with
findings rated by severity (critical / high / medium / low).

## Prerequisites

- Python 3.9+
- Network access to the target URL

## Agent Workflow

### 1. Install Dependencies

```bash
pip install -r scripts/requirements.txt
```

### 2. Run the SEO Audit

Execute the audit script, passing the target URL:

```bash
python scripts/seo_audit.py <URL> [--max-pages N] [--max-depth N] [--output PATH]
```

| Flag           | Default | Description                                |
| -------------- | ------- | ------------------------------------------ |
| `--max-pages`  | 50      | Maximum number of pages to crawl           |
| `--max-depth`  | 2       | Maximum link-follow depth from start URL   |
| `--output`     | stdout  | Write JSON to a file instead of stdout     |
| `--user-agent` | —       | Override the default User-Agent string     |
| `--delay`      | 1.5     | Seconds to wait between HTTP requests      |

### 3. Read the JSON Output

The script outputs a JSON report to stdout (or the file specified by `--output`).

**Top-level structure:**
```json
{
  "site": "example.com",
  "audited_at": "2026-09-07T21:00:00Z",
  "pages_crawled": 23,
  "crawl_depth": 2,
  "site_info": {
    "has_sitemap": true,
    "sitemap_url_count": 45,
    "has_robots_txt": true,
    "uses_https": true
  },
  "summary": {
    "total_findings": 14,
    "critical": 2,
    "high": 5,
    "medium": 4,
    "low": 3
  },
  "findings": [ ... ]
}
```

**Each finding:**
```json
{
  "id": "F-001",
  "category": "technical-seo",
  "title": "Missing sitemap.xml",
  "severity": "high",
  "affected_urls": ["https://example.com/sitemap.xml"],
  "evidence": "GET /sitemap.xml returned 404.",
  "suggested_action": {
    "summary": "Create and submit an XML sitemap listing all indexable pages.",
    "priority": "high"
  }
}
```

### 4. Present the Report

1. Read the report template from `assets/report-template.md`.
2. Fill in the template with the JSON data.
3. Sort findings by severity: critical → high → medium → low.
4. For each finding, present the evidence and recommended action clearly.
5. **Do not fabricate findings** — only report what the audit discovered.

## 10 Audit Categories (50 Checks)

| # | Category | Checks | Key Issues Detected |
|---|---|---|---|
| 1 | Technical SEO | 7 | Status codes, redirects, canonical, sitemap, URL structure |
| 2 | On-Page SEO | 8 | Title, meta description, headings, noindex |
| 3 | Structured Data | 4 | JSON-LD, Open Graph, Twitter Cards |
| 4 | Content Quality | 4 | Thin content, duplicates, placeholder text |
| 5 | Performance | 5 | Page size, blocking resources, DOM size, lazy loading |
| 6 | Mobile | 1 | Viewport meta tag |
| 7 | Security | 4 | HTTPS, mixed content, security headers |
| 8 | Accessibility | 4 | Alt text, lang, form labels, ARIA |
| 9 | Link Health | 5 | Broken links, orphan pages, nofollow, anchor text |
| 10 | International SEO | 6 | Hreflang, lang, x-default |

## Safety & Ethics

- The crawler validates URLs and **rejects** localhost, private IPs (RFC 1918),
  and link-local addresses to prevent SSRF.
- `robots.txt` is fetched and obeyed before any page is requested.
- A polite 1–2 second delay is enforced between requests.
- The User-Agent clearly identifies itself as a research bot.
