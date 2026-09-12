# AI Discoverability & Engagement Audit — Precision Build

An Agent Skill Marketplace built to the Round 3 model: a manifest, exactly one
entrypoint skill, and specialist skills composed at run time rather than wired
together in code.

```
marketplace.json                  manifest: skills, contracts, safety envelope
run.py                            entrypoint -> run_audit(url) -> report dict
lib/contracts.py                  shared Page / SiteSnapshot model + the finding contract
lib/loader.py                     manifest-driven loader and dependency ordering
lib/llm/                          null feature layer — see "Determinism" below
skills/
  audit-orchestrator/             ENTRYPOINT: composes the others, emits the report
  crawl-render-audit/             reachability, robots, redirects, JS-render gaps
  content-semantics-audit/        extraction, structured data, facts locked in non-text
  entity-freshness-audit/         entity identity, name ambiguity, staleness
  engagement-audit/               orientation, navigation landmarks, internal linking
tools/validate_package.py         97 structural checks; run before submitting
```

## Determinism

`lib/llm/` exists only to satisfy the orchestrator's imports. It contains no
provider, no prompts, no API surface and no network path — the capability is
**absent from the package**, so no environment variable can switch a model on.
The same site always produces the same report.

That is the design bet: a report a reader can re-run and reproduce exactly is
worth more than one that is occasionally cleverer.

## How the entrypoint composes the others

1. `crawl-render-audit` runs first and produces the shared `SiteSnapshot` — every page fetched once, then reused. No other skill makes a request.
2. The three analysis skills each read that snapshot and own one mechanism apiece. They never read each other's output, so a change to one cannot silently move another's numbers.
3. The orchestrator merges findings — one per category, weaker detections attached as `corroborated_by` — ranks by whether the mechanism blocks machine comprehension outright, assigns ids, and records every severity down-rank in `severity_note`.
4. Proactive recommendations are collected, de-duplicated, ranked by effort and capped.

## The evidence contract

`lib/contracts.make_finding` rejects, at construction time, any finding whose
evidence carries no reproducible measurement. A detector cannot report a
suspicion; it has to report a count, a ratio, a status code or a date. This is
the single mechanism most responsible for the false-positive rate.

## Run it

```bash
python run.py https://example.com report.json
python tools/validate_package.py        # structural check
```

Python 3.9+, `requests`, `beautifulsoup4`. No API key. Outbound HTTP GET only;
never writes to the audited site.
