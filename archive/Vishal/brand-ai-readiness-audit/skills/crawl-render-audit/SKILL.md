---
name: crawl-render-audit
description: Audit public website reachability and machine-readable access, including robots.txt (including AI-specific crawler blocks), HTTP behaviour, HTTPS enforcement, sitemap exposure, redirect chains, canonical tags, noindex/nofollow meta tags, raw HTML versus rendered content, hidden content, iframe accessibility, page size, mixed content, and orphan page risk.
license: Apache-2.0
tools: [fetch]
---

# Crawl and Render Audit

## When to use
Use when auditing a website's reachability, robots.txt restrictions (including AI-crawler blocks), sitemap exposure, redirect behaviour, canonical correctness, HTTPS status, noindex/nofollow directives, and detecting content locked behind client-side rendering or non-text formats.

## Inputs
- JSON array of page URLs (piped via stdin from the orchestrator), or a single URL as a CLI argument.

## Procedure
1. Fetch and inspect `robots.txt`; identify rules that block general crawlers AND rules that specifically block AI crawlers (GPTBot, ChatGPT-User, Google-Extended, CCBot, anthropic-ai). Do not treat the mere existence of robots.txt as a problem.
2. Check whether the site uses HTTPS; flag HTTP-only sites.
3. Discover sitemap references and inspect XML sitemaps. Compare representative important URLs against discovered crawlable URLs.
4. For each sampled page:
   a. Record HTTP status and redirect chain length. Flag chains longer than 2 hops.
   b. Check `<link rel="canonical">` — flag if it points to a different URL.
   c. Count internal links in raw HTML — flag if fewer than 3 on a large page (JS-rendering risk).
   d. Measure visible text vs. page size — flag thin text that suggests content locked in JS.
   e. Check images for missing alt text — flag if many images lack alt attributes.
   f. Check for `<meta name="robots" content="noindex">` — flag pages that exclude themselves from indexing.
   g. Check for `<meta name="robots" content="nofollow">` — flag pages that block link equity.
   h. Detect hidden content (display:none, hidden attribute, aria-hidden) — flag significant hidden text.
   i. Detect iframe-embedded content — flag iframes containing potentially important content.
   j. Check page size — flag pages over 2 MB.
   k. Detect mixed content (HTTP resources on HTTPS pages).
5. Check sitemap coverage — flag important pages missing from the XML sitemap.
6. Detect orphan pages — flag sampled pages with no inbound links from other sampled pages.

## False-positive controls
- A page may use JavaScript without being a problem if core factual content is present in readable HTML.
- Do not infer that every robots restriction is harmful; focus on public pages that matter.
- Do not call a missing sitemap a defect by itself when important pages are otherwise readily discoverable.

## Output
JSON array of findings, each containing `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`.
