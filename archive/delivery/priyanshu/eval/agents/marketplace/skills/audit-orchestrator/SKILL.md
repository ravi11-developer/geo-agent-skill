---
name: audit-orchestrator
description: Entrypoint skill for the AI Discoverability & Engagement Audit Marketplace. Plans an audit of a single website, invokes the installed specialist skills in dependency order, merges and prioritises their findings, and emits one structured, evidence-backed report.
version: 1.0.0
license: MIT
kind: orchestrator
allowed-tools: []
safety: read-only; no network calls of its own; delegates all fetching to crawl-render-audit
---

# Audit Orchestrator

## When to use

Use this skill when someone asks *"audit this website for AI discoverability / AI
search visibility / how well an AI assistant can read and cite this site"*. It is
the only entrypoint: the specialist skills are not meant to be called directly by
a user, they are composed by this one.

**Input:** a single URL.
**Output:** the audit report described in `marketplace.json` -> `contracts`.

## What it does

1. **Plan.** Reads `marketplace.json`, loads every enabled skill, and topologically
   orders them so producers run before consumers (`crawl-render-audit` produces the
   shared `snapshot` that the other three consume, so the site is fetched exactly once).
2. **Invoke.** Runs each skill inside its own error boundary. A skill that raises is
   recorded in telemetry with its traceback message; the audit still completes with
   the remaining skills instead of failing the run.
3. **Merge.** One finding per category. When two skills detect the same category, the
   stronger evidence becomes the finding and the weaker is attached as
   `corroborated_by`, so nothing measured is discarded and nothing is double-reported.
4. **Prioritise.** Severity is treated as *relative* guidance, not a fixed label. When a
   site has four or more findings including a blocking one (`crawlability`,
   `rendering`, `content_extraction`, `entity_identity`), secondary findings
   (`engagement`, `non_text_facts`, `freshness`) are capped at `medium` and carry a
   `severity_note` explaining the down-rank. The measurement itself never changes.
5. **Report.** Assigns stable ids (`MKT-<AREA>-<rank>`), computes a health score,
   and publishes a `coverage` map for all eight categories, so a reader can tell
   *checked and clean* from *not checked*. Proactive advice is kept in
   `recommendations`, never mixed into `findings`.

## Report shape

| field | meaning |
|---|---|
| `summary` | totals, severity histogram, health score, top priority, runtime |
| `findings` | ranked defects, each with evidence, locations, suggested action, mechanism |
| `recommendations` | proactive improvements that are **not** defects |
| `coverage` | `flagged` / `clean` / `not_checked` per category |
| `checks_performed` | every assertion each skill evaluated, pass or fail |
| `telemetry` | per-skill runtime, findings count, errors; pages fetched |

## Design rules

- A category is reported at most once per audit.
- A finding is never invented by the orchestrator; it only merges and ranks.
- Empty findings is a valid, expected outcome: on a healthy site the value of the
  report is the `coverage` map plus the recommendations.
