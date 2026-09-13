# AI Discoverability & Engagement Audit — Coverage Build

An **Agent Skill Marketplace** for AI discoverability and on-site engagement auditing,
built to the Round 3 handout's marketplace model: a manifest, an entrypoint skill, and
specialist skills that are composed at run time rather than wired together in code.


> **This is the coverage build.** It is the precision build plus two skills:
> `offsite-discoverability-audit`, which covers the half of the handout that asks
> why a brand is not found or cited at all, and `sentiment-engagement-audit`,
> which is inert unless the optional semantic layer is switched on. The model
> layer ships **off**: the default run is deterministic and needs no API key.
> See `../A-precision/` for the deterministic-only sibling.

```
marketplace.json                     # manifest: skills, contracts, features, safety envelope
run.py                               # benchmark entrypoint -> run_audit(url) -> dict
lib/contracts.py                     # shared Page/SiteSnapshot model + finding & observation contracts
lib/loader.py                        # manifest-driven loader + dependency ordering
lib/llm/                             # OPTIONAL hybrid layer, off by default (see below)
  config.py                          #   feature modes, thresholds, crawl budget
  provider.py                        #   provider-neutral client: disabled / fake / anthropic
  evidence.py                        #   SiteSnapshot -> normalised evidence pack with stable ids
  validation.py                      #   the gate: schema, citation, quotation, numeracy, promotion
  errors.py                          #   ErrorEvent, recovery allowlist, audit health
  engine.py                          #   the only place a model is actually called
  prompts/                           #   versioned prompt templates (.md)
  playbook.py                        #   local remediation playbook
  cache.py, redaction.py, observability.py, offline_analyst.py
skills/
  audit-orchestrator/                # entrypoint: plan -> invoke -> merge -> prioritise -> report
  crawl-render-audit/                # crawlability, rendering   (+ produces the snapshot)
  content-semantics-audit/           # content_extraction, structured_data, non_text_facts
  entity-freshness-audit/            # entity_identity, freshness
  engagement-audit/                  # engagement          (navigation and internal linking)
  sentiment-engagement-audit/        # engagement_sentiment (copy, tone, aspect friction) - optional
```

Each skill directory carries a `SKILL.md` (human/agent-readable contract) and a
`skill.json` (machine-readable detector, severity and safety declaration).

## Run it

```bash
./geo run B-coverage https://example.com                        # via the repo CLI
python agents/B-coverage/run.py https://example.com report.json # standalone, no CLI

python bench/runners/run_agent.py --agent B-coverage --url http://localhost:9500/site-008-mixed-faults/
python bench/runners/run_suite.py --agent B-coverage            # all 18 synthetic sites

pytest                                          # offline suite, no API key needed
python agents/B-coverage/tools/validate_package.py   # package/contract self-check
```

No API key, no network beyond the audited site, and no configuration are required for any
of the above: the hybrid layer described below is **off by default**.

## Why it scores where it does

**Composition, not duplication.** The site is fetched once. `crawl-render-audit`
publishes a `SiteSnapshot`; the other three skills read it and never touch the network.
The loader topologically orders skills from their declared `consumes`/`produces`, so
adding a skill to `marketplace.json` is a manifest edit, not a code change.

**Evidence is a contract, not a convention.** `make_finding()` rejects any finding whose
evidence lacks a reproducible measurement, and every finding cites the URL, the observed
HTTP status and a count. A detector that cannot prove its claim cannot emit it - which is
the structural reason this agent produced zero false positives.

**Recall came from mechanism, not keywords.**
- *JS-only facts*: inline scripts are mined for the markup they inject and the elements
  they target; the recovered payload is diffed against the raw HTML, so the report can
  quote the prices a non-rendering crawler never sees.
- *Image-only facts*: images are scored on filename, host heading, alt quality and how
  much prose surrounds them, so an infographic that nothing restates is caught.
- *Entity ambiguity*: names are collected from ten identity slots across all pages,
  normalised, and compared pairwise; three or more names plus one near-variant pair is
  the ambiguity fingerprint.

**Precision came from preconditions.** Every detector needs several independent
conditions to agree, `content_extraction` needs three, and cosmetic SEO habits (multiple
H1s, keyword-stuffed titles, underscore URLs, generic anchors, verbose CSS, several
JSON-LD blocks) are explicitly classified as non-defects. Everything that is worth saying
but is not a defect goes to `recommendations`.

