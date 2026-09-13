"""Action success rate: did the skill run to completion and emit valid output.

This is "Action Success Rate" from the deterministic-metrics framework, mapped
onto what already exists here: tools/stress_test.py (does the audit survive
~28 pathological-HTML cases without raising, within budget, with the right
categories present/absent) and tools/validate_package.py (is the marketplace
itself well-formed, and is a given report schema-valid). Both already exit 1 on
failure, so this file is a thin pytest wrapper rather than new test logic -
its value is making `pytest bench/tests/` a single command that gates on
everything, and catching a regression at the CI/file level instead of only
when someone remembers to run the script by hand.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
def test_stress_suite_all_cases_pass(agent, tmp_path):
    out = tmp_path / "stress.json"
    proc = subprocess.run(
        [sys.executable, "tools/stress_test.py", "--json", str(out)],
        cwd=REPO_ROOT / "agents" / agent, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        f"stress_test.py reported failures for {agent}:\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}"
    )
    assert out.exists(), "stress_test.py exited 0 but wrote no --json report"
    results = json.loads(out.read_text())
    assert len(results) > 0


@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
def test_package_is_marketplace_valid(agent):
    proc = subprocess.run(
        [sys.executable, "tools/validate_package.py"],
        cwd=REPO_ROOT / "agents" / agent, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"validate_package.py failed for {agent}:\n{proc.stdout[-3000:]}"


@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
def test_gold_site_report_is_schema_valid(agent, synthetic_server, gold_standards, agent_runner):
    """A live report from a real gold fixture, run back through the package's
    own --audit validator - the strictest available check on real output."""
    site_id = "site-001-healthy"
    assert site_id in gold_standards
    url = f"{synthetic_server}/{site_id}/"
    report = agent_runner(agent, url)

    tmp = REPO_ROOT / "bench" / "results" / f"_pytest_audit_{agent}.json"
    tmp.write_text(json.dumps(report), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "tools/validate_package.py", "--audit", str(tmp)],
            cwd=REPO_ROOT / "agents" / agent, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"--audit rejected a live report:\n{proc.stdout[-3000:]}"
    finally:
        tmp.unlink(missing_ok=True)
