
---

## 14. Real-web benchmark

The synthetic suite and the held-out set both use HTML we wrote. `eval/REAL-WEB.md`
documents the third layer: 104 curated hard-to-cite real sites, captured once into a
replayable corpus and scored without gold labels by an independent adjudicator that is
allowed to abstain.

```bash
python eval/runners/capture_corpus.py       # fetch once, politely, into eval/sites/real/corpus
python eval/runners/run_real_suite.py       # replay locally, score every agent
python eval/runners/validate_adjudicator.py # measure the scorer against labelled gold (0.990 agreement)
```

Requires ordinary outbound internet access, so it runs on a normal workstation rather than
inside a sandboxed environment.