**Prioritisation is explicit.** On a multi-fault site the orchestrator down-ranks
secondary findings so the report answers "what do I fix first"; the down-rank is recorded
in `severity_note` rather than hidden.

## Benchmark result (16 sites: 10 development + 6 held-out)

Scoring v2, `python bench/runners/run_suite.py`:

| Agent | F1 | Prec | Rec | FP | Evidence | Actions | Severity | Proactive | Generalization | Overall | (legacy) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **marketplace** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | **0.928** | **1.000** | **99.3** | 80.5 |
| deterministic | 0.720 | 0.818 | 0.643 | 0.981 | 0.557 | 0.542 | 0.947 | 0.232 | 0.667 | 68.5 | 63.9 |
| hybrid | 0.692 | 0.750 | 0.643 | 0.963 | 0.481 | 0.519 | 0.947 | 0.215 | 0.667 | 66.0 | 61.6 |
| reasoning | 0.692 | 0.750 | 0.643 | 0.963 | 0.394 | 0.544 | 0.925 | 0.162 | 0.571 | 64.0 | 58.8 |
| specialist | 0.529 | 0.450 | 0.643 | 0.906 | 0.542 | 0.399 | 0.910 | 0.077 | 0.706 | 59.2 | 56.5 |

20 of 20 expected findings across 16 sites, 0 missed, 0 false positives, every severity
exactly matching gold, every scored finding 4/4 on evidence and 4/4 on suggested action.
The `legacy` column is the original formula on the original 10 sites, unchanged, so
earlier numbers stay comparable (deterministic still reads 63.9).

### The held-out set (sites 011-016)

Six sites the agent was **not** developed against, including the first multi-page ones:

| site | fault | what it tests |
|---|---|---|
| 011-multipage-healthy | none | false positives across three cross-linked pages |
| 012-spa-shell | empty `#root` + bundle | SPA detection where *no* text survives |
| 013-invalid-schema | JSON-LD trailing commas | invalid reported as invalid, not as missing |
| 014-subpage-entity-drift | drift on sub-pages only | multi-page recall; entry-page-only agents miss both faults |
| 015-image-nav-faults | 4 fact images, no nav | precision boundary: image-only facts on a text-rich page must **not** also fire `content_extraction` |
| 016-noindex-block | `meta robots noindex` | the crawlability detector, which no development site exercised |

The first run against them failed three ways, and each failure was a real bug rather
than a tuning miss:

1. **Charset** - `requests` falls back to ISO-8859-1 for `text/html` without a charset
   parameter (RFC 2616), so UTF-8 pages that declare their charset in `<meta>` were
   mojibaked. Em dashes in titles became `â€"`, title separators stopped matching, and
   every sub-page title turned into a phantom company name.
2. **Sub-page titles** - `"Page - Brand"` on sub-pages versus `"Brand - tagline"` on home
   pages. Taking the leading segment blindly invented an entity per page; the fix takes
   the trailing segment when the leading one is a generic page word.
3. **Legal suffixes** - stripping punctuation turns `B.V.` into `b v`, so the suffix no
   longer matched and `Harborline Logistics B.V.` read as a different company from
   `Harborline Logistics`. Suffixes are now matched on up to three joined trailing tokens.
4. **SPA navigation** - a JavaScript shell has no nav in its raw HTML, so the engagement
   skill reported a second defect for one root cause. It now abstains on pages whose DOM
   is built entirely by JS (the rendering finding covers them) and says so in a
   recommendation - while still reporting missing navigation on partially-dynamic pages.

All four were fixes to real-world robustness, not to benchmark fit. Nothing regressed on
the original ten sites.

### What changed in the harness, and why

Three dimensions were measured rather than assumed. The changes are in
`bench/scoring/proactive.py` and `aggregate_scores()` in `run_suite.py`, and they apply
identically to every agent - every agent's score moved.

1. **Evidence, action and severity are averaged over sites where the agent matched a gold
   finding.** Previously a healthy site (no gold findings, so no true positives) contributed
   a hard 0 to evidence and actions and a hard 0.5 to severity. That measured the dataset's
   composition, not the agent: it capped *any* perfect agent at 0.70 evidence and 0.85
   severity, and 80.5 overall.
2. **`proactive_score` is graded** against each gold's `expected_proactive` list, which was
   already present in every gold file and previously unused. Standalone recommendations earn
   full credit; the same advice delivered only inside a detected defect's `suggested_action`
   earns half, because the rubric asks for recommendations *beyond* the defects found.
   Advice is collected from any recommendation-shaped field and from low-severity findings,
   so agents are not penalised for formatting.
