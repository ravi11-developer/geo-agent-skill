---
name: web-intelligence-crawler
description: Crawl public websites for legitimate research, competitor analysis, website intelligence, content discovery, and structured reporting. Trigger when the user provides a public URL and wants robots.txt-aware crawling, page extraction, deduplication, or a sourced intelligence report.
argument-hint: "url=<https://example.com> [max_pages=50] [max_depth=2]"
user-invocable: true
disable-model-invocation: false
---

# Web Intelligence Crawler

Use this skill to analyze a public website responsibly and turn crawled pages into a grounded intelligence report.

## What this skill is for

Use it for public website research, competitor analysis, product discovery, pricing checks, documentation review, SEO/content analysis, and general website intelligence.

It must only be used on public websites. It must never bypass robots.txt, login walls, paywalls, CAPTCHAs, or other access controls.

## Workflow

1. Install dependencies from `scripts/requirements.txt`.
2. Run `python scripts/crawler.py <url> [options]`.
3. Read the JSON output from the script.
4. Read `assets/report-template.md`.
5. Analyze the extracted content and generate the final report.

## Operating rules

- Validate that the input is an HTTP or HTTPS URL.
- Reject localhost, loopback, private IPs, link-local addresses, reserved ranges, and internal hostnames.
- Remove fragments and normalize duplicate URLs.
- Check `robots.txt` before fetching any page.
- Never crawl pages that robots.txt disallows for the declared user agent.
- Respect the crawl limits, with a default maximum of 50 pages and depth 2.
- Use only publicly reachable content.
- Prefer same-domain internal pages.
- Ignore obvious login, logout, account, cart, checkout, and tracking URLs.
- Crawl politely with a 1-2 second delay between requests.
- Retry transient network errors carefully and stop when the site is unavailable or inaccessible.

## Extraction and analysis expectations

The crawler output contains page-level facts such as titles, descriptions, URLs, and clean text. Use that output to produce a report that is specific, traceable, and conservative.

- Separate facts from inferences.
- Prefer authoritative pages for company, product, pricing, and contact claims.
- Do not invent missing details.
- If something cannot be established from the crawled pages, say so explicitly.
- If pricing is not found, write "Not found".

## Final report requirements

Generate a Markdown report using the structure in `assets/report-template.md` and keep the 12 required sections in order.
