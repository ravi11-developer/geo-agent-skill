"""Hallucination rate: an LLM judge checks each finding's evidence against the
raw page text the agent actually read.

lib/verification.py (in both agents) already catches structural ungroundedness
deterministically for free, on every run - a finding citing a page never
fetched, a count exceeding pages read. This is the complementary *semantic*
check your framework asked for: does the evidence sentence say something
actually true of the page. It needs a real judge model, so it's opt-in and
excluded from the default (no-API-key) test run.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SYNTHETIC_DIR = REPO_ROOT / "bench" / "sites" / "synthetic"

# Fast, hand-authored fixtures: any disagreement here is a real bug, not a
# judge miscalibration, so this is the strictest place to run the check.
SAMPLE_SITE_IDS = [
    "site-001-healthy", "site-005-entity-conflict", "site-006-stale-facts",
    "site-008-mixed-faults", "site-013-invalid-schema", "site-017-robots-blocks-ai",
]


@pytest.mark.llm
@pytest.mark.parametrize("site_id", SAMPLE_SITE_IDS)
def test_no_hallucinated_findings(site_id, require_api_key, synthetic_server, agent_runner):
    from bench.tools.llm_judge import judge_report, load_pages_from_dir

    url = f"{synthetic_server}/{site_id}/"
    report = agent_runner("B-coverage", url)
    if not report.get("findings"):
        pytest.skip(f"{site_id} produced no findings - nothing to judge")

    pages = load_pages_from_dir(SYNTHETIC_DIR / site_id)
    verdicts = judge_report(report, pages)
    hallucinated = [v for v in verdicts if not v.get("grounded")]

    assert not hallucinated, "\n".join(
        f"{v.get('category')} / {v.get('id')}: {v.get('reason')}" for v in hallucinated
    )


@pytest.mark.llm
@pytest.mark.slow
def test_hallucination_rate_report(require_api_key, synthetic_server, agent_runner):
    """Not a pass/fail gate - writes a hallucination-rate summary to
    bench/results/, following the same convention run_suite.py's leaderboard
    already uses, so this number can be tracked across runs like every other
    benchmark metric in this repo."""
    import json
    from bench.tools.llm_judge import judge_report, load_pages_from_dir

    rows = []
    for site_id in SAMPLE_SITE_IDS:
        url = f"{synthetic_server}/{site_id}/"
        report = agent_runner("B-coverage", url)
        if not report.get("findings"):
            continue
        pages = load_pages_from_dir(SYNTHETIC_DIR / site_id)
        verdicts = judge_report(report, pages)
        rows.append({
            "site": site_id, "findings": len(verdicts),
            "hallucinated": sum(1 for v in verdicts if not v.get("grounded")),
        })

    total = sum(r["findings"] for r in rows) or 1
    hallucinated = sum(r["hallucinated"] for r in rows)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "agent": "B-coverage", "sites": rows,
        "total_findings": total, "hallucinated": hallucinated,
        "hallucination_rate": round(hallucinated / total, 4),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = REPO_ROOT / "bench" / "results" / f"hallucination_{stamp}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nhallucination rate: {summary['hallucination_rate']:.1%}  ({hallucinated}/{total})")
    print(f"saved {out}")
