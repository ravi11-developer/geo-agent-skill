# Running the agents & reading the output

Two ways to run each agent: the CLI (fastest, no LLM involved) or handing the
skill to an AI agent the way a judge/user actually would. Both produce the same
JSON report — see [Reading the JSON findings](#reading-the-json-findings) below
for how to inspect it either way.

## 1. CLI — no LLM needed

This is the direct path: pure Python, deterministic, good for testing quickly.

```bash
# Agent A — precision build (5 skills, no model layer at all)
./geo run A-precision https://example.com -o report-a.json

# Agent B — coverage build (7 skills, off-site discoverability, semantic layer off)
./geo run B-coverage https://example.com -o report-b.json
```

Drop `-o report.json` to just print the summary to the terminal instead of saving it.

Each agent also runs standalone, without `geo`, exactly as a judge would invoke it:

```bash
python agents/A-precision/run.py https://example.com report-a.json
python agents/B-coverage/run.py https://example.com report-b.json
```

`./geo run` and `run.py` are the same call — `geo` just resolves the agent id
through `bench/registry.py` first.

## 2. Prompting an AI agent to run the skill

Each skill folder is a self-contained [agentskills.io](https://agentskills.io)
skill — a `SKILL.md` an AI agent reads and follows. This is how a general
assistant would use them without knowing anything about `geo` or Python.

**To run Agent A** (precision build), give an assistant that can read files and
run shell commands a prompt like:

> Read `agents/A-precision/skills/audit-orchestrator/SKILL.md` and follow it to
> audit `https://example.com`. The marketplace root is `agents/A-precision/`.

**To run Agent B** (coverage build), same shape, different root:

> Read `agents/B-coverage/skills/audit-orchestrator/SKILL.md` and follow it to
> audit `https://example.com`. The marketplace root is `agents/B-coverage/`.

The orchestrator's `SKILL.md` tells the agent it is the only entrypoint (the
other skill folders are composed by it, not called directly), what one input it
needs (a URL), and what it must emit (the report described in
`marketplace.json` → `contracts`). An agent with shell access will typically
satisfy this by just running `python run.py <url>` under that root — the
`SKILL.md` documents the same contract the script implements, so both paths land
on the same report.

To ask an agent to compare them instead of running one:

> Run both `agents/A-precision` and `agents/B-coverage` against
> `https://example.com` and tell me what each one found that the other didn't.

## Reading the JSON findings

Every report — from either agent, either path above — has this shape (the
floor required by the handout; both agents add a few extra fields):

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
      "title": "No machine-readable structured data ... anywhere on the site",
      "evidence": "...",
      "suggested_action": { "summary": "...", "priority": "medium" }
    }
  ],
  "recommendations": [
    { "title": "...", "detail": "...", "category": "...", "effort": "low" }
  ]
}
```

`findings` are defects (evidence + severity + a fix). `recommendations` are
proactive, beyond-the-defects suggestions — never counted as problems. Every
skill's category is one of: `crawlability`, `rendering`, `content_extraction`,
`structured_data`, `non_text_facts`, `freshness`, `entity_identity`,
`engagement` (declared in each agent's `marketplace.json` → `contracts.categories`).

**In the terminal**, `./geo run` already prints a readable summary — that's
usually enough. For the full JSON:

```bash
# pretty-print the whole report
cat report-b.json | python3 -m json.tool | less

# just the findings, one per line
python3 -c "
import json
r = json.load(open('report-b.json'))
for f in r['findings']:
    print(f\"[{f['severity'].upper():<8}] {f['category']:<19} {f['title']}\")
"

# with jq, if installed
jq '.findings[] | {severity, category, title}' report-b.json
jq '.recommendations[] | .title' report-b.json
```

**In VS Code / the IDE**, just open the saved `report-a.json` / `report-b.json`
file — it's plain JSON, so the editor gives you folding and syntax highlighting
for free. Right-click → Format Document if it was saved unformatted.

**Comparing A and B side by side** on the same site:

```bash
./geo run A-precision https://example.com -o /tmp/a.json
./geo run B-coverage   https://example.com -o /tmp/b.json
diff <(jq -S '.findings' /tmp/a.json) <(jq -S '.findings' /tmp/b.json)
```

## Scoring instead of eyeballing

If the question is "which one is actually better" rather than "what did it
find on this one site", that's what the benchmark is for:

```bash
./geo bench                 # both agents, all 18 gold sites, full metric table
./geo bench --agent A-precision   # one agent only
```

See the root [README.md](README.md) for the current leaderboard.
