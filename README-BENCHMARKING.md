# Agent Benchmarking Infrastructure

This README explains how to build a repeatable benchmarking system for the Adobe University Hackathon Round 3 marketplace.

The goal is simple:

> Given the same website and the same task, run multiple agent/skill versions and measure which one is actually better.

Do **not** decide based only on which report looks more detailed. The benchmark should measure detection quality, false positives, evidence quality, suggested-action quality, severity accuracy, proactive recommendations, output validity, and runtime.

The benchmark should be useful both for comparing completely different agents and for comparing successive versions of the same agent.

---

## 1. What the benchmark should test

The Round 3 handout says the marketplace is evaluated on:

- detection accuracy across AI discoverability and on-site engagement
- evidence-backed findings with few false positives
- quality and mechanism-soundness of suggested actions
- clear structured output with severity and prioritization
- genuine multi-skill composition, when multiple skills are used
- useful recommendations beyond explicitly detected defects
- generalization to unseen websites
- engineering hygiene, safe read-only behavior, and runtime constraints

The benchmark therefore has two layers:

```text
Layer A: Specialist benchmark
--------------------------------
Test crawl/render skill
Test content/facts skill
Test freshness/entity skill
Test engagement skill

Layer B: End-to-end marketplace benchmark
-----------------------------------------
URL
  ↓
Entrypoint/orchestrator
  ↓
Multiple skills
  ↓
Deduplication + normalization
  ↓
Final audit report
  ↓
Scoring
```

Always keep both layers. A specialist may perform very well while the final orchestrator performs poorly because of duplicated findings, bad severity merging, or lost evidence.

---

## 2. Create the benchmark directory

Add an `eval/` directory at the root of the repository.

Recommended structure:

```text
geo-agent-skill-main/
│
├── anti/
├── web-intelligence-crawler/
│
├── eval/
│   ├── README.md
│   ├── sites/
│   │   ├── real/
│   │   │   └── sites.json
│   │   └── synthetic/
│   │       ├── site-001/
│   │       ├── site-002/
│   │       └── ...
│   │
│   ├── gold/
│   │   ├── site-001.json
│   │   ├── site-002.json
│   │   └── ...
│   │
│   ├── agents/
│   │   ├── agent-v1.json
│   │   ├── agent-v2.json
│   │   └── agent-v3.json
│   │
│   ├── runners/
│   │   ├── run_agent.py
│   │   └── run_suite.py
│   │
│   ├── scoring/
│   │   ├── normalize.py
│   │   ├── detection.py
│   │   ├── evidence.py
│   │   ├── actions.py
│   │   └── score.py
│   │
│   └── results/
│       ├── raw/
│       ├── normalized/
│       └── reports/
│
└── ...
```

Keep evaluation code separate from the actual submission skills. `eval/` is development/testing infrastructure and does not have to be included in the final marketplace ZIP unless you deliberately want to include it.

---

## 3. Define a common agent interface

Every agent version must receive the same input and produce the same high-level output.

At minimum, use:

```text
Input:
    website URL/domain

Output:
    JSON audit report
```

For example:

```bash
python eval/runners/run_agent.py \
  --agent agent-v2 \
  --url https://example.com
```

The runner must not give one agent extra information that another agent does not receive.

For an end-to-end test, the input should be only the URL/domain and the normal marketplace task.

---

## 4. Build the test-site dataset

Use a mixture of real websites and controlled synthetic websites.

A useful first version is:

| Dataset | Purpose | Suggested count |
|---|---|---:|
| Real healthy sites | Measure false positives | 8–10 |
| Real weak/mixed sites | Test realistic detection | 10–15 |
| Synthetic controlled sites | Known ground truth | 10–15 |
| Edge cases | Stress generalization | 5–10 |

Do not rely only on websites that you used while developing the agent. Those can make the benchmark misleading because the agent may have been implicitly tuned around them.

The final benchmark should contain sites the agent has not seen during development.

---

## 5. The most important part: create synthetic fault-injection sites

Real websites are useful, but their exact ground truth is difficult to know.

Synthetic sites solve this problem.

