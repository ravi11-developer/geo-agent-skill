---
name: semantic-entity-audit
description: Audit whether a website states important facts explicitly and unambiguously, uses appropriate structured data (JSON-LD, OpenGraph, Twitter Cards), validates structured data values against visible content, establishes a consistent identity for its organisation across multiple pages, includes sameAs disambiguation links, FAQ and speakable schema, E-E-A-T credibility signals, and avoids duplicate titles and descriptions.
license: Apache-2.0
tools: [fetch]
---

# Semantic and Entity Audit

## When to use
Use when auditing whether a website states important facts explicitly, uses appropriate structured data, validates data accuracy, and establishes a consistent identity across pages.

## Inputs
- JSON array of page URLs (piped via stdin from the orchestrator), or a single URL as a CLI argument.

## Procedure
1. For each sampled page:
   a. Check for a descriptive `<title>` tag.
   b. Check for `<meta name="description">`.
   c. Check for JSON-LD (`application/ld+json`) and OpenGraph tags.
   d. Check for Twitter Card meta tags.
   e. If JSON-LD is present, validate syntax (parseable JSON) and compare `name`/`description` values against visible page text to detect stale or mismatched structured data.
   f. Evaluate page-intent clarity: can a machine distinguish whether this is a product, article, contact page, or category listing from the available signals?
   g. Check authorship metadata on article-type pages (`<meta name="author">`, `article:author`, JSON-LD `author` field).
   h. Assess content quotability: flag pages with no concrete machine-extractable facts (numbers, prices, dates, measurements).
   i. Check for E-E-A-T signals: authorship, sameAs links, about/team page links, outbound authority references.
2. After auditing all pages, check cross-page consistency:
   - Compare `Organization.name` (or equivalent) in JSON-LD across pages — flag differences.
   - Compare the brand portion of `<title>` tags across pages — flag inconsistencies.
   - Compare body-text brand name variants — flag inconsistent entity naming.
   - Flag duplicate page titles and duplicate meta descriptions.
3. Site-level checks (once, based on all sampled data):
   - Flag absence of `sameAs` links for entity disambiguation.
   - Flag FAQ/help pages missing FAQPage schema.
   - Flag absence of speakable schema.
   - Assess brand name ambiguity risk.
4. Report only issues that plausibly affect extraction, interpretation, or entity resolution.

## False-positive controls
- No structured data is not automatically a defect.
- Marketing language is not a factual claim unless it is presented as one.
- Different wording is not a contradiction when the underlying fact is consistent.
- Twitter Card absence is low-severity if OpenGraph is present (browsers fall back).

## Output
JSON array of findings, each containing `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`.