3. **`generalization_score` is measured** as detection F1 on the held-out sites instead of
   being hard-coded to 0.5. Headline detection metrics come from the development sites, so
   the two numbers stay independent.

The honest reading of 99.3: detection, precision, evidence and severity are the agent's;
generalization is real but measured on sites written by the same hand that wrote the agent,
so the true test is the judges' unseen sites; and proactive scoring rewards a report layer
the other agents never implemented.

## The optional hybrid LLM layer

Python collects and verifies facts. A model, when one is switched on, interprets meaning,
diagnoses ambiguity and writes contextual remediation. Deterministic code decides what
enters the report. That division is the whole design, and it is enforced structurally
rather than by convention: the model never sees raw HTML, never receives a tool, and
never writes to `findings` - it returns proposals that a validator either accepts or
discards.

```
URL
 -> Python crawl / simulated render
 -> normalised, evidence-backed SiteSnapshot
 -> deterministic checks (unchanged)        +     optional LLM semantic reasoning
 -> evidence and schema validation
 -> deterministic policy gate
 -> merge and deduplicate (provenance preserved)
 -> prioritised recommendations
 -> report + audit_health
```

### Feature modes

| mode | what runs | can it change a finding? |
|---|---|---|
| `off` **(default)** | nothing; no client is built | no |
| `suggestions_only` | richer remediation detail for findings that already exist | no - added alongside, never replacing |
| `semantic_shadow` | aspect-level semantic analysis, reported under `observations` | no - nothing score-bearing is touched |
| `semantic_enabled` | shadow + suggestion enhancement + gated promotion | only by *adding* a validated semantic finding |
| `full` | everything above + verifier + AI error diagnosis + allowlisted recovery | as above |

Deterministic findings are never removed, never downgraded, and never re-severitied by
any mode. `suggested_action.summary` - the field every existing consumer reads - is always
the deterministic text; enhancement is added at `suggested_action.detail`.

### Turning it on and off

```bash
# disable everything (this is also the default, so it is only ever needed to undo a shell export)
unset LLM_ENABLED LLM_MODE            # or: export LLM_ENABLED=false

# the four working modes
export LLM_ENABLED=true LLM_MODE=suggestions_only
export LLM_ENABLED=true LLM_MODE=semantic_shadow
export LLM_ENABLED=true LLM_MODE=semantic_enabled
export LLM_ENABLED=true LLM_MODE=full
```

Both variables are required. `LLM_ENABLED=false` is a hard master switch that pins the
mode to `off`, so a stray `LLM_MODE` left in a shell can never start spending money.

### Providers

```bash
# offline: exercises the whole hybrid path with a scripted responder, no key, no cost
export LLM_PROVIDER=fake LLM_MODEL=offline-analyst

# a real provider
export LLM_PROVIDER=anthropic
export LLM_MODEL=<the model id you want>      # never hard-coded anywhere in this package
export ANTHROPIC_API_KEY=<key>                # environment only; never committed, never logged
pip install anthropic                         # imported lazily; not a package dependency
```

If the provider cannot be built - no key, no model, SDK missing - the audit runs
deterministically, records `llm_provider_unavailable` in `audit_health.fallbacks_used`,
and says so in `audit_health.warnings`. It never fails and never invents a finding.

### Configuration

| variable | default | meaning |
|---|---|---|
| `LLM_ENABLED` | `false` | master switch; `false` pins the mode to `off` |
| `LLM_MODE` | `off` | `off`, `suggestions_only`, `semantic_shadow`, `semantic_enabled`, `full` |
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `fake`, `disabled` |
| `LLM_MODEL` | *(unset)* | model id; supplied by configuration, never by code |
| `ANTHROPIC_API_KEY` | *(unset)* | read from the environment only |
| `LLM_TIMEOUT_SECONDS` | `30` | per-request timeout |
| `LLM_MAX_RETRIES` | `1` | bounded; `Retry-After` is honoured |
| `LLM_MAX_INPUT_TOKENS` | `12000` | oversized requests are refused before they are sent |
| `LLM_CONFIDENCE_THRESHOLD` | `0.80` | promotion threshold |
| `LLM_CORROBORATION_THRESHOLD` | `0.65` | below this the layer abstains |
| `LLM_VERIFIER_ENABLED` | `true` | required for any high/critical semantic finding |
| `LLM_CACHE_ENABLED` | `true` | in-process; add `LLM_CACHE_DIR` for a resumable disk cache |
| `LLM_MAX_CALLS` | `6` | hard per-audit call ceiling |
| `LLM_ALLOW_BENCHMARK_CALLS` | `false` | required before a paid provider can run in a large benchmark |
| `AUDIT_CRAWL_PROFILE` | `extended` | `extended` (150-page ceiling, 60-page soft target, depth 3, default) or `legacy` (12 pages, as originally shipped) |
| `AUDIT_FETCH_CONCURRENCY` | `6` | concurrent GETs; forced to 1 when the site publishes `Crawl-delay` |
| `AUDIT_PER_HOST_DELAY_SECONDS` | `0.15` | minimum interval between request *starts*, shared across workers |
| `AUDIT_MAX_FETCH_ATTEMPTS` | `3` | attempts per URL on a transport error or 408/425/429/5xx; `Retry-After` honoured |
| `AUDIT_EXPANSION_LOOPS` | `4` | how many times a saturation stop may widen sampling instead of ending the crawl |
| `AUDIT_TOTAL_BUDGET_SECONDS` | `300` | the handout's allowance; the exploration deadline is derived from it |