Start with one small, healthy website and create controlled variations by introducing one known defect at a time.

Example:

```text
site-001-clean
site-002-no-schema
site-003-js-only-facts
site-004-image-only-facts
site-005-stale-facts
site-006-entity-conflict
site-007-poor-navigation
site-008-mixed-faults
```

The purpose is not to make realistic production websites. The purpose is to know exactly which problems exist.

### Recommended fault categories

Create test cases for the mechanisms relevant to the challenge, such as:

```text
1. Crawlability problem
2. JavaScript/rendering gap
3. Important facts available only in non-text content
4. Missing/invalid structured data
5. Stale information
6. Weak external corroboration
7. Entity ambiguity
8. Poor on-site orientation/context retention
9. Weak internal navigation/context
10. Healthy page with no major problem
11. Mixed site with several simultaneous problems
```

Each synthetic site should have a clear expected result.

---

## 6. Create the gold-standard file for every site

For every test site, create one JSON file in `eval/gold/`.

Example:

```json
{
  "site_id": "site-002",
  "url": "http://localhost:8002",
  "expected_findings": [
    {
      "category": "structured-data",
      "description": "Important organization/product information lacks appropriate machine-readable structured data",
      "severity": "medium"
    },
    {
      "category": "rendering",
      "description": "Important content is unavailable in the initial machine-readable HTML",
      "severity": "high"
    }
  ],
  "expected_proactive": [
    "Improve explicit, machine-readable representation of important brand facts"
  ]
}
```

Do not require the agent to reproduce the exact wording.

The evaluator should compare findings semantically by category and mechanism.

For example:

```text
Gold:
    important content unavailable in raw HTML

Agent:
    critical product facts only appear after client-side rendering

Result:
    MATCH
```

But:

```text
Gold:
    nothing about favicon

Agent:
    missing favicon is hurting AI discoverability

Result:
    FALSE POSITIVE
```

---

## 7. Define finding categories

Create a fixed vocabulary for scoring.

For example:

```text
crawlability
rendering
content-extraction
structured-data
fact-clarity
freshness
corroboration
entity-identity
internal-orientation
engagement
other
```

Agents may use different titles, but the normalizer should map them to the benchmark categories.

Example mapping:

```text
"JS-only content"            -> rendering
"client rendered facts"      -> rendering
"no schema.org"              -> structured-data
"missing Organization JSON"  -> structured-data
"stale pricing"              -> freshness
"conflicting brand names"    -> entity-identity
```

This makes scoring consistent across agent versions.

---

## 8. Normalize agent output before scoring

The benchmark should never score raw text directly.

Convert every result into a canonical structure such as:

```json
{
  "site": "site-002",
  "agent": "agent-v3",
  "findings": [
    {
      "id": "F-001",
      "category": "rendering",
      "title": "Important facts require client-side rendering",
      "severity": "high",
      "evidence": "Raw HTML did not contain the product facts found after rendering.",
      "suggested_action": {
        "summary": "Expose critical product facts directly in machine-readable HTML.",
        "priority": "high"
      }
    }
  ]
}
```

The normalizer should:

1. Parse JSON.
2. Reject malformed results.
3. Convert severity values to `critical/high/medium/low`.
4. Map findings to benchmark categories.
5. Deduplicate near-identical findings.
6. Preserve the original evidence and action text.
7. Record parsing/runtime errors separately.

Never silently discard malformed output. Count it as an output-quality failure.

---

## 9. Match agent findings to gold findings

For every gold finding, determine whether the agent found the same underlying problem.

Use three outcomes:

```text
TP = true positive
FP = false positive
FN = false negative
```

Example:

```text
Gold findings:
    rendering
    structured-data
    freshness

Agent findings:
    rendering
    structured-data
    favicon

TP = 2
FP = 1
FN = 1
```

The benchmark should prefer mechanism/category matching over exact string matching.

For a first implementation, use deterministic category matching plus a small manually curated alias table. Do not make the benchmark depend entirely on another LLM to decide whether the agent was correct.

---

## 10. Calculate detection precision, recall, and F1

For each agent:

