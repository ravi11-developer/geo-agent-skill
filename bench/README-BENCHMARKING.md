# `bench/` — how the agents are measured

`bench/` is the evaluation half of the repo. Nothing in here ships inside a
submission zip: `agents/*/tools/validate_package.py` size-checks the marketplace
root only, so pytest, the judge model and the 10,000-site URL lists stay out of
the package.

An agent is anything under `agents/` with an `agent.json`. `registry.py`
discovers them by scanning, runs each one in its **own subprocess** as
`python run.py <url> <out.json>` — exactly how a judge invokes it — and never
raises: a crash becomes an `error` string on the result.

```
bench/
├── gold/          18 hand-labelled expectations, one JSON per synthetic site
├── sites/
│   ├── synthetic/ 18 fault-injected fixtures served from localhost
│   └── real/      the real-web corpus: site lists + captured bytes
├── runners/       the executables — sweeps, suites, capture, replay server
├── scoring/       pure functions that turn a report into numbers
├── tests/         pytest suite (hermetic by default)
├── tools/         llm_judge.py — opt-in hallucination grading
├── results/       every run ever, timestamped, never overwritten
└── registry.py    agent discovery + subprocess invocation
```

## The three layers of evidence

| layer | input | ground truth | what it proves |
|---|---|---|---|
| **synthetic** | 18 fixtures we wrote | hand-written gold labels | the agent detects planted faults and stays quiet on healthy sites |
| **real-web** | 88–90 captured real sites | none — a mechanical adjudicator that may abstain | the agent survives real HTML: tag soup, cookie walls, bad encodings |
| **unit** | JSON dicts | assertions | a scoring bug cannot silently move the leaderboard |

Each answers a question the one above it cannot. The synthetic suite is the only
layer with real ground truth, and it is also the layer most at risk of being
fitted to — which is why the real-web layer exists.

---

## `gold/` — the labels

One file per synthetic site. This is the only ground truth in the repo.

```json
{
  "site_id": "site-008-mixed-faults",
  "url": "http://localhost:9500/site-008-mixed-faults/",
  "description": "Multiple simultaneous faults: no schema, entity conflict, ...",
  "expected_findings":     [{ "category": "...", "severity": "...", "keywords": [...] }],
  "expected_non_findings": [],
  "expected_proactive":    [],
  "max_acceptable_findings": 10
}
```

`expected_non_findings` is the important half: it is how a site says *"flagging
this would be wrong"*, and it is what turns a healthy-site fixture into a real
precision test rather than a free pass. `expected_proactive` grades the
recommendations, so an agent cannot win by staying silent.

## `sites/synthetic/` — the fixtures

18 sites, `site-001` … `site-018`, each a directory of plain HTML served at
`http://localhost:9500/<site-id>/`. They range from healthy (`site-001`,
`site-009-weird-but-healthy`, `site-011`, `site-018`) through single-fault
(`site-003-js-only-facts`, `site-013-invalid-schema`) to `site-008-mixed-faults`
with six simultaneous defects.

Fixtures that need an origin-level document (`robots.txt`, `sitemap.xml`) declare
`needs_own_origin` and `runners/serve_synthetic.py` gives them a **port of their
own** — otherwise every fixture would share one `/robots.txt` and no site could
carry a crawler policy of its own. This is why `site-016-noindex-block` and
`site-017-robots-blocks-ai` cannot be tested by pointing a plain static server at
the directory.

## `sites/real/` — the real-web corpus

| file | what it is |
|---|---|
| `sites.json` | 104 curated hard-to-cite domains across twelve verticals |
| `corpus/` | 90 captured sites — the actual stored bytes agents replay |
| `categories/` | per-vertical notes on why citation is weak there |
| `sites_2600.json`, `sites_10000.json`, `urls_2600.txt` | bulk lists for the scale runners |

Captured once, replayed many times — for politeness (one fetch per page for the
whole benchmark, not one per agent), fairness (byte-identical input for every
agent) and reproducibility. See [REAL-WEB.md](REAL-WEB.md) for the full rationale
and the findings from the first real run.

## `runners/` — the executables

| runner | what it does |
|---|---|
| `run_suite.py` | **the main one.** Every agent × all 18 synthetic sites → leaderboard |
| `run_agent.py` | one agent, one URL — for eyeballing a single report |
| `serve_synthetic.py` | serves the fixtures, including own-origin ports |
| `capture_corpus.py` | fetch real sites once into `sites/real/corpus` |
| `run_real_suite.py` | replay the corpus, score every agent without gold labels |
| `validate_adjudicator.py` | measures the *scorer's* accuracy against labelled gold |
| `ksweep.py` | crawl-budget sweep: how many findings does page N buy? |
| `saturation_sweep.py` | how patient should the crawl's early-stop be? |
| `run_2600_benchmark.py`, `run_10000_benchmark.py` | the same pipeline at scale |