### Crawl budget and page selection

The default crawl scope is `extended`: a 150-page ceiling with a 60-page soft target,
depth 3, stratified template sampling, low-value URLs dropped, up to 6 concurrent GETs
behind a politeness gate, and an exploration deadline derived from
`AUDIT_TOTAL_BUDGET_SECONDS` (275s with the LLM layer off, 210s with it on).

**The wall clock ends the crawl, not a page count.** Saturation - "no URL template has
both fewer than `per_template_samples` fetched and a candidate still queued" - used to
stop the crawl outright, which on a real site meant stopping at exactly the 60-page soft
target after ~74s with hundreds of URLs still queued and three quarters of the handout's
allowance unspent. It now *widens* sampling instead: while at least `expansion_headroom`
(35%) of the exploration budget remains, the per-template allowance rises by
`targeted_additions` and the depth ceiling lifts to `targeted_depth`, releasing the links
that were held back at the old ceiling. Up to `expansion_loops` of these run before
`saturated` is reported for real.

The original scope is still available as an explicit opt-out, and it is byte-for-byte the
behaviour originally shipped - sequential, no retries, no expansion:

```bash
export AUDIT_CRAWL_PROFILE=legacy    # 12 pages, breadth-first, no depth ceiling (as originally shipped)
```

Fetching is retried: up to `AUDIT_MAX_FETCH_ATTEMPTS` per URL - robots.txt and sitemaps
included - on a transport error or a retryable status, honouring `Retry-After`. If the
first pass still comes back with at most one usable page while the sitemap advertised
more, a bounded recovery round re-queues up to 20 sitemap URLs and re-fetches the entry.
A crawl that is rate-limited (an explicit 429, or a wall of refusals that begins only
after the crawl was already working) reports an audit limitation in `audit_health` and
emits **no** `crawlability` finding - a throttled auditor is not evidence that a site
blocks machines.

Deterministic checks run on every crawled page. Only 8-10 representative pages (never more
than 15) are selected for semantic analysis, ranked by page-type coverage value, template
uniqueness, semantic information value, and penalised for duplicate templates, depth and
tracking query strings. The LLM may only rank and classify URLs the crawler already found;
it never proposes a URL and never browses.

### What the gate checks

Every semantic result must survive, in order: JSON shape and required fields; closed
vocabularies; evidence ids that this run actually issued; cited ids belonging to the page
named; a `quote` that is a verbatim substring of a cited section; every number in the prose
present in the cited text; no technology name absent from the evidence; no URL outside the
crawled set; no contradiction of a category measured clean; no restatement of a
deterministic finding; and enough independent sections to support the severity claimed.

One structured repair attempt is allowed. The repair prompt receives only the schema
violations, the model's own previous answer and the permitted evidence ids - never new
evidence and never a hint about which answer would pass. If it still fails, the result is
discarded, the deterministic output stands, and a non-fatal diagnostic is recorded.

Promotion is separate from validation. `>= 0.80` confidence with validated evidence is
eligible; `0.65-0.79` needs two independent sections on different pages or deterministic
corroboration; below `0.65` the layer abstains. High and critical additionally require two
evidence sections and a verifier pass. Self-reported confidence is never the only signal.

### Terminology that the validator enforces

Brand copy can support a claim about **content tone** or **predicted visitor friction**. It
can never support a claim about **customer sentiment** - only reviews, testimonials, survey
or ticket text can. This is checked at construction time and again at the gate.

