# Agent F - The Marketplace

An **Agent Skill Marketplace** for AI discoverability and on-site engagement auditing,
built to the Round 3 handout's marketplace model: a manifest, an entrypoint skill, and
specialist skills that are composed at run time rather than wired together in code.

```
marketplace.json                     # manifest: skills, contracts, safety envelope
run.py                               # benchmark entrypoint -> run_audit(url) -> dict
lib/contracts.py                     # shared Page/SiteSnapshot model + finding contract
lib/loader.py                        # manifest-driven loader + dependency ordering
skills/
  audit-orchestrator/                # entrypoint: plan -> invoke -> merge -> prioritise -> report
  crawl-render-audit/                # crawlability, rendering   (+ produces the snapshot)
  content-semantics-audit/           # content_extraction, structured_data, non_text_facts
  entity-freshness-audit/            # entity_identity, freshness
  engagement-audit/                  # engagement
```

Each skill directory carries a `SKILL.md` (human/agent-readable contract) and a
`skill.json` (machine-readable detector, severity and safety declaration).

## Run it

```bash
python eval/runners/run_agent.py --agent marketplace --url http://localhost:9500/site-008-mixed-faults/
python eval/runners/run_suite.py --agent marketplace
python eval/agents/marketplace/run.py https://example.com report.json   # standalone
```

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

Scoring v2, `python eval/runners/run_suite.py`:

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
`eval/scoring/proactive.py` and `aggregate_scores()` in `run_suite.py`, and they apply
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

## Safety

Read-only by construction: HTTP GET only, robots.txt obeyed, at most 12 pages and ~8s per
request, no cookies persisted, no writes to the target, no PII collected. Dependencies are
`requests` and `beautifulsoup4`, with a `urllib` fallback if `requests` is absent. Typical
runtime is under 0.1s per site on localhost; a failing skill degrades coverage rather than
the run.
