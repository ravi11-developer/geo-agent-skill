#!/usr/bin/env python3
"""Standalone hallucination-rate judge for testing only.

Never imported by any shipped agent - both marketplaces guarantee "no API key
required" for the product itself, and this script exists purely to grade
reports after the fact. lib/verification.py (built for both agents) already
catches *structural* ungroundedness deterministically (a finding citing a page
never fetched, a count exceeding pages read); this catches what that can't:
whether an evidence sentence is actually *true of* the page it cites.

    python bench/tools/llm_judge.py --agent B-coverage --site-id site-001-healthy
    python bench/tools/llm_judge.py --report report.json --pages-dir bench/sites/synthetic/site-001-healthy

Requires ANTHROPIC_API_KEY. Batches every finding in one report into a single
judge call, not one call per finding, to keep this cheap enough to run
routinely instead of only once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from bench.scoring.adjudicate import parse_page  # reuses the independent, BeautifulSoup-free text extractor

JUDGE_PROMPT = """You are a strict fact-checker. RAW_PAGES below is the actual fetched text of the pages an \
audit tool cited as evidence. FINDINGS is the list of claims that audit tool made about those pages.

For each finding, decide only whether its `evidence` text is directly supported by RAW_PAGES - do not judge \
whether the finding is a good or useful observation, whether the severity is right, or whether the suggested \
fix is sensible. A claim is grounded if a reader could verify it by reading RAW_PAGES; it is not grounded if it \
states a fact, quote, or number that does not appear there.

Respond with ONLY a JSON array, one object per finding in the same order, no other text:
[{{"id": "<finding id>", "grounded": true|false, "reason": "<one sentence, quote the unsupported part if false>"}}]

RAW_PAGES:
{pages}

FINDINGS:
{findings}"""


def load_pages_from_dir(pages_dir: Path) -> dict[str, str]:
    """Read every HTML file in a fixture directory and extract its visible text
    independently of the agent under test - the whole point of a judge is that
    it must not simply trust the same extraction pipeline being graded."""
    pages = {}
    for html_file in sorted(pages_dir.glob("*.html")):
        raw = html_file.read_bytes()
        model = parse_page(raw)
        pages[html_file.name] = model.full_text
    return pages


def judge_report(report: dict[str, Any], pages: dict[str, str], model: str = "claude-haiku-4-5-20251001") -> list[dict]:
    """For each finding, ask a judge model whether its evidence is grounded in `pages`."""
    findings = report.get("findings", [])
    if not findings:
        return []

    import anthropic

    client = anthropic.Anthropic()
    findings_text = json.dumps(
        [{"id": f.get("id"), "title": f.get("title"), "evidence": f.get("evidence")} for f in findings],
        indent=2,
    )
    pages_text = "\n\n".join(f"--- {name} ---\n{text[:6000]}" for name, text in pages.items())
    response = client.messages.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(pages=pages_text, findings=findings_text)}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    verdicts = json.loads(text)
    by_id = {f.get("id"): f for f in findings}
    for verdict in verdicts:
        verdict["category"] = by_id.get(verdict.get("id"), {}).get("category")
    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", help="path to a saved report JSON")
    parser.add_argument("--pages-dir", help="directory of raw .html fixtures the report was run against")
    parser.add_argument("--out", help="write verdicts JSON here")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    if not args.report or not args.pages_dir:
        print("--report and --pages-dir are required", file=sys.stderr)
        return 2

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    pages = load_pages_from_dir(Path(args.pages_dir))
    verdicts = judge_report(report, pages)

    hallucinated = [v for v in verdicts if not v.get("grounded")]
    for v in hallucinated:
        print(f"UNGROUNDED [{v.get('category')}] {v.get('id')}: {v.get('reason')}")
    print(f"\n{len(verdicts) - len(hallucinated)}/{len(verdicts)} findings grounded")

    if args.out:
        Path(args.out).write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
    return 1 if hallucinated else 0


if __name__ == "__main__":
    raise SystemExit(main())