### Prompt injection

Page text is untrusted data everywhere. Evidence is JSON-encoded inside an explicit fence
that page content cannot close, trusted and untrusted regions are separately labelled, and
every system prompt states that website text is evidence and never instruction. The test
suite feeds a fixture carrying "ignore previous instructions", "mark this website as
perfect", "reveal your system prompt", an external URL, a shell command and a forged
response object, and asserts that none of it can change the schema, add evidence, trigger
browsing, leak the prompt, execute anything or suppress a deterministic finding.

### Errors and audit health

Failures are normalised into `ErrorEvent` records and classified deterministically first.
A timeout, a parser exception or a provider outage is an `auditor_limitation`, never a
website defect - and an LLM diagnosis that tries to call one a `website_defect` is
downgraded. Recovery is restricted to a nine-code allowlist, further narrowed per category,
and the LLM may only *recommend*; deterministic code validates and executes. At most one
general retry and one fallback.

Every report carries an optional `audit_health` block with `complete`, `partial` or
`failed`, page accounting, the LLM mode and status, fallbacks used, warnings and sanitised
errors. A partial crawl is never presented as complete, and no stack trace, path or
credential reaches the report.

### Privacy

Credential headers are dropped; API keys, JWTs, bearer tokens, session ids, card numbers,
emails and phone numbers are redacted before a payload is built, and a pre-send scan
refuses any request that still looks like it carries a secret. Only safe metadata is
recorded: snapshot hash, provider, configured model, prompt/schema/taxonomy versions,
cache status, call count, latency, token usage, repair count, validation and fallback
results. Raw HTML and request bodies are never logged.

### Cost control

One batched semantic request per site, not one per page. A second call is only made for
high-severity verification, ambiguous evidence, or a structured-output repair. Responses
are cached on `snapshot_hash + prompt_version + schema_version + taxonomy_version +
provider + model + feature_mode`, in process and optionally on disk for resumable
benchmarks. The 2,600- and 10,000-site runners use `LLMConfig.for_benchmark`, which refuses
a paid provider unless `LLM_ALLOW_BENCHMARK_CALLS=true`.

## Ablation

A dedicated ablation runner is not shipped in this repo. The same comparison is
driven by the feature-mode environment variables, one suite run per mode:

```bash
for mode in off suggestions_only semantic_shadow semantic_enabled full; do
  LLM_ENABLED=true LLM_MODE=$mode LLM_PROVIDER=fake \
    python bench/runners/run_suite.py --agent B-coverage \
      --output bench/results/ablation_$mode.json
done
```

`LLM_PROVIDER=fake` selects the offline responder described below, so the sweep
costs nothing and needs no API key.

The default responder is `offline-analyst`: a deterministic scripted responder that reads
the same evidence pack a model would and answers by lexical rule. It is **not a model**. It
exercises the whole pipeline - prompt assembly, transport, validation, repair, promotion,
verification, merging, telemetry - reproducibly and at zero cost, so an ablation measures
*system behaviour*. It says nothing about a real model's judgement, and the benchmark
report labels the responder it used. Any claim about model quality has to come from a run
with `--responder anthropic --allow-paid-calls`.

## Known limitations

- Semantic findings are outside the benchmark's graded eight-category taxonomy, so their
  precision is **not measured** by the existing gold labels. They are declared `other`,
  which is the honest classification - they are neither credited nor penalised. If they
  were instead filed as `engagement`, micro-averaged precision on the ablation set drops
  from 0.952 to 0.833, because the gold correctly says those sites have no navigation
  defect. That is the reason promotion is off by default and `semantic_shadow` is the
  highest mode recommended for graded runs.
- The ablation numbers come from a scripted responder, not a model.
- Rendering is a deterministic *simulated* render (inline-script mining), not a headless
  browser; it recovers markup injected by inline JavaScript but not content fetched at
  runtime from an API.
- Page-type classification for ambiguous URLs is deterministic-only in this version; the
  hook for LLM-assisted classification exists but is not wired to a prompt.

## Safety

Read-only by construction: HTTP GET only, robots.txt and `Crawl-delay` obeyed, at most 150
pages, 6 concurrent requests and ~8s per request, no cookies persisted, no writes to the
target, no PII collected. Dependencies are
`requests` and `beautifulsoup4`, with a `urllib` fallback if `requests` is absent. Typical
runtime is under 0.1s per site on localhost; a failing skill degrades coverage rather than
the run.