```text
precision = TP / (TP + FP)

recall = TP / (TP + FN)

F1 = 2 * precision * recall / (precision + recall)
```

Interpretation:

```text
Precision answers:
    "When the agent reports a problem, how often is it actually real?"

Recall answers:
    "How many of the real problems did the agent find?"
```

This is important because a noisy agent can achieve high recall simply by reporting everything.

You want both high recall and high precision.

Also compute these numbers per category:

```text
crawlability F1
rendering F1
structured-data F1
freshness F1
entity F1
engagement F1
...
```

This tells you exactly where an agent is weak.

---

## 11. Score evidence quality separately

A finding should not receive full credit simply because its category is correct.

Score evidence on a 0–3 scale:

```text
0 = no evidence / unsupported claim
1 = vague evidence
2 = specific but limited evidence
3 = directly verifiable evidence with concrete details
```

Examples:

### Score 0

```text
"The site is difficult for AI systems to understand."
```

### Score 1

```text
"Some important information may not be accessible to crawlers."
```

### Score 2

```text
"The product page does not contain Product JSON-LD."
```

### Score 3

```text
"GET /product/widget returned 200, but the initial HTML contains no Product JSON-LD and the product attributes appear only after client-side rendering."
```

For every true-positive finding, record the evidence score.

Then calculate:

```text
average_evidence_score
```

---

## 12. Score severity accuracy

The agent should not call every problem critical.

For controlled test cases, put the expected severity in the gold file.

Use:

```text
critical = 4
high     = 3
medium   = 2
low      = 1
```

A simple scoring method is:

```text
exact severity       = full credit
one level away       = partial credit
2+ levels away      = low/no credit
```

Example:

```text
Gold: HIGH
Agent: HIGH       -> 1.0
Agent: MEDIUM     -> 0.5
Agent: LOW        -> 0.0
Agent: CRITICAL   -> 0.5
```

This prevents severity inflation from improving an agent's score.

---

## 13. Score suggested actions

For every true-positive finding, score the proposed fix on a 0–4 scale:

```text
0 = incorrect or unrelated
1 = vaguely relevant
2 = correct but generic
3 = technically correct and specific
4 = technically correct + specific + prioritized + directly actionable
```

Example:

```text
Problem:
Important facts are only available in an image.
```

Weak action:

```text
Improve SEO.
```

Score: 1

Strong action:

```text
Reproduce the critical facts as visible machine-readable HTML text and expose equivalent structured data where appropriate; keep the image as supporting content.
```

Score: 4

Track:

```text
average_action_score
```

---

## 14. Test proactive recommendations

Some useful improvements may not correspond to an explicit detected defect.

For clean or mostly healthy sites, check whether the agent still recommends genuinely useful improvements.

For every proactive recommendation, score:

```text
relevance
mechanism-soundness
specificity
potential impact
non-obviousness
```

Use a simple 0–4 scale for the first benchmark version.

Do not reward generic advice such as:

```text
"Improve SEO."
"Create more content."
"Use social media."
```

Prefer recommendations that are directly connected to how an assistant can discover, extract, trust, cite, or correctly represent the brand, or how an arriving visitor can understand and continue engaging with the site.

---

## 15. Test clean websites aggressively for false positives

A very important benchmark subset is:

```text
healthy website
        ↓
agent
        ↓
very few findings
```

A sophisticated benchmark should contain some intentionally healthy sites.

Otherwise an agent can get a deceptively high recall score by flagging many things.

For clean sites, measure:

```text
false findings per site
false-positive rate
```

Track this separately from recall.

---

## 16. Add mutation testing

Once the basic benchmark works, create a stronger test.

Start from one healthy site and mutate one property at a time.

Example:

```text
Version A
healthy

Version B
remove structured data

Version C
hide important facts behind JS

Version D
move important facts into an image

Version E
add stale claims

Version F
introduce conflicting entity names

Version G
add poor navigation/context
```

The desired behavior is:

```text
healthy
    -> few/no findings

+ schema defect
    -> structured-data finding appears

+ rendering defect
    -> rendering finding appears

+ stale fact
    -> freshness finding appears
```

