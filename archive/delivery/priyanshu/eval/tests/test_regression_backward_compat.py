#!/usr/bin/env python3
"""Regression: the deterministic agent must not have moved.

The comparison is against a frozen golden report captured from the shipped
agent, so this catches a drift introduced by any future change, not just by the
LLM layer. Regenerate deliberately with:

    python eval/tests/test_regression_backward_compat.py --freeze
"""

from __future__ import annotations

import json
import os
import sys
import unittest

from .helpers import MARKETPLACE_ROOT, TESTS_DIR, serve

from run import run_audit
from lib.contracts import CATEGORIES
from lib.loader import load_manifest, load_skills

GOLDEN_DIR = os.path.join(TESTS_DIR, "golden")
SITES = ("site-001-healthy", "site-003-js-only-facts", "site-007-poor-navigation",
         "site-008-mixed-faults", "site-012-spa-shell", "site-016-noindex-block")


def core(report: dict) -> dict:
    """Everything an existing consumer or the benchmark scorer reads."""
    return {
        "summary": {k: v for k, v in report["summary"].items() if k != "runtime_seconds"},
        "coverage": report["coverage"],
        "findings": [
            {
                "id": f["id"], "title": f["title"], "category": f["category"],
                "_category": f.get("_category"), "severity": f["severity"],
                "evidence": f["evidence"], "locations": f.get("locations", []),
                "suggested_action": {
                    "summary": f["suggested_action"]["summary"],
                    "priority": f["suggested_action"]["priority"],
                    "mechanism": f["suggested_action"].get("mechanism"),
                },
            }
            for f in report["findings"]
        ],
        "recommendations": [{"title": r["title"], "category": r["category"],
                             "effort": r.get("effort")} for r in report["recommendations"]],
    }


def golden_path(site: str) -> str:
    return os.path.join(GOLDEN_DIR, f"{site}.json")


def capture(site: str) -> dict:
    with serve(site) as url:
        return core(run_audit(url))


class TestDeterministicOutputIsFrozen(unittest.TestCase):
    def test_golden_reports_exist(self):
        self.assertTrue(os.path.isdir(GOLDEN_DIR), "run with --freeze to create the golden set")

    def test_every_site_matches_its_golden_report(self):
        for site in SITES:
            with self.subTest(site=site):
                with open(golden_path(site), encoding="utf-8") as handle:
                    expected = json.load(handle)
                actual = capture(site)
                # URLs contain an ephemeral port, so compare everything else.
                self.assertEqual(scrub(expected), scrub(actual))


class TestManifestInvariants(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest()

    def test_exactly_one_entrypoint(self):
        entrypoints = [s for s in self.manifest["skills"] if s.get("kind") == "orchestrator"]
        self.assertEqual(len(entrypoints), 1)
        self.assertEqual(self.manifest["entrypoint"], entrypoints[0]["id"])

    def test_declared_categories_still_match_the_code(self):
        self.assertEqual(set(self.manifest["contracts"]["categories"]), set(CATEGORIES))

    def test_semantic_category_is_kept_out_of_the_deterministic_contract(self):
        self.assertNotIn("engagement_sentiment", self.manifest["contracts"]["categories"])
        self.assertIn("engagement_sentiment", self.manifest["contracts"]["semantic_categories"])

    def test_every_consumed_artifact_is_produced_by_someone(self):
        produced = {p for s in self.manifest["skills"] for p in s.get("produces", [])}
        for skill in self.manifest["skills"]:
            for dependency in skill.get("consumes", []):
                self.assertIn(dependency, produced, f"{skill['id']} consumes {dependency}")

    def test_skills_load_and_order_producers_before_consumers(self):
        skills = load_skills(self.manifest, kinds=("audit",))
        available: set[str] = set()
        for skill in skills:
            for dependency in skill.consumes:
                self.assertIn(dependency, available, f"{skill.id} ran before {dependency} existed")
            available.update(skill.produces)

    def test_the_semantic_skill_runs_last(self):
        skills = load_skills(self.manifest, kinds=("audit",))
        self.assertEqual(skills[-1].id, "sentiment-engagement-audit")

    def test_llm_defaults_are_off_in_the_manifest(self):
        llm = self.manifest["features"]["llm"]
        self.assertFalse(llm["enabled_by_default"])
        self.assertEqual(llm["default_mode"], "off")

    def test_no_model_identifier_is_committed_anywhere(self):
        offenders = []
        for root, _dirs, files in os.walk(MARKETPLACE_ROOT):
            if "__pycache__" in root:
                continue
            for name in files:
                if not name.endswith((".py", ".json")):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read().lower()
                for marker in ("claude-3", "claude-sonnet-", "claude-opus-", "gpt-4", "gpt-5"):
                    if marker in text:
                        offenders.append(f"{path}: {marker}")
        self.assertEqual(offenders, [])


def scrub(payload):
    """Drop the ephemeral host:port so golden files are portable."""
    text = json.dumps(payload, sort_keys=True)
    import re
    text = re.sub(r"http://127\.0\.0\.1:\d+", "http://HOST", text)
    return json.loads(text)


def freeze() -> None:
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    for site in SITES:
        with open(golden_path(site), "w", encoding="utf-8") as handle:
            json.dump(scrub(capture(site)), handle, indent=2, sort_keys=True)
        print(f"froze {site}")


if __name__ == "__main__":
    if "--freeze" in sys.argv:
        freeze()
    else:
        unittest.main()
