---
name: offsite-discoverability-audit
description: Audit the signals that let an AI assistant identify a brand against the rest of the web, rather than merely read its pages — external identity anchoring (sameAs, Wikidata, LinkedIn), corroboration surface (press, newsroom, awards), direct-answer content shape, answer-engine schema coverage, fact quotability, and contact details as readable text. Emits prioritized recommendations only, never defects, because a read-only crawl of one origin cannot observe the wider web. Use when diagnosing why a brand is absent from or confused in AI answers even though its own site reads cleanly.
version: 1.0.0
license: MIT
kind: audit
provides: [offsite_discoverability]
consumes: [snapshot]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot, performs no network calls of its own
---

# Off-Site Discoverability Audit

## When to use

Use alongside the on-site skills when the question is *why is this brand not
cited*, rather than *why can this page not be read*. Every other skill in this
marketplace reasons about one document. This one reasons about how that document
anchors itself to the wider web — handout appendices B (how assistants pick
sources), D (why agreement across the web matters) and E (prior context).

## Inputs

The shared `SiteSnapshot` produced by `crawl-render-audit`, read from
`context["artifacts"]["snapshot"]`. This skill performs **no fetching of its
own** — it makes no request to any third-party service, so the marketplace stays
self-contained and offline-reproducible.

## Why this skill emits no findings

A defect in this marketplace must cite a reproducible measurement
(`lib/contracts.make_finding` rejects anything else). From one origin we can
observe that a site *publishes* no anchor; we cannot observe whether the wider
web corroborates it. Reporting "this brand is uncorroborated" would assert
something never measured, and would fire on healthy sites — `site-010` carries
valid Organization JSON-LD with no `sameAs` and is not defective.

So every output here is a **recommendation**: an opportunity that would
strengthen discoverability, which is what the handout asks for under
"suggestions may go beyond the detected problems". The cost of a wrong
recommendation is a wasted afternoon; the cost of a wrong defect is a report a
reader stops trusting.

## Procedure

1. Collect `Organization`/`Corporation`/`LocalBusiness`/`Brand` JSON-LD objects across all crawled pages.
2. Extract declared `sameAs` targets; separately collect outbound links to known identity providers.
3. **Identity anchoring** — if neither exists, recommend publishing verifiable anchors. If profile links exist in markup but not in `sameAs`, recommend mirroring them into the structured data.
4. **Corroboration surface** — scan readable text for a press / newsroom / awards / coverage surface; recommend dated announcements when absent.
5. **Direct-answer shape** — detect Q&A prose or `FAQPage` markup; recommend question-shaped content when absent.
6. **Answer-engine schema coverage** — compare published `@type`s against `FAQPage`, `HowTo`, `QAPage`, `Speakable`, `BreadcrumbList`, `Article`; recommend extension when five or more are absent *and* an Organization block already exists.
7. **Quotability** — count pages carrying a figure, price, date or quantity; recommend concrete facts when fewer than half qualify.
8. **Contact reachability** — detect an email or phone number in readable text; recommend publishing them as text when neither is present.

## False-positive controls

- Never emits a finding, so it can never raise a false defect.
- Schema extension is recommended only when an Organization block already exists — a site with no structured data at all is `content-semantics-audit`'s concern, and duplicating it would double-report one root cause.
- Quotability uses a half-of-pages threshold, not any single page, so an About page with no numbers is not itself an issue.
- Identity anchoring accepts an outbound profile link as evidence of anchoring even when `sameAs` is absent, and then recommends only the cheaper structured-data fix.

## Output

A `SkillResult` carrying `recommendations` (each with `title`, `detail`,
`category`, `effort`), a `checks` list recording every signal measured with its
observed value, and an `offsite_profile` artifact. The orchestrator merges these
into the report's `recommendations` array.
