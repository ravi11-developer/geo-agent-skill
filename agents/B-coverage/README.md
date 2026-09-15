# AI Discoverability & Engagement Audit — Agent Skill Marketplace

Point this at a URL and it answers two questions with evidence: **why an AI
assistant can't find, read or cite this brand**, and **why a visitor who does
arrive doesn't stay**.

Seven read-only skills, composed at run time by one entrypoint skill, emitting a
single structured audit report. No API key, no configuration, no writes to the
audited site.

```bash
pip install -r requirements.txt
python run.py https://example.com              # -> audit_example.json
```

---

## Layout

```
marketplace.json          manifest: skills, entrypoint, contracts, safety envelope
run.py                    entrypoint -> run_audit(url) -> report dict
schema/audit.schema.json  JSON Schema for the report
skills/                   seven skill folders, each with SKILL.md + skill.json
lib/                      shared contracts, manifest loader, verification
  llm/                    OPTIONAL semantic layer, off by default
examples/                 four real-world reports across different stacks
tools/validate_package.py package + contract self-check
tools/stress_test.py      robustness suite against pathological servers
```

Each skill folder is an independently valid [agentskills.io](https://agentskills.io)
skill: a `SKILL.md` with YAML frontmatter and instructions, detailed checklists
pushed down to `references/`, executable checks under `scripts/`, and a
machine-readable `skill.json` declaring its detectors, severities and safety
envelope.

---

## The skills

`audit-orchestrator` is the entrypoint. The other six are specialists it
composes — they are not meant to be invoked directly.

| skill | one concern | emits |
|---|---|---|
| **audit-orchestrator** *(entrypoint)* | plan → invoke → merge → prioritise → report | the final report |
| **crawl-render-audit** | Can a machine fetch the pages, and is the substance there before JavaScript runs? | `crawlability`, `rendering` — **and the shared `snapshot`** |
| **content-semantics-audit** | Are the facts a buyer would ask about present as machine-readable text? | `content_extraction`, `structured_data`, `non_text_facts` |
| **entity-freshness-audit** | Is this one identifiable company, and are its facts still current? | `entity_identity`, `freshness` |
| **engagement-audit** | Can a visitor who lands on a deep page tell where they are and continue? | `engagement` |
| **offsite-discoverability-audit** | Does the brand anchor itself to the wider web well enough to be identified and corroborated? | recommendations only |
| **sentiment-engagement-audit** | Does the copy give a visitor a reason to stay, and can an answer engine restate it without guessing? | `engagement_sentiment` — *off by default* |

**Why the split is not padding.** Each skill owns one causal mechanism and one
set of categories, declared in its `skill.json` as `provides`. No two skills
detect the same category, so the manifest itself is the proof that
responsibilities don't overlap. The division follows the failure modes, not the
code: fetching and rendering are one root cause, fact extractability is another,
identity and recency a third, on-site orientation a fourth, off-site anchoring a
fifth.

**Two skills deliberately break the "emit findings" pattern, and the reason is
evidence discipline:**

- `offsite-discoverability-audit` emits **recommendations only, never defects**.
  A read-only crawl of one origin cannot observe the wider web, so it cannot
  *prove* that a brand lacks external corroboration. It reports what the site
  could do to become citable, and refuses to call the absence of evidence a
  defect.
- `sentiment-engagement-audit` is **inert unless the optional semantic layer is
  switched on**. With the layer off it performs no work and returns empty.

---

## How the entrypoint composes them

The orchestrator performs no detection of its own. It only sequences, merges and
ranks:

1. **Plan.** Reads `marketplace.json`, loads every enabled skill, and
   topologically orders them from their declared `consumes` / `produces`.
   `crawl-render-audit` produces the `snapshot`; everyone else consumes it and
   touches the network **zero** times, so the site is fetched exactly once.
   Adding a skill is a manifest edit, not a code change.
2. **Invoke.** Each skill runs inside its own error boundary. A skill that
   raises is recorded in telemetry; the audit completes with the remaining
   skills rather than failing the run.
3. **Merge.** One finding per category. When two skills reach the same category,
   the stronger evidence becomes the finding and the weaker is attached as
   `corroborated_by` — nothing measured is discarded, nothing is double-reported.
4. **Prioritise.** Severity is relative guidance, not a fixed label. On a site
   with four or more findings including a blocking one (`crawlability`,
   `rendering`, `content_extraction`, `entity_identity`), secondary findings are
   capped at `medium` and carry a `severity_note` explaining the down-rank, so
   the report answers *"what do I fix first"*. The measurement itself never
   changes.
5. **Report.** Assigns stable ids (`MKT-<AREA>-<rank>`), computes a health score,
   and publishes a `coverage` map across all eight categories so a reader can
   distinguish *checked and clean* from *not checked*. Proactive advice stays in
   `recommendations`, never mixed into `findings`.

---

## The report

Superset of the handout's required schema; validated by `schema/audit.schema.json`.

```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": { "total_findings": 2, "high": 1, "medium": 1, "health_score": 67 },
  "findings": [
    {
      "id": "MKT-STR-01",
      "category": "structured_data",
      "severity": "medium",
      "title": "No machine-readable structured data anywhere on the site",
      "evidence": "Crawled 12 pages; 0 contain schema.org markup.",
      "suggested_action": { "summary": "Add Product/Offer JSON-LD to every product page.", "priority": "medium" }
    }
  ],
  "recommendations": [ { "title": "...", "detail": "...", "effort": "low" } ]
}
```

| field | meaning |
|---|---|
| `summary` | totals, severity histogram, health score, top priority, runtime |
| `findings` | ranked **defects** — evidence, locations, suggested action, mechanism |
| `recommendations` | **proactive** improvements that are not defects, never counted as problems |
| `coverage` | `flagged` / `clean` / `not_checked` per category |
| `checks_performed` | every assertion each skill evaluated, pass or fail |
| `telemetry` | per-skill runtime, findings count, errors; pages fetched |
| `audit_health` | `complete` / `partial` / `failed`, page accounting, warnings |

Categories: `crawlability`, `rendering`, `content_extraction`, `structured_data`,
`non_text_facts`, `freshness`, `entity_identity`, `engagement`.

Empty `findings` is a valid outcome. On a healthy site the value of the report is
the `coverage` map plus the recommendations.

See [`examples/`](examples/) for four reports across a React SPA, a WordPress
site, hand-written HTML and a Shopify storefront.

---

## Design rules that drive the results

**Evidence is a contract, not a convention.** `lib/contracts.py::make_finding()`
*rejects at construction time* any finding whose evidence lacks a reproducible
measurement. Every finding cites the URL, the observed HTTP status and a count.
A detector that cannot prove its claim cannot emit it.

**Recall comes from mechanism, not keywords.**
- *JS-only facts*: inline scripts are mined for the markup they inject and the
  elements they target; the recovered payload is diffed against the raw HTML, so
  the report can quote the prices a non-rendering crawler never sees.
- *Image-only facts*: images are scored on filename, host heading, alt quality
  and surrounding prose, so an infographic that nothing restates is caught.
- *Entity ambiguity*: names are collected from ten identity slots across all
  pages, normalised (including legal suffixes like `B.V.`), and compared
  pairwise.

**Precision comes from preconditions.** Every detector requires several
independent conditions to agree — `content_extraction` needs three. Cosmetic SEO
habits (multiple H1s, keyword-stuffed titles, underscore URLs, generic anchors,
several JSON-LD blocks) are explicitly classified as **non-defects**. Anything
worth saying that isn't a defect goes to `recommendations`.

**A throttled auditor is not a broken site.** A crawl that hits an explicit 429,
or a wall of refusals that begins only after the crawl was already working,
reports an audit limitation in `audit_health` and emits **no** `crawlability`
finding.

---

## Optional semantic layer

Ships **off**. With `LLM_MODE=off` (the default) the marketplace builds no
client, makes no model call, and needs no API key.

When enabled, the division is structural: Python collects and verifies facts; a
model interprets meaning and writes contextual remediation; deterministic code
decides what enters the report. The model never sees raw HTML, never receives a
tool, and never writes to `findings` — it returns proposals that a validator
accepts or discards. Deterministic findings are never removed, downgraded or
re-severitied by any mode.

```bash
export LLM_ENABLED=true LLM_MODE=semantic_shadow
export LLM_PROVIDER=fake LLM_MODEL=offline-analyst   # scripted responder: no key, no cost
```

| mode | what runs | can it change a finding? |
|---|---|---|
| `off` **(default)** | nothing; no client is built | no |
| `suggestions_only` | richer remediation detail on existing findings | no — added at `suggested_action.detail`, never replacing `summary` |
| `semantic_shadow` | aspect-level analysis reported under `observations` | no |
| `semantic_enabled` | shadow + gated promotion | only by *adding* a validated semantic finding |
| `full` | + verifier and AI error diagnosis with allowlisted recovery | as above |

Both `LLM_ENABLED` and `LLM_MODE` are required; `LLM_ENABLED=false` is a hard
master switch, so a stray `LLM_MODE` in a shell can never start spending money.
If the provider cannot be built, the audit runs deterministically and records
`llm_provider_unavailable` in `audit_health.fallbacks_used`. It never fails and
never invents a finding.

**Page text is untrusted data everywhere.** Evidence is JSON-encoded inside a
fence page content cannot close; every system prompt states that website text is
evidence, never instruction. Credentials, keys, tokens, emails and card numbers
are redacted before any payload is built. Full configuration table:
`lib/llm/config.py`.

---

## Safety

Read-only by construction: **GET only**, robots.txt and `Crawl-delay` obeyed, at
most 150 pages and 6 concurrent requests per run, ~8s per request, no cookies
persisted, no writes to the target, no PII collected. No skill modifies a live
site; suggested actions are recommendations, never applied changes.

Dependencies are `requests` and `beautifulsoup4`, with a `urllib` fallback if
`requests` is absent. A failing skill degrades coverage rather than the run.
Typical runtime is well inside the 5-minute budget.

```bash
python tools/validate_package.py    # 134 conformance checks: manifest, SKILL.md, schema, safety
python tools/stress_test.py         # pathological servers: huge pages, redirect loops, slow bytes
```

---

## Known limitations

- Rendering is a deterministic **simulated** render (inline-script mining), not
  a headless browser: it recovers markup injected by inline JavaScript, but not
  content fetched at runtime from an API.
- `offsite-discoverability-audit` infers off-site anchoring from on-site signals
  (`sameAs`, Wikidata/LinkedIn links, press and newsroom surface). It does not
  query third-party services, so it recommends rather than asserts.
- Semantic findings fall outside the eight-category taxonomy and are declared
  `other`. That is why promotion is off by default and `semantic_shadow` is the
  highest mode recommended.
- Page-type classification for ambiguous URLs is deterministic-only; the hook
  for LLM-assisted classification exists but is not wired to a prompt.

---

**License:** MIT · **Python:** 3.9+ · Adobe University Hackathon 2026 — Round 3
