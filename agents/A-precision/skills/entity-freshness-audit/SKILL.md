---
name: entity-freshness-audit
description: Cross-references the organisation name across title, H1, JSON-LD, OpenGraph, footer copyright and body prose to detect entity ambiguity, and dates every factual claim to detect stale content.
version: 1.0.0
license: MIT
kind: audit
provides: [entity_identity, freshness]
consumes: [snapshot]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot, performs no network calls
---

# Entity & Freshness Audit

## Responsibility

1. **Is this one identifiable company?** -> `entity_identity`
2. **Are the facts still current?** -> `freshness`

## Entity identity

The skill reads the *identity slots* a machine actually uses, on every crawled page:

`<title>` prefix - first `<h1>` (with a leading "Welcome to" stripped) - JSON-LD
`name` / `legalName` / `alternateName` **on Organization-family types only** -
`og:title` / `og:site_name` - footer copyright line - and four explicit prose
patterns: *"Welcome to X"*, an *"About X"* heading, *"(formerly X)"*, *"a division of X"*.

Each name is normalised (lowercased, punctuation stripped, trailing legal suffixes such
as Inc/Ltd/Corp/GmbH removed) and the normalised set is compared pairwise with a string
similarity ratio.

**A finding (severity high) requires both:**

- **three or more** distinct normalised names occupy those slots, **and**
- at least one pair is a **near-variant**: similarity in `[0.50, 0.95)` - similar
  enough to be the same company, different enough to be a different string.

That second condition is the false-positive guard. Two spellings (e.g. a short title
and the full legal name) are a *recommendation*, not a defect. Product names are never
treated as entity names, which is why a page carrying both Organization and
SoftwareApplication JSON-LD stays clean.

## Freshness

Every four-digit year in the body text is collected with a +/-40 character context
window. Years in *founding* contexts ("founded in 2015", "since 2015", "established")
are excluded - a founding date is a fact, not staleness. The rest are classified by
what they date: pricing, award, statistic, update stamp, press, team.

**Fires when** either the footer copyright year is two or more years old, **or** three
or more stale claims exist with no current-era year anywhere on the site.

**Severity:** `high` at eight or more stale claims (the staleness is systemic),
otherwise `medium` (isolated stale blocks).

## Precision guards

- A single old year (an award, a funding round) is never a freshness defect.
- A site with no dates at all gets a recommendation to date-stamp, not a defect.
- Entity variants are only reported with the specific slot each name came from, so the
  finding is directly verifiable by opening the page source.
