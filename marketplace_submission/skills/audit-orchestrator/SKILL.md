---
name: audit-orchestrator
description: Entrypoint for the AI Discoverability & Engagement Audit Marketplace. Audits one website end to end - plans the run, invokes the specialist skills, merges and prioritises their findings, and emits a single evidence-backed audit.json. Use when asked to audit a site for AI discoverability, AI search visibility, or how well an assistant can read and cite it.
version: 1.1.0
license: MIT
kind: orchestrator
provides: []
consumes: []
produces: [audit_report]
allowed-tools: []
safety: read-only; performs no network calls of its own; delegates all fetching to crawl-render-audit
---

# Audit Orchestrator

Single entrypoint for the marketplace. The specialist skills are composed by this
one and are not meant to be invoked directly.

## When to use

"Audit this site for AI discoverability", "why don't AI assistants cite us",
"check our AI search visibility". Input is one URL; output is `audit.json`.

## Run it

```bash
python run.py https://example.com audit.json      # from the package root
```

Programmatically: `from run import run_audit; run_audit(url, config=None)`.

## What it emits

`audit.json` conforming to `schema/audit.schema.json`: `site`, `audited_at`,
`summary` (`total_findings`, `critical`, `high`, `medium`, `low`, `health_score`),
`findings[]` (`id`, `title`, `category`, `severity`, `evidence`, `locations`,
`suggested_action{summary, priority, mechanism}`), plus `recommendations[]`,
`coverage`, `checks_performed` and `telemetry`.

## How it composes

1. **Plan** - reads `marketplace.json`, loads enabled skills, orders them by their
   declared `consumes`/`produces` so the site is fetched exactly once.
2. **Invoke** - each skill runs inside its own error boundary; a skill that raises
   is recorded in `telemetry` and the audit still completes.
3. **Merge** - one finding per category; a weaker duplicate detection is attached
   as `corroborated_by` rather than emitted twice.
4. **Prioritise** - severity is relative: on a site with four or more findings
   including a blocking one, secondary findings are capped at `medium` and carry a
   `severity_note` recording the down-rank.
5. **Report** - stable ids, `coverage` for every category so a reader can tell
   *checked and clean* from *not checked*, and proactive advice kept out of
   `findings`.

Findings are never invented here: the orchestrator only merges, ranks and counts.
Empty `findings` is a valid result - the value is then `coverage` plus
`recommendations`.

## See also

- `references/composition.md` - merge, prioritisation and id rules in full
- `references/output-contract.md` - field-by-field schema notes and consumer guidance
- `scripts/orchestrate.py` - implementation
