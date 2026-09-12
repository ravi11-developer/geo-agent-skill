# Hybrid LLM layer — what to copy into `C:\adobe\geo-agent-skill`

Everything in this archive is at **repo-relative paths**. Copy the contents over
your repo root and the layout lands where it belongs.

I could not reach your computer over the desktop bridge while finishing this, so
nothing has been written to your disk — this archive is the delivery.

## Before you copy

`git status` first, then copy. Every path below is either **new** or a
**backward-compatible edit**; nothing is deleted. The four edited files are:

| path | edit |
|---|---|
| `.gitignore` | anchored the `lib/` rule to the repo root, added LLM cache paths |
| `README-BENCHMARKING.md` | appended an "hybrid LLM layer and its ablation" appendix |
| `priyanshu/eval/agents/marketplace/lib/contracts.py` | added `Page.depth`, `SiteSnapshot.content_fingerprint()`, evidence + observation contracts (all additive) |
| `priyanshu/eval/agents/marketplace/marketplace.json` | version 1.1.0, one new skill, `features` block, semantic contracts |
| `priyanshu/eval/agents/marketplace/skills/audit-orchestrator/orchestrator.py` | LLM wiring, promotion, audit_health, flat severity counts |
| `priyanshu/eval/agents/marketplace/skills/crawl-render-audit/crawl_render.py` | depth tracking, opt-in budget, error events |

> **`.gitignore` matters.** The old unanchored `lib/` rule matched at every
> depth, so `priyanshu/eval/agents/marketplace/lib/` — the shared contracts, the
> manifest loader and the whole new LLM layer — was being ignored by git. Check
> `git check-ignore -v priyanshu/eval/agents/marketplace/lib/contracts.py`
> before and after; if those files were never committed, the fixed rule is what
> lets you commit them.

## After you copy

```powershell
cd C:\adobe\geo-agent-skill\priyanshu
python eval\tests\run_tests.py          # 190 tests, offline, no API key
python eval\runners\run_suite.py --agent marketplace
python eval\runners\run_ablation.py
```

`marketplace_submission/` and `marketplace_submission.zip` in this archive are
**generated**, not hand-maintained. Rebuild them any time with:

```powershell
python priyanshu\eval\agents\marketplace\tools\build_submission.py --zip
```

That single command derives the submission package from the live agent tree,
rewrites every entrypoint to the `scripts/` layout, runs the package validator
and checks the 50 MB limit — which is what stops the two trees drifting apart
again.

## The one thing to decide

The LLM layer ships **off**. Semantic findings are outside the benchmark's
graded eight-category taxonomy, so their precision is not measured by your gold
labels; promotion is therefore off by default and `semantic_shadow` is the
highest mode I would use for a graded run. Full reasoning is in
`priyanshu/eval/agents/marketplace/README.md` under "Known limitations".
