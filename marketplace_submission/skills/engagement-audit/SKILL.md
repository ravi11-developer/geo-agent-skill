---
name: engagement-audit
description: Measures on-site orientation and continuation - navigation landmarks, the internal link graph, breadcrumbs and next-step affordances - so a visitor arriving from a search result or an AI citation is never stranded. Use after crawl-render-audit; reads the shared snapshot and makes no network calls.
version: 1.1.0
license: MIT
kind: audit
provides: [engagement]
consumes: [snapshot]
produces: [navigation_profile]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot; no network access
---

# Engagement & Orientation Audit

## When to use

After the snapshot exists. One question: can a visitor - or an agent that followed a
citation into a deep page - tell where they are and continue?

## Run it

As a skill: `run(context) -> SkillResult`, with `context["artifacts"]["snapshot"]`.

## Measured per page

`<nav>` and `role="navigation"` landmarks, unique internal links, external links,
`<header>`/`<footer>` presence, breadcrumbs (`aria-label`, class, or BreadcrumbList
schema), a contact/demo/pricing call to action, and generic anchor text.

## What it emits

| category | severity | fires when |
|---|---|---|
| `engagement` | high | **every** measurable page has zero nav landmarks and < 2 internal links, entry page included |

The orchestrator may down-rank this to `medium` on a site that also has blocking
machine-access defects; the measurement is unchanged, only the fix order.

## Abstention

Pages whose DOM is built entirely by JavaScript are skipped: a menu that exists after
hydration cannot be judged from the served HTML, and `crawl-render-audit` already
reports that root cause. If every page is a shell, this skill emits a recommendation to
re-audit after server-rendering instead of a second finding for one cause.

## Reported as advice, never as a defect

Generic anchor text ("click here"), missing breadcrumbs on a site that has navigation,
a missing `<footer>`, no call to action. These reduce quality but strand nobody.
Keyword-stuffed titles, underscore URLs and multiple H1s are SEO habits, not AI
discoverability defects, and are deliberately ignored.

## See also

- `references/detection-rules.md` - counting rules, the shell exemption, and the advice list
