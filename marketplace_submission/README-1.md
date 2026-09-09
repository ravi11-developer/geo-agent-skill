# AI Discoverability & Engagement Audit Marketplace

An Agent Skill Marketplace that audits a website for how well AI answer engines and
autonomous agents can **retrieve, render, extract, resolve, trust and navigate** it —
then reports what to fix first, with evidence for every claim.

Round 3 submission, Adobe University Hackathon 2026.

## Quick start

```bash
pip install -r requirements.txt
python run.py https://example.com audit.json
```

That crawls the site, runs the five skills below, and writes a validated `audit.json`
to disk. Drop the second argument to just print the summary to stdout.

You can also call it as a library:

```python
from run import run_audit

report = run_audit("https://example.com")
print(report["summary"]["health_score"])
```

(The docstring in `run.py` also shows the benchmark-harness import path,
`from eval.agents.marketplace.run import run_audit`, for when this package is dropped
into that harness.)

## What it produces

`audit.json`, validated against `schema/audit.schema.json`:

```json
{
  "site": "https://example.com",
  "audited_at": "2026-09-09T04:11:52.104Z",
  "summary": {"total_findings": 3, "critical": 0, "high": 1, "medium": 2, "low": 0,
              "health_score": 56, "top_priority": "Business-critical content is only present after client-side JavaScript execution"},
  "findings": [{
    "id": "MKT-REND-01",
    "category": "rendering",
    "severity": "high",
    "evidence": "http://…/ (HTTP 200) serves only 41 characters (6 words) of extractable body text; the element #root is empty in the raw HTML and is populated at runtime by inline JavaScript carrying 1,204 characters of markup…",
    "suggested_action": {"summary": "Server-side render (SSR) or statically pre-render…", "priority": "high",
                         "mechanism": "Most AI retrieval pipelines index the raw HTTP response…"}
  }],
  "recommendations": [],
  "coverage": {"crawlability": "clean", "rendering": "flagged", "…": "…"}
}
```

Four worked examples across a React SPA, a WordPress site, a hand-written HTML page and
a Shopify storefront are in [`examples/`](examples/) — look there first if you want to
see real output before running your own audit.

## How the pieces fit together

`marketplace.json` is the installed catalogue; `audit-orchestrator` (the entrypoint
skill) loads it at run time and runs the other four in dependency order, so adding or
reordering a skill is a manifest edit, not a code change.

| skill | provides | reads |
|---|---|---|
| **audit-orchestrator** *(entrypoint)* | the report | — |
| crawl-render-audit | `crawlability`, `rendering` | the live site (GET only) |
| content-semantics-audit | `content_extraction`, `structured_data`, `non_text_facts` | shared snapshot |
| entity-freshness-audit | `entity_identity`, `freshness`, `corroboration` | shared snapshot |
| engagement-audit | `engagement` | shared snapshot |

The site is fetched **once**: `crawl-render-audit` publishes a `SiteSnapshot` and the
other three skills read that shared snapshot rather than re-fetching. `lib/loader.py`
topologically orders skills from their declared `consumes`/`produces`, so you can add a
new skill by giving it a `skill.json` with the right `consumes`/`produces` keys and
listing it in `marketplace.json` — no wiring code required.

Every skill folder follows the Agent Skills layout:

```
skills/<skill>/
  SKILL.md        # lean: when to use, what it emits, precision guards
  skill.json      # machine-readable detectors, severity model, safety
  scripts/        # the executable check
  references/     # thresholds, worked examples, false-positive log
```

## Layout

```
run.py                    entrypoint: run_audit(url) -> audit.json
marketplace.json          catalogue, contracts, safety envelope
schema/audit.schema.json  the output contract
lib/                      shared page model, finding contract, manifest loader
skills/                   the five skills (see table above)
tools/                    package validator, stress suite
examples/                 four real audit.json outputs
```

## Testing your changes