This is useful because it checks whether the agent responds to the actual mechanism instead of merely reacting to the website's overall appearance.

---

## 17. Test mixed faults

Do not stop at one-fault sites.

Create sites containing multiple simultaneous problems:

```text
site-008:
    rendering problem
    stale facts
    entity ambiguity
    weak navigation
```

The agent should:

- find multiple independent problems
- keep the evidence attached to the correct problem
- avoid duplicating the same root cause
- assign reasonable severity
- prioritize the actions

Mixed-fault tests are especially useful for testing the orchestrator.

---

## 18. Compare individual skills first

Suppose you have:

```text
agent-v1
agent-v2
agent-v3
```

Run each one against exactly the same dataset.

Example command:

```bash
python eval/runners/run_suite.py --agent agent-v1
python eval/runners/run_suite.py --agent agent-v2
python eval/runners/run_suite.py --agent agent-v3
```

Store raw reports separately:

```text
results/raw/agent-v1/
results/raw/agent-v2/
results/raw/agent-v3/
```

Never overwrite old benchmark results. Version them.

---

## 19. Compare skill combinations

When your marketplace contains multiple skills, test combinations as well as individual skills.

For example:

```text
crawl only
content only
entity only
engagement only

crawl + content
crawl + entity
content + entity

crawl + content + entity
all skills + orchestrator
```

This is necessary because adding a skill can make the final system worse.

Possible failure modes include:

```text
duplicate findings
conflicting severity
conflicting recommendations
overlong report
lost evidence
slow runtime
```

The benchmark should expose these problems.

---

## 20. Add an end-to-end orchestration score

After specialist scoring, score the final report as a complete artifact.

Measure:

```text
1. Required fields present
2. Valid JSON
3. Correct finding count
4. Correct severity counts
5. Evidence preserved
6. Suggested actions preserved
7. Duplicate findings removed
8. Findings correctly prioritized
9. Proactive recommendations present when useful
```

The final report should remain compatible with the required marketplace report shape.

At minimum, preserve:

```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": {
    "total_findings": 6,
    "critical": 1,
    "high": 2,
    "medium": 3
  },
  "findings": [
    {
      "id": "F-001",
      "title": "...",
      "severity": "high",
      "evidence": "...",
      "suggested_action": {
        "summary": "...",
        "priority": "high"
      }
    }
  ]
}
```

---

## 21. Measure runtime

The marketplace has a runtime constraint, so the benchmark should record execution time for every site.

Record:

```text
start time
end time
duration
exit code
```

Calculate:

```text
average runtime
median runtime
p95 runtime
max runtime
timeouts
```

Do not compare agents only by quality. A marginally better agent that consistently exceeds the runtime constraint is not a useful submission.

---

## 22. Track failures separately from false positives

A benchmark should distinguish:

```text
correctly detected problem
false positive
missed problem
malformed output
runtime failure
timeout
crash
```

For example:

```text
site-014
Agent v3
    TP = 4
    FP = 1
    FN = 2
    malformed = 0
    timeout = 0
```

This makes debugging much easier.

---

## 23. Create a weighted overall score

Use the benchmark to rank agent versions.

A practical development weighting is:

| Metric | Weight |
|---|---:|
| Detection F1 | 20% |
| False-positive performance | 15% |
| Evidence quality | 15% |
| Suggested-action quality | 20% |
| Severity accuracy | 10% |
| Proactive recommendations | 10% |
| Output/schema validity | 5% |
| Runtime/reliability | 5% |

These weights are **your internal benchmark**, not an official Adobe scoring formula.

Calculate an overall score such as:

```text
overall =
    0.20 * detection_score +
    0.15 * false_positive_score +
    0.15 * evidence_score +
    0.20 * action_score +
    0.10 * severity_score +
    0.10 * proactive_score +
    0.05 * output_score +
    0.05 * runtime_score
```

Keep the component scores visible. Never report only the final number.

---

## 24. Produce a comparison table

After every benchmark run, generate something like:

