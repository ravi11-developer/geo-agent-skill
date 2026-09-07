---
name: offsite-discoverability-audit
description: Audit a brand's off-site discoverability signals — why AI assistants may fail to find, cite, or correctly identify the brand. Covers entity anchoring (sameAs), brand disambiguation, content quotability, Organisation schema quality, press/media mentions, contact information accessibility, AI-optimised schema types, and direct-answer content patterns. Addresses Appendices B, D, and E of the evaluation criteria.
license: Apache-2.0
tools: [fetch]
---

# Off-Site Discoverability Audit

## When to use
Use when auditing why a brand is not found, cited, or correctly identified by AI assistants. Addresses the "off-site discoverability" half of the problem: external signals, entity anchoring, and content structure for AI citation.

## Inputs
- JSON array of page URLs (piped via stdin from the orchestrator), or a single URL as a CLI argument.
- The first URL must be the homepage (the orchestrator always places it first).

## Procedure
1. Fetch the homepage; extract JSON-LD blocks and visible text.
2. Check for `sameAs` links in Organisation JSON-LD — flag missing or incomplete external profile anchors (Wikipedia, Wikidata, LinkedIn, Crunchbase).
3. Evaluate brand name ambiguity risk — flag generic or collision-prone names without disambiguating anchors.
4. Sample up to 5 pages and assess content quotability — flag pages with no concrete, machine-extractable facts.
5. Check Organisation/Brand JSON-LD completeness — flag missing name, url, description, logo, sameAs fields.
6. Check for press, media, or external mention links — flag absence of corroboration signals.
7. Check for visible contact information — flag absence of phone/email in readable text.
8. Identify missing high-ROI schema types (FAQPage, HowTo, Speakable, QAPage) across sampled pages.
9. Detect direct-answer content patterns — flag sites with no Q&A-style content.

## False-positive controls
- Do not flag sameAs as missing if the Organisation has a genuinely unique name with no collision risk.
- Do not flag lack of press links as high-severity for newly-launched brands.
- Do not require Wikipedia presence — flag its absence only as a recommendation.

## Output
JSON array of findings, each containing `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`.