```bash
python tools/validate_package.py --strict            # 96 package conformance checks
python tools/validate_package.py --audit audit.json  # + validate an output file against the schema
python tools/stress_test.py                          # 33 robustness cases
```

Run `validate_package.py --strict` after touching anything under `skills/` or `lib/` —
it checks manifest consistency, SKILL.md frontmatter, size limits, and scans shipped
code for disallowed operations (see Design commitments below) before you ever hit a
live site.

`tools/stress_test.py` serves pathological responses from a local server and asserts the
audit neither raises nor breaks its schema: 8MB documents, 6,000-level DOM nesting, 2,000
images, invalid UTF-8, charset-lying pages, UTF-16 with BOM, gzip bodies, HTTP 500/403,
empty bodies, PDFs and JSON at the entry URL, missing Content-Type, NUL bytes, RTL/CJK
text, redirect loops, stalled servers, connection refused, robots.txt that disallows
everything, binary robots.txt, malformed and exotic JSON-LD, `<base href>`, 5,000-link
pages, cyclic links, data: URIs — plus 8-way concurrency and an idempotency check.

Regression cases are also encoded as behaviour: a JS widget on a content-rich page must
**not** raise `rendering`, and `noindex` on a cart URL must **not** raise `crawlability`.

## Design commitments

**Evidence is a contract, not a convention.** `make_finding()` rejects any finding whose
evidence lacks a reproducible measurement; every finding cites the page URL, the observed
HTTP status and a count. A detector that cannot prove its claim cannot emit it.

**Detectors need several independent conditions to agree.** `content_extraction` needs
three. Cosmetic SEO habits — multiple H1s, keyword-stuffed titles, underscore URLs,
generic anchor text, verbose CSS — are classified as non-defects, not findings.

**No site-specific selectors.** Checks are domain-agnostic heuristics: fact-family
coverage, whole-token filename matching, canonical-anchored name resolution, structured-
data graphs. `tools/validate_package.py` fails the build if a CMS-theme class or vendor
token appears in skill code.

**Severity is relative.** On a multi-fault site, secondary findings are capped at medium
so the report answers "what do I fix first", and the down-rank is recorded in
`severity_note` rather than hidden.

**Read-only by construction.** GET only, robots.txt obeyed, ≤12 pages, bodies streamed
and truncated at 5MB, a 60s crawl budget, no cookies persisted, nothing written to the
target, no PII collected. The validator scans shipped code for non-GET calls, subprocess,
eval/exec and file deletion.

## Measured behaviour

Against 83 captured real sites (government portals, universities, NGOs, D2C storefronts,
hospitals, marketplaces, SPA product sites, B2B manufacturers, media):

| metric | value |
|---|---|
| sites audited without error | 83 / 83 |
| latency | mean 0.42s, median 0.24s, p95 1.28s, max 3.35s (budget 300s) |
| findings | 73 across all nine categories; 30 sites clean |
| findings per site | 0.88 — conservative by design |
| evidence completeness | 73 / 73 carry URL + HTTP status + measurement |
| schema validity | 83 / 83 |
| package size | 0.24 MB of 50 MB |

## Requirements

Python 3.9+, `requests`, `beautifulsoup4`. `PyYAML` and `jsonschema` make the validator
stricter and are optional — it falls back to built-in parsers without them.

## Troubleshooting

- **`ModuleNotFoundError: requests` / `bs4`** — you skipped `pip install -r requirements.txt`.
- **`tools/validate_package.py` or `requirements.txt` "not found"** — you're not standing
  in the `marketplace_submission/` folder, or you're pointed at a stale copy of it.
  `dir requirements.txt tools\validate_package.py` (PowerShell) or
  `ls requirements.txt tools/validate_package.py` (bash) from inside the folder should
  show both; if it doesn't, re-extract the package rather than patching the old folder.
- **A single skill misbehaves** — run its `scripts/` file directly against a small local
  HTML fixture before re-running the full orchestrator; each skill's `SKILL.md` documents
  its inputs/outputs in isolation.
