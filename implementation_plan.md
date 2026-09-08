# Agent Benchmarking Laboratory — Implementation Plan

## Architecture Analysis of Current Implementation

### Current Structure
The repository contains **two** implementations of a web crawler/auditor:

1. **`web-intelligence-crawler/`** — A general-purpose intelligence crawler that extracts page content (title, meta, headings, text) and outputs JSON. It does NOT produce findings, severity, or an audit report. It's a data-collection tool only.

2. **`anti/web-intelligence-crawler/`** — A full SEO audit system with three files:
   - [`crawler.py`](file:///c:/adobe/anti/web-intelligence-crawler/scripts/crawler.py) — BFS crawler that collects raw HTML, headers, redirect chains, sitemap, robots.txt
   - [`analyzers.py`](file:///c:/adobe/anti/web-intelligence-crawler/scripts/analyzers.py) — 50 deterministic checks across 10 categories producing structured findings
   - [`seo_audit.py`](file:///c:/adobe/anti/web-intelligence-crawler/scripts/seo_audit.py) — Orchestrator that runs crawl → analyze → deduplicate → report

### Critical Weakness Assessment

| Weakness | Impact |
|---|---|
| **Pure SEO focus** — all 50 checks are traditional SEO (titles, meta, canonical, hreflang). Zero checks for AI discoverability, entity clarity, fact freshness, corroboration, or engagement | Missing the core Adobe Round 3 criteria |
| **No rendering gap detection** — crawler uses only `requests` (no JS rendering). Cannot detect content that exists only after client-side JS | Misses entire class of findings |
| **No structured data semantic checks** — checks only for JSON-LD presence/parse errors, not whether the schema types/content are appropriate | Shallow detection |
| **No entity/brand analysis** — no check for conflicting names, ambiguous identity, missing organization schema | Misses entity-identity category |
| **No freshness analysis** — no date/timestamp checking, no stale-fact detection | Misses freshness category |
| **No corroboration analysis** — no external validation of claims | Misses trust/corroboration category |
| **No engagement/orientation analysis** — no check for navigation quality, user journey, context retention | Misses engagement category |
| **No content-in-non-text detection** — no check for facts locked in images only | Misses non-text-facts category |
| **Noisy false positives likely** — thin content <300 words, missing viewport, URL underscores are flagged as real problems but are irrelevant to AI discoverability | Will inflate FP rate |
| **Crawler cannot reach localhost/synthetic sites** — validation rejects private IPs, which blocks all synthetic test sites | Cannot run benchmark |
| **`lxml` parser dependency** — uses `'lxml'` parser throughout analyzers | May fail if lxml not installed |

> [!IMPORTANT]
> The current implementation is an **SEO auditor**, not an **AI discoverability auditor**. It will score poorly on the Adobe Round 3 rubric because it tests the wrong things. This is the #1 blind spot.

## Proposed Changes

### Benchmark Infrastructure (`eval/`)

#### [NEW] `eval/sites/synthetic/` — 10 synthetic test sites
Static HTML files served by a local HTTP server. Each site has exactly one known fault (or is healthy). Sites include:
- `site-001-healthy` — Clean baseline
- `site-002-no-schema` — JSON-LD removed
- `site-003-js-only-facts` — Key facts in noscript/JS-dependent div
- `site-004-image-only-facts` — Key info in `<img>` only, no text equivalent
- `site-005-entity-conflict` — Conflicting company names across pages
- `site-006-stale-facts` — Pricing/dates clearly outdated (2019 dates)
- `site-007-poor-navigation` — No nav, no internal links, orphan pages
- `site-008-mixed-faults` — Multiple simultaneous problems
- `site-009-technically-weird-healthy` — Unusual HTML but actually fine
- `site-010-seo-noise` — Minor SEO issues (trailing slashes, etc.) that should NOT be flagged as AI discoverability problems

#### [NEW] `eval/gold/` — Ground-truth files
One JSON file per synthetic site defining expected findings, expected categories, expected severity, and expected non-findings.

#### [NEW] `eval/mutations/` — Mutation testing framework
A healthy baseline site plus a mutation generator that systematically introduces single faults.

---

### Competing Agents (`eval/agents/`)

#### [NEW] Agent B — Deterministic Technical Auditor (`eval/agents/deterministic/`)
Focused on explicit, measurable checks relevant to AI discoverability:
- HTTP accessibility, robots.txt, sitemap
- Raw HTML vs rendered (simulate by checking noscript, JS framework detection)
- JSON-LD presence, type validation, content completeness
- Visible text extraction, metadata completeness
- Entity name consistency across pages
- Date/freshness signal detection
- Navigation structure analysis
- Link density and internal linking
- Content-to-noise ratio

#### [NEW] Agent C — Reasoning-First Intelligence Agent (`eval/agents/reasoning/`)
Crawls, builds a brand/entity model, then reasons about discoverability gaps:
- Extracts brand identity, products, claims
- Checks for ambiguity, staleness, missing corroboration
- Analyzes user journey quality
- Produces findings based on reasoning about what an AI assistant would struggle with

#### [NEW] Agent D — Multi-Specialist Marketplace (`eval/agents/specialist/`)
Six specialist skills + orchestrator:
1. Crawl/render auditor
2. Structured data auditor
3. Content/fact extraction auditor
4. Freshness/corroboration auditor
5. Entity/identity auditor
6. Engagement/orientation auditor
7. Orchestrator (merges, deduplicates, prioritizes)

#### [NEW] Agent E — Hybrid (`eval/agents/hybrid/`)
Built after evaluating the benchmark results. It combines the winning precision of the deterministic agent with the semantic capabilities of the reasoning agent:
- **Crawler Engine**: Strict, scope-bound BFS from the Deterministic agent to avoid out-of-scope false positives.
- **Precision Layer (Deterministic)**: Rigid checks for structured data, basic crawlability, and freshness (where reasoning models hallucinate).
- **Semantic Layer (Reasoning)**: Brand modeling, entity consistency, and non-text fact extraction to catch the complex semantic issues Deterministic missed (e.g. on `site-008`).
- **Evidence Formatting**: Adopts the deterministic agent's strict CSS selector/HTML snippet evidence formatting.

#### [NEW] Agent F — The Marketplace (`eval/agents/marketplace/`)
Built to strictly adhere to the Round 3 PDF handout's required format and to score higher than Deterministic/Hybrid. It decomposes the best hybrid logic into independent, agentskills.io-compliant skills:
- **`marketplace.json`**: Manifest declaring the entrypoint and bundled skills.
- **`skills/audit-orchestrator/`**: The entrypoint that invokes other skills and compiles the final JSON report.
- **`skills/crawl-render-audit/`**: Executes the deterministic crawler and checks `crawlability`.
- **`skills/content-semantics-audit/`**: Checks `content_extraction` and `non_text_facts` to ensure facts aren't trapped in images or JS.
- **`skills/entity-freshness-audit/`**: Checks `entity_identity` consistency and `freshness` of facts.
- **`skills/engagement-audit/`**: Checks `engagement` via navigation and site structure analysis.
**Key Improvement:** Perfectly isolating concerns to maximize the "Marketplace composition" rubric score, while fixing the semantic false positives from the Hybrid agent.

---

### Scoring Infrastructure (`eval/scoring/`)

#### [NEW] `eval/scoring/normalize.py`
Maps agent findings to benchmark categories, normalizes severity, deduplicates.

#### [NEW] `eval/scoring/detection.py`
TP/FP/FN calculation, precision, recall, F1, per-category breakdown.

#### [NEW] `eval/scoring/evidence.py`
Scores evidence quality 0-4 based on concrete criteria.

#### [NEW] `eval/scoring/actions.py`
Scores suggested action quality 0-4.

#### [NEW] `eval/scoring/severity.py`
Scores severity accuracy against gold standard.

#### [NEW] `eval/scoring/schema_validator.py`
Validates output JSON against required report schema.

#### [NEW] `eval/scoring/score.py`
Weighted overall score combining all dimensions.

---

### Runners (`eval/runners/`)

#### [NEW] `eval/runners/run_agent.py`
Runs a single agent against a single site, captures output + runtime.

#### [NEW] `eval/runners/run_suite.py`
Runs all agents against all sites, produces leaderboard.

#### [NEW] `eval/runners/serve_synthetic.py`
HTTP server that serves synthetic test sites.

---

### Baseline Wrapper (`eval/agents/baseline/`)

#### [NEW] `eval/agents/baseline/run.py`
Wraps the existing `anti/` implementation to work with the benchmark interface. Patches the localhost restriction for synthetic sites.

## Open Questions

> [!IMPORTANT]
> **Runtime constraint**: The handout mentions a runtime constraint but the exact limit isn't specified in the repository. I'll assume a 5-minute per-site limit. Should this be different?

> [!IMPORTANT]
> **Real website testing**: For the unseen holdout set, I'll use real public websites. This requires network access during benchmark runs. The synthetic-only tests can run offline. Is this acceptable?

> [!IMPORTANT]
> **LLM dependency for Agent C/D**: The reasoning-first and specialist agents need an LLM for semantic analysis. Since we're benchmarking the *architecture*, I'll implement these as deterministic Python that simulates the reasoning (rule-based heuristics + NLP), not requiring an actual LLM API call. This keeps the benchmark reproducible and fast. Is this acceptable, or should I use actual LLM calls?

## Verification Plan

### Automated Tests
1. Run all agents against all 10 synthetic sites
2. Verify detection scores against gold standard
3. Verify schema validity of all outputs
4. Run mutation tests and verify sensitivity
5. Compare leaderboard scores

### Manual Verification
- Inspect false positive/negative reports for each agent
- Review evidence quality samples
- Verify the leaderboard ranking makes intuitive sense

## Execution Order

I'll build this in phases, with each phase producing runnable output:

1. **Phase 1**: Synthetic sites + gold standards + HTTP server
2. **Phase 2**: Common agent interface + baseline wrapper
3. **Phase 3**: Agent B (deterministic)
4. **Phase 4**: Agent C (reasoning-first)
5. **Phase 5**: Agent D (specialist marketplace)
6. **Phase 6**: Scoring infrastructure (normalize → detect → score)
7. **Phase 7**: Runner + leaderboard
8. **Phase 8**: Run benchmark, analyze results
9. **Phase 9**: Agent E (hybrid based on results)
10. **Phase 10**: Agent F (The Marketplace)
11. **Phase 11**: Mutations + edge cases + regression
12. **Phase 12**: Final leaderboard + error analysis
