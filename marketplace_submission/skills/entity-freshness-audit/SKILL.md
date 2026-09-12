---
name: entity-freshness-audit
description: Cross-references the organisation name across title, H1, JSON-LD, OpenGraph, footer copyright and body prose to detect entity ambiguity, dates every factual claim to detect stale content, and checks whether any claim can be corroborated against an independent source. Use after crawl-render-audit; reads the shared snapshot and makes no network calls.
version: 1.1.0
license: MIT
kind: audit
provides: [entity_identity, freshness, corroboration]
consumes: [snapshot]
produces: [entity_profile, corroboration]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot; no network access
---

# Entity, Freshness & Corroboration Audit

## When to use

After the snapshot exists. Answers three questions about whether a machine can trust
what it reads:

1. Is this one identifiable organisation? -> `entity_identity`
2. Are the facts still current? -> `freshness`
3. Can any claim be checked against a third party? -> `corroboration`

## Run it

As a skill: `run(context) -> SkillResult`, with `context["artifacts"]["snapshot"]`.

## What it emits

| category | severity | fires when |
|---|---|---|
| `entity_identity` | high | >= 3 spellings of one organisation occupy identity slots, anchored on a canonical name |
| `freshness` | high / medium | >= 8 / fewer stale dated claims, on pages carrying no current-year date |
| `corroboration` | low | zero `sameAs`, zero identity-platform links and zero citation markup on a site that names itself and states facts |

## Identity slots read

`<title>` segment, first `<h1>`, JSON-LD `name`/`legalName`/`alternateName` on
Organization-family types only, `og:title`, `og:site_name`, footer copyright, and four
explicit prose patterns: "Welcome to X", an "About X" heading, "(formerly X)",
"a division of X".

## Precision guards

- Names are counted only when they are variants **of the canonical name**. On a real
  multi-page site, sub-page titles ("Online Rent Agreement", "Scaling Render Services")
  are page topics, not competing company names.
- Product/service sub-brands that extend the canonical with extra whole words
  ("Google Maps", "Google Workspace", "Samsung Galaxy") are not counted as name variants,
  so a mega-portal's product catalogue does not read as a naming conflict.
- Wiki namespace routes ("Help:Contents", "Wikipedia:About") are recognised as MediaWiki
  taxonomy addresses, not brand names, so the trailing site name is used instead.
- Product names are never entity names, so a page with both Organization and
  SoftwareApplication schema stays clean.
- A founding year ("founded in 2015") is a fact, not staleness.
- A stale copyright line on a site that publishes current-year content is a
  recommendation, not a defect.
- One external profile link anywhere is enough to satisfy corroboration.

## See also

- `references/detection-rules.md` - canonical anchoring, date classification, corroboration signals
- `references/entity-resolution.md` - why the medoid anchor, with worked examples