`ksweep.py` and `saturation_sweep.py` are why the crawl profile in
`lib/llm/config.py` has the numbers it has. They exist so "why 150 pages?" is
answered with a measurement instead of a round number.

## `scoring/` — report in, numbers out

Pure functions over JSON dicts, unit-tested directly in `tests/test_scoring_units.py`.

| module | responsibility |
|---|---|
| `normalize.py` | maps an agent's own category names onto the benchmark's |
| `detection.py` | TP / FP / FN → precision, recall, F1, overall and per category |
| `evidence.py` | evidence, suggested-action and severity quality |
| `proactive.py` | grades recommendations against `expected_proactive` |
| `adjudicate.py` | gold-free labelling for real sites; **may abstain** |
| `score.py` | the weighted overall score |

Weights in `score.py::DEFAULT_WEIGHTS`:

| metric | weight | | metric | weight |
|---|---|---|---|---|
| detection F1 | 0.20 | | severity | 0.10 |
| actions | 0.20 | | proactive | 0.10 |
| evidence | 0.15 | | schema | 0.05 |
| false positive | 0.10 | | runtime | 0.05 |
| | | | generalization | 0.05 |

Detection is only 20%. Being right is table stakes; *proving* it (evidence) and
*acting* on it (actions) together outweigh it 35 to 20 — which is the rubric's
point, and the reason an agent cannot win this benchmark by detecting more.

`adjudicate.py`'s three-valued output (`yes` / `no` / **`abstain`**) is the
design decision that makes gold-free scoring defensible: a scorer forced to guess
on every site produces confident nonsense.

## `tests/` — pytest

```bash
pytest                  # hermetic: localhost fixtures only, no third-party network
pytest --runslow        # adds the tests that fetch live sites
pytest -m llm           # needs ANTHROPIC_API_KEY, otherwise auto-skipped
```

Default run: **41 passed, 13 skipped** in ~3.5 min. `slow` tests fetch live
third-party sites and are deselected unless `--runslow` is passed — otherwise a
plain `pytest` on a laptop with no network fails for reasons that say nothing
about the agents.

| file | what it asserts |
|---|---|
| `test_action_success.py` | stress suite passes, package is valid, reports match the schema |
| `test_routing.py` | `marketplace.json` wiring matches what the orchestrator actually runs |
| `test_scoring_units.py` | the 20 scoring-function unit tests |
| `test_telemetry_efficiency.py` | crawl profiles reach more templates; runs finish inside budget |
| `test_hallucination_judge.py` | `llm`-marked: a judge checks evidence against the page text |

## `tools/` and `results/`

`tools/llm_judge.py` grades hallucination *after the fact* and is never imported
by a shipped agent — both marketplaces guarantee "no API key required" for the
product itself. The deterministic half of that check (`lib/verification.py`)
already runs on every audit for free.

`results/` accumulates `benchmark_*.json` + `leaderboard_*.csv` per run,
`realweb_*` for the real-web layer, and `ksweep_*` / `satsweep_*` for the crawl
sweeps. Nothing is overwritten, so any number in a README can be traced to the
run that produced it.

`realweb_review_sample_*.csv` ships with an empty `human_verdict` column holding
every finding the adjudicator called `no` or `abstain`. Hand-label a few dozen
rows and you have a human-validated precision estimate — do this before quoting
real-web numbers to judges.

---

## Testing an agent against your own sites

Three ways, cheapest first.

**One live site, nothing captured:**

```bash
./geo run B-coverage https://your-site.com -o report.json
```

**A list of your own sites, captured once then replayed** — the honest way to
compare agents, since both see identical bytes:

```bash
printf '%s\n' https://site-a.com https://site-b.com > my_urls.txt
python bench/runners/capture_corpus.py --urls my_urls.txt --out bench/sites/real/mine
python bench/runners/run_real_suite.py --corpus bench/sites/real/mine
```

**A new labelled fixture**, when you want it in the scored suite — this is the
only way to add ground truth:

1. `mkdir bench/sites/synthetic/site-019-my-fault/` and write the HTML.
2. Add `bench/gold/site-019-my-fault.json` with `expected_findings`,
   `expected_non_findings` and `expected_proactive`.
3. `./geo bench` — the runner discovers both by scanning.

A fixture with an empty `expected_findings` and a populated
`expected_non_findings` is a precision test, and those are the ones worth
writing: a suite of fault-injected sites measures recall and nothing else.

## Adding an agent

Drop a directory under `agents/` containing an `agent.json`. `registry.discover()`
scans for it; no code change anywhere in `bench/`.
