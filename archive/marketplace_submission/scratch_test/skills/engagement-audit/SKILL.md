---
name: engagement-audit
description: Measures on-site orientation and continuation - navigation landmarks, the internal link graph, breadcrumbs and next-step affordances - so a visitor arriving from a search result or an AI citation is never stranded.
version: 1.0.0
license: MIT
kind: audit
provides: [engagement]
consumes: [snapshot]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot, performs no network calls
---

# Engagement & Orientation Audit

## Responsibility

One question: **can a visitor - or an agent that followed a citation into a deep page -
tell where they are and continue?** Category emitted: `engagement`.

## Measured per page

`<nav>` and `role="navigation"` landmarks - unique internal links - external links -
`<header>` / `<footer>` presence - breadcrumbs (`aria-label`, class, or BreadcrumbList
schema) - a contact/demo/pricing call to action - generic anchor text.

## Detection rule

`engagement` (severity **high**) fires only when **every** crawled page has **zero**
navigation landmarks **and fewer than two** internal links, and the entry page is among
them: the site is a set of dead ends. The evidence quotes the landmark count, the link
counts, and how many of the crawled pages share the pattern.

The orchestrator may down-rank this finding to `medium` on a site that also has
blocking machine-access defects; the measurement is unchanged, only the fix order.

## Reported as advice, never as a defect

- generic anchor text ("click here", "read more");
- missing breadcrumbs on a site that does have navigation;
- a missing `<footer>` landmark;
- no explicit call to action.

These reduce quality but do not strand anyone, so they belong in `recommendations`.
Likewise, keyword-stuffed titles, underscore URLs and multiple H1s are SEO habits, not
AI-discoverability defects, and this skill deliberately ignores them.