```text
Agent      Detection F1   Precision   Evidence   Actions   Severity   Runtime   Overall
------------------------------------------------------------------------------------------------
v1              0.71        0.86        2.1        2.7       0.76      2m31s      76.4
v2              0.80        0.89        2.5        3.1       0.83      2m42s      84.7
v3              0.84        0.94        2.8        3.5       0.91      2m18s      90.8
```

Then inspect category-level performance:

```text
Agent v3

crawlability        0.92 F1
rendering           0.95 F1
structured-data     0.87 F1
freshness           0.70 F1
entity              0.81 F1
engagement          0.76 F1
```

This tells you what to improve next.

---

## 25. Add regression testing

Once a version becomes your best version, freeze its results.

For example:

```text
benchmarks/
    baseline-v3.json
```

Every new version must be compared against it.

Example rule:

```text
Do not accept a new version if:
    overall score decreases > 2 points
    OR detection F1 decreases > 0.03
    OR false positives increase significantly
```

You can tune the thresholds later.

This prevents the common situation where a new skill improves one category but silently damages another.

---

## 26. Test generalization separately

Keep some websites completely hidden from routine development.

For example:

```text
training/development sites
30 sites

hidden validation sites
10 sites

final holdout sites
10 sites
```

Only use the final holdout at the end.

The important rule is:

> Never modify the agent specifically to handle a hidden site's quirks after seeing its benchmark result.

Otherwise the benchmark stops measuring generalization.

---

## 27. Recommended first implementation order

Do not build the complete benchmark in one pass.

### Phase 1 — minimum viable evaluator

Build:

```text
10 synthetic sites
5 real sites
one gold JSON per site
one runner
one normalizer
precision/recall/F1
runtime measurement
comparison CSV/JSON
```

### Phase 2 — quality scoring

Add:

```text
evidence scoring
action scoring
severity scoring
false-positive analysis
```

### Phase 3 — marketplace testing

Add:

```text
multiple skills
multiple skill combinations
orchestrator scoring
deduplication checks
```

### Phase 4 — robustness

Add:

```text
mutation tests
mixed-fault sites
edge cases
hidden holdout set
regression testing
```

---

## 28. Suggested files to implement first

Start with these five files:

```text
eval/runners/run_agent.py
eval/runners/run_suite.py
eval/scoring/normalize.py
eval/scoring/detection.py
eval/scoring/score.py
```

### `run_agent.py`

Responsibilities:

```text
- accept agent version + URL
- execute the selected agent
- enforce timeout
- capture stdout/stderr
- store raw JSON
- record runtime and exit status
```

### `run_suite.py`

Responsibilities:

```text
- load all benchmark sites
- run the selected agent on every site
- call normalization
- call scoring
- write aggregate results
```

### `normalize.py`

Responsibilities:

```text
- validate output
- normalize severity
- map categories
- deduplicate findings
- preserve evidence/actions
```

### `detection.py`

Responsibilities:

```text
- compare normalized findings to gold findings
- calculate TP/FP/FN
- calculate precision/recall/F1
- calculate category scores
```

### `score.py`

Responsibilities:

```text
- combine detection
- evidence
- actions
- severity
- proactive
- output validity
- runtime
- produce final leaderboard
```

---

## 29. Example final command flow

After everything is implemented, the workflow should look approximately like:

```bash
# Run agent v3 on the full benchmark
python eval/runners/run_suite.py --agent agent-v3

# Run a new version
python eval/runners/run_suite.py --agent agent-v4

# Compare versions
python eval/scoring/score.py \
  --results eval/results/agent-v3 \
  --results eval/results/agent-v4
```

Output:

```text
============================================================
AGENT BENCHMARK
============================================================

Agent v3
--------
Detection F1:        0.84
False positives:    0.08
Evidence:            2.8 / 3.0
Actions:             3.5 / 4.0
Severity:            0.91
Proactive:           0.82
Runtime:             2m 18s
Overall:             90.8 / 100

Agent v4
--------
Detection F1:        0.87
False positives:    0.13
Evidence:            2.9 / 3.0
Actions:             3.6 / 4.0
Severity:            0.92
Proactive:           0.84
Runtime:             2m 51s
Overall:             89.9 / 100

WINNER: Agent v3
Reason: v4 improved recall but introduced too many false positives and increased runtime.
============================================================
```

