# GEO Agent Skill — two candidate audit marketplaces

Round 3 asks for **one** Agent Skill Marketplace. This repo holds **two**
complete, independently submittable candidates and the benchmark that decides
between them.

```
agents/A-precision/     marketplace root — zip this directly
agents/B-coverage/      marketplace root — zip this directly
bench/                  gold sites, scoring, runners, agent registry
geo                     CLI
```

## The two candidates

Both are complete marketplaces: `marketplace.json`, one entrypoint skill, a
compliant `SKILL.md` per skill, read-only, no API key, sub-second on a typical
site.

|  | **A-precision** | **B-coverage** |
|---|---|---|
| Skills | 5 | 7 |
| Model layer | **absent from the package** | present, **off by default** |
| Off-site discoverability | no | yes (recommendations only) |
| Optimises | precision, reproducibility | recall, off-site coverage, proactive quality |

**A** is deterministic *by construction*: `lib/llm/` contains no provider, no
prompts and no API surface, so no environment variable can switch a model on.
**B** adds the half of the handout A does not cover — why a brand is not *found
or cited at all* — plus an opt-in semantic layer that ships disabled.

## Current standing — 18 gold sites, 9 weighted metrics

| Agent | F1 | Prec | Rec | FP | Evidence | Actions | Severity | Proactive | Generalization | **Overall** |
|---|---|---|---|---|---|---|---|---|---|---|
| **B-coverage** | 0.966 | 0.933 | 1.000 | 1.000 | 1.000 | 0.981 | 1.000 | **0.949** | 1.000 | **98.4** |
| A-precision | 0.966 | 0.933 | 1.000 | 1.000 | 1.000 | 0.981 | 1.000 | 0.932 | 1.000 | 98.2 |

The two are identical on detection; B wins on proactive suggestion quality alone.
Reproduce with `./geo bench`.

**Read that gap honestly.** 0.2 points is one recommendation on one site. Both
agents miss the same single false positive (`structured_data` on `site-007`),
and generalization is measured on sites written by the same hand that wrote the
agents — the real unseen test is the judges' sites. B is ahead because it covers
a requirement A does not, not because it detects better.

## Usage

```bash
./geo list                                   # what the agents are and how they differ
./geo run B-coverage https://example.com     # run one against a real site
./geo bench                                  # score every agent on the gold sites
./geo package B-coverage                     # build the submission zip (checks the 50 MB limit)
```

Adding a third candidate is a filesystem operation: drop a directory under
`agents/` containing an `agent.json`, and the bench discovers it.

See [RUNNING.md](RUNNING.md) for running each agent standalone, prompting an AI
agent to invoke the `SKILL.md` directly instead of the CLI, and inspecting the
JSON findings a report produces.

## Requirements

Python 3.9+, `requests`, `beautifulsoup4`. No API key. No network access beyond
outbound HTTP GET to the site being audited.
