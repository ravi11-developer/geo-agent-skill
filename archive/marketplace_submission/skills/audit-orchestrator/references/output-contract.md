# Output contract (audit.json)

Validated by `schema/audit.schema.json`; run `python tools/validate_package.py --audit audit.json`.

| field | type | notes |
|---|---|---|
| `site` | string | URL exactly as passed in |
| `audited_at` | string | ISO-8601 UTC timestamp |
| `agent` | string | `"marketplace"` |
| `marketplace` | object | manifest id/name/version and the skills actually invoked |
| `summary.total_findings` | int | equals `len(findings)` |
| `summary.critical/high/medium/low` | int | always present, zero when none; sums to `total_findings` |
| `summary.health_score` | int | 0-100 |
| `summary.top_priority` | string | title of the highest-ranked finding, or a clean-site message |
| `summary.pages_crawled` / `pages_retrievable` | int | crawl reach |
| `findings[].id` | string | `MKT-<AREA>-<rank>` |
| `findings[].category` | enum | one of the nine contract categories |
| `findings[].severity` | enum | critical / high / medium / low |
| `findings[].evidence` | string | **must** carry the page URL, the observed HTTP status and at least one measurement |
| `findings[].locations[]` | string | URLs the finding applies to |
| `findings[].proof` | object | machine-readable counters behind the evidence |
| `findings[].suggested_action.summary` | string | what to change |
| `findings[].suggested_action.priority` | enum | mirrors severity after prioritisation |
| `findings[].suggested_action.mechanism` | string | why it changes AI behaviour |
| `recommendations[]` | array | proactive advice; never a defect, never counted in `summary` |
| `coverage` | object | per category: `flagged` / `clean` / `not_checked` |
| `checks_performed[]` | array | every assertion each skill evaluated, pass or fail |
| `telemetry` | object | per-skill runtime, findings count, errors; pages fetched |

## Consumer guidance

- Read `summary.critical/high/medium` for triage counts; `by_severity` is the same
  numbers as a nested object and is retained for older consumers.
- Treat `recommendations` as opportunities, not defects: they are deliberately kept
  out of `findings` so precision metrics are not diluted.
- `coverage` distinguishes "checked and clean" from "not checked" - a skill that
  errored reports `not_checked`, never `clean`.