This gives you a defensible answer to "which agent is better?"

---

## 30. The benchmark should guide development, not just rank agents

After each run, look at the failures.

For example:

```text
v4 vs v3

Improved:
    rendering +12%
    actions +7%

Regressed:
    freshness -8%
    false positives +9%
```

Then change only the part of the system responsible for the regression.

After making the change, rerun the entire benchmark.

Do not evaluate only the sites related to the change. That makes regression testing unreliable.

---

## 31. Recommended benchmark philosophy

Use these rules throughout development:

```text
1. Same input for every agent.
2. Never score report length.
3. Reward evidence, not confident wording.
4. Penalize false positives heavily.
5. Test clean sites.
6. Test known synthetic faults.
7. Test multiple simultaneous faults.
8. Test unseen real sites.
9. Score specialists and the final orchestrator.
10. Keep raw results for every version.
11. Never overwrite benchmark history.
12. Use regression tests before accepting a new agent version.
```

The most important principle is:

> A better agent is not the one that finds the most problems. It is the one that finds the most real problems, supports them with strong evidence, avoids false positives, and gives technically correct actions that generalize to websites it has never seen.

---

## 32. Final target architecture

When the infrastructure is complete, the repository should conceptually work like this:

```text
                         ┌─────────────────────┐
                         │ Benchmark Test Set  │
                         │ real + synthetic    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │     Agent Runner    │
                         └──────────┬──────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 ▼                  ▼                  ▼
             Agent v1            Agent v2            Agent v3
                 │                  │                  │
                 └──────────────────┼──────────────────┘
                                    ▼
                         ┌─────────────────────┐
                         │     Normalizer      │
                         └──────────┬──────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  ▼                 ▼                 ▼
             Detection          Evidence           Actions
             TP/FP/FN            quality            quality
                  │                 │                 │
                  └─────────────────┼─────────────────┘
                                    ▼
                         ┌─────────────────────┐
                         │   Overall Scoring   │
                         └──────────┬──────────┘
                                    ▼
                         ┌─────────────────────┐
                         │ Leaderboard/Report  │
                         └─────────────────────┘
```

Once this exists, adding a new agent becomes cheap: add the agent configuration, run the suite, and compare the metrics.

That is the point of the infrastructure — it turns agent development from subjective report inspection into measurable experimentation.

---

## 13. Scoring v2 and the held-out split (added with Agent F)

Two limitations of the original harness were fixed in `eval/runners/run_suite.py`. Both
apply identically to every agent, so the leaderboard stays apples-to-apples; the
`Legacy` column reproduces the original formula on the original ten sites.

1. **Per-true-positive metrics are averaged over the sites that have true positives.**
   Evidence, action and severity quality describe matched findings. Healthy sites have no
   gold findings, so they previously injected a hard 0 (evidence, actions) and 0.5
   (severity) into those averages, capping any perfect agent at 80.5 overall. They are now
   excluded from those three averages only; they still count fully towards detection,
   false-positive and runtime scores, where they are exactly the sites that matter.

2. **`proactive_score` and `generalization_score` are measured, not assumed.**
   - Proactive: graded by `eval/scoring/proactive.py` against each gold's
     `expected_proactive` list (already present in every gold file, previously unused).
     Standalone recommendations earn full credit, advice inside a detected defect's
     suggested action earns half. Advice is read from any recommendation-shaped field
     (`recommendations`, `suggestions`, `next_steps`, ...) and from low-severity findings.
   - Generalization: detection F1 over the sites whose gold carries `"holdout": true`
     (sites 011-016). Headline detection metrics are computed on the development sites
     (001-010) so the two numbers are independent.

**Held-out sites (011-016)** were written after the agents and cover multi-page crawling,
an SPA shell, invalid JSON-LD, entity drift that only appears on sub-pages, image-only
facts on a text-rich page, and a `noindex` block. Add new sites with `"holdout": true` in
their gold file and they join the generalization set automatically.

Keep the split honest: once an agent has been debugged against a hold-out site, that site
has become a development site. Retire it into the dev set and write new ones.
