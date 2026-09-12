# AI Discoverability & Engagement Audit Marketplace

An Agent Skill Marketplace that audits a website for how well AI answer engines and
autonomous agents can **retrieve, render, extract, resolve, trust and navigate** it —
then reports what to fix first, with evidence for every claim.

Round 3 submission, Adobe University Hackathon 2026.

```bash
pip install -r requirements.txt
python run.py https://example.com audit.json
```

## What it produces

`audit.json`, validated by `schema/audit.schema.json`:

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
a Shopify storefront are in [`examples/`](examples/).

## The marketplace

`marketplace.json` is the installed catalogue; the orchestrator loads it at run time, so
adding or reordering a skill is a manifest edit, not a code change.

| skill | provides | reads |
|---|---|---|
| **audit-orchestrator** *(entrypoint)* | the report | — |
| crawl-render-audit | `crawlability`, `rendering` | the live site (GET only) |
| content-semantics-audit | `content_extraction`, `structured_data`, `non_text_facts` | shared snapshot |
| entity-freshness-audit | `entity_identity`, `freshness`, `corroboration` | shared snapshot |
| engagement-audit | `engagement` | shared snapshot |

Every skill folder follows the Agent Skills layout:

```
skills/<skill>/
  SKILL.md        # lean: when to use, what it emits, precision guards
  skill.json      # machine-readable detectors, severity model, safety
  scripts/        # the executable check
  references/     # thresholds, worked examples, false-positive log
```

The site is fetched **once**: `crawl-render-audit` publishes a `SiteSnapshot` and the
other three read it. The loader topologically orders skills from their declared
`consumes`/`produces`.

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

## Testing

```bash
python tools/validate_package.py --strict            # 96 package conformance checks
python tools/validate_package.py --audit audit.json  # + output schema validation
python tools/stress_test.py                          # 33 robustness cases
```

`tools/stress_test.py` serves pathological responses from a local server and asserts the
audit neither raises nor breaks its schema: 8MB documents, 6,000-level DOM nesting, 2,000
images, invalid UTF-8, charset-lying pages, UTF-16 with BOM, gzip bodies, HTTP 500/403,
empty bodies, PDFs and JSON at the entry URL, missing Content-Type, NUL bytes, RTL/CJK
text, redirect loops, stalled servers, connection refused, robots.txt that disallows
everything, binary robots.txt, malformed and exotic JSON-LD, `<base href>`, 5,000-link
pages, cyclic links, data: URIs — plus 8-way concurrency and an idempotency check.

Regression cases are also encoded as behaviour: a JS widget on a content-rich page must
**not** raise `rendering`, and `noindex` on a cart URL must **not** raise `crawlability`.

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

## Layout

```
marketplace.json          catalogue, contracts, safety envelope
run.py                    entrypoint: run_audit(url) -> audit.json
schema/audit.schema.json  the output contract
lib/                      shared page model, finding contract, manifest loader
skills/                   the five skills
tools/                    package validator, stress suite
examples/                 four real audit.json outputs
```

## Requirements

Python 3.9+, `requests`, `beautifulsoup4`. `PyYAML` and `jsonschema` make the validator
stricter and are optional — it falls back to built-in parsers without them.
