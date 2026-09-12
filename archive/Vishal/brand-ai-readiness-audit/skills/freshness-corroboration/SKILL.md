---
name: freshness-corroboration
description: Audit important website facts for freshness, internal consistency across pages, corroboration signals, outbound authority references, and email/newsletter readability (Appendix F — AI email summarization). Reports observable conflicts without claiming that an external source is inherently authoritative.
license: Apache-2.0
tools: [fetch]
---

# Freshness and Corroboration Audit

## When to use
Use when auditing important website facts for freshness, internal consistency, email readability, and corroboration signals across multiple pages.

## Inputs
- JSON array of page URLs (piped via stdin from the orchestrator), or a single URL as a CLI argument.

## Procedure
1. For each sampled page:
   a. Check copyright year — flag if more than 1 year behind the current year.
   b. Check the `Last-Modified` HTTP header — flag if the content is older than 1 year.
   c. Check for machine-readable date meta tags (`article:published_time`, `article:modified_time`).
   d. Extract repeatable facts (phone numbers, email addresses, physical addresses) for cross-page comparison.
   e. On content pages (blog, article, guide), check for outbound links to authoritative sources (.gov, .edu, Wikipedia, major news outlets) — flag absence as a credibility gap.
   f. Check for email newsletter signup forms — flag image-heavy/text-poor pages as risky for AI email summarization (Appendix F).
2. After auditing all pages, compare extracted facts across pages:
   - Flag inconsistent phone numbers, email addresses, or physical addresses.
   - Phrase evidence as "source A says X; source B says Y."
3. Distinguish an observable conflict from a proven falsehood.
4. Flag stale information only when there is evidence it is outdated; do not assume a page is stale solely because it lacks a date.

## False-positive controls
- A lack of corroboration is not proof of inaccuracy.
- One differing source is not proof that the site's claim is false.
- Do not manufacture recency requirements for facts that are inherently stable.
- Email readability findings apply only to pages with actual email signup forms.

## Output
JSON array of findings, each containing `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`.
