# Composition rules

## Merge (one finding per category)

Two skills may detect the same category (for example a JS shell is visible to both
the render and the extraction checks). The finding with the higher severity wins;
on a tie, the longer evidence string wins. The loser is attached to the winner as:

```json
"corroborated_by": [{"skill": "content-semantics-audit", "evidence": "..."}]
```

`locations` are unioned. Nothing measured is discarded, and nothing is reported twice.

## Prioritisation (relative severity)

Severity answers "what do I fix first", so it is adjusted in context:

- Blocking categories: `crawlability`, `rendering`, `content_extraction`,
  `entity_identity`. These describe a machine being unable to read or resolve the
  site at all.
- Secondary categories: `engagement`, `non_text_facts`, `freshness`
  (`lib/contracts.py::SECONDARY_CATEGORIES`).

When a site has **>= 4 findings including at least one blocking finding**, secondary
findings at `high` are capped at `medium`, and the finding records why:

```json
"severity_note": "Down-ranked to medium: this site has blocking machine-access defects
that must be fixed first; the underlying measurement is unchanged."
```

The measurement never changes - only the recommended order of work.

## Verification (an adversarial pass before anything is reported)

Detection and verification are separate passes on purpose. The skills are tuned to
*notice*; this stage is tuned to *disbelieve*, and it runs after every skill has
reported, spending the audit's remaining time budget re-checking what they claimed.
It may drop a finding or lower its confidence. It never invents one and never edits
what a finding means.

| rule | fires when | outcome |
|---|---|---|
| `transient_fetch` | `proof.transport_error` is set | re-fetches the URL; **drops** the finding if it now succeeds |
| `location_integrity` | every URL in `locations` was never fetched this run | **drops** - the reader cannot reproduce it |
| `coverage_adequacy` | a site-wide *absence* claim, on a site with more than 8 known URLs, where under 25% were read | **downgrades** confidence and appends a coverage note |
| `proof_consistency` | `proof.pages_affected` exceeds the pages actually read | **drops** - the count is impossible |

Two deliberate asymmetries:

* **Thin coverage downgrades, it never drops.** Not having looked at most of a site
  makes a site-wide claim *unproven*, not false. Deleting it would trade a false
  positive for a false negative; annotating it lets the reader weigh the sample.
* **Page-level claims are never coverage-gated.** "This page serves 12 characters of
  text" is a measurement of one document and stands on its own however little of the
  site was read.

The stage's decisions are reported under `verification`, so a dropped detection is
visible rather than silently discarded.

## Ranking and ids

Findings sort by severity rank (critical > high > medium > low), then by the fixed
category order in `lib/contracts.py`. Ids are assigned after sorting:

```
MKT-<AREA>-<rank>     e.g. MKT-REND-01, MKT-SCHEMA-03, MKT-TRUST-04
```

Area codes: CRAWL, REND, EXTR, SCHEMA, IMG, FRESH, ENTITY, TRUST, ENGAGE.
Ids are stable for a given input, so two runs over the same site produce the same ids.

## Health score

`100 - sum(severity_cost)`, clamped to 0-100, with critical=34, high=22, medium=11,
low=4. It is a communication aid, not an input to any detector.

## Error boundary

Each skill is called inside `try/except`. A raising skill is recorded in
`telemetry.skills[]` with `status: "error"` and its exception text; its categories
then report as `not_checked` in `coverage` rather than silently as `clean`. This is
why one broken skill degrades coverage instead of failing the audit.
