---
name: engagement-audit
description: Audit the first-visit experience across multiple pages for clear orientation, context retention, navigation, breadcrumbs, broken links, useful next steps, viewport readiness, heading hierarchy (H1 uniqueness, level skipping), lang attribute, hreflang tags, FAQ/Q&A content patterns, form accessibility, and friction that can prevent visitors from continuing after arriving from an AI answer.
license: Apache-2.0
tools: [fetch]
---

# On-Site Engagement Audit

## When to use
Use when auditing a website's first-visit experience for clear orientation, context retention, navigation health, and friction that can prevent visitors from engaging.

## Inputs
- JSON array of page URLs (piped via stdin from the orchestrator), or a single URL as a CLI argument.

## Procedure
1. For each sampled page, treat the landing page as a continuation of the user's question. Determine whether a first-time visitor can quickly understand what the site/brand offers.
2. Check orientation cues: presence of `<h1>`, heading hierarchy (sequential levels, no skipping), unique H1 (flag multiple H1s), and informational paragraphs.
3. Check navigation health: count internal links, detect dead-end pages (0 internal links), and probe a sample of internal links for broken (4xx/5xx) responses.
4. Check breadcrumbs or context indicators on pages deeper than one level.
5. Check for explicit calls to action (`<button>` elements or CTA-classed links).
6. Check for `<meta name="viewport">` to ensure mobile readiness.
7. Check for `lang` attribute on the `<html>` element.
8. On the homepage, check for `hreflang` link elements for international/multilingual sites.
9. Detect Q&A-style content patterns and flag pages with questions but no FAQPage JSON-LD schema.
10. Check form inputs for associated labels to ensure accessibility.
11. Assess word count: flag critically thin (<100 words) and sparse (<300 words) pages.
12. Probe a sample of external links for broken (4xx/5xx) responses.
13. Prioritize issues that plausibly cause a visitor arriving from an AI answer to leave or fail to complete the next logical task.
14. Suggest proactive improvements when they improve orientation or context retention, even if no hard defect exists (labelled as recommendations).

## False-positive controls
- Minimal design is not automatically poor engagement.
- Do not infer abandonment or conversion rates without analytics evidence.
- Do not recommend intrusive popups or manipulative patterns merely to increase engagement.
- Heading hierarchy violations are informational for single-level sites.

## Output
JSON array of findings, each containing `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`.
