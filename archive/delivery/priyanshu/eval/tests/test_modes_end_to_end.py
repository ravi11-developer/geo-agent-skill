#!/usr/bin/env python3
"""End-to-end mode behaviour and backward compatibility.

These are the regression tests that matter most: they run the real entrypoint
against local fixtures in every feature mode and assert that the deterministic
report is preserved.
"""

from __future__ import annotations

import copy
import json
import unittest

from .helpers import (
    MARKETPLACE_ROOT, first_evidence_ids, ids_on_different_pages,
    observation_payload, pack_from_fixture, serve, suggestion_payload,
)

from run import run_audit
from lib.contracts import SEMANTIC_CATEGORY

VOLATILE = ("audited_at", "runtime_seconds", "elapsed_seconds", "latency_ms",
            "elapsed_ms", "snapshot_id")


def stable(value):
    """Strip timing and identity noise so two reports can be compared."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def deterministic_core(report):
    """The part of the report that existing consumers and the scorer read."""
    return stable({
        "site": report["site"],
        "summary": report["summary"],
        "findings": report["findings"],
        "recommendations": report["recommendations"],
        "coverage": report["coverage"],
    })


class ModeTestBase(unittest.TestCase):
    fixture = "brochure"

    @classmethod
    def setUpClass(cls):
        cls.server = serve(cls.fixture)
        cls.url = cls.server.__enter__()
        cls.baseline = run_audit(cls.url)
        # Evidence ids as the skill will actually issue them for this crawl.
        cls.pack = pack_from_fixture(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def audit(self, mode, responses=None, **llm):
        config = {"llm": {"mode": mode, "provider": "fake", "model": "fake-model", **llm}}
        if responses is not None:
            config["llm_fake_responses"] = responses
        return run_audit(self.url, config)



class TestOffMode(ModeTestBase):
    def test_off_is_the_default(self):
        self.assertEqual(self.baseline["llm"]["mode"], "off")
        self.assertEqual(self.baseline["audit_health"]["llm_mode"], "off")

    def test_off_makes_no_calls_and_needs_no_key(self):
        self.assertEqual(self.baseline["llm"]["calls"], 0)
        self.assertEqual(self.baseline["llm"]["status"], "disabled")

    def test_off_emits_no_observations(self):
        self.assertNotIn("observations", self.baseline)
        self.assertNotIn("promoted_observations", self.baseline)

    def test_off_output_is_reproducible(self):
        again = run_audit(self.url)
        self.assertEqual(deterministic_core(self.baseline), deterministic_core(again))

    def test_report_still_carries_every_mandatory_round3_field(self):
        for key in ("site", "audited_at", "summary", "findings"):
            self.assertIn(key, self.baseline)
        self.assertIn("total_findings", self.baseline["summary"])
        for finding in self.baseline["findings"]:
            for key in ("id", "title", "severity", "evidence", "suggested_action"):
                self.assertIn(key, finding)

    def test_new_blocks_are_additive_and_serialisable(self):
        json.dumps(self.baseline, default=str)
        self.assertIn("audit_health", self.baseline)
        self.assertIn(self.baseline["audit_health"]["status"], ("complete", "partial", "failed"))


class TestSuggestionsOnlyMode(ModeTestBase):
    fixture = "js-heavy"

    def test_findings_are_never_added_or_removed(self):
        finding_id = self.baseline["findings"][0]["id"]
        report = self.audit("suggestions_only", {"suggestion": suggestion_payload(finding_id)})
        self.assertEqual(len(report["findings"]), len(self.baseline["findings"]))
        self.assertEqual([f["title"] for f in report["findings"]],
                         [f["title"] for f in self.baseline["findings"]])

    def test_severity_and_priority_are_untouched(self):
        finding_id = self.baseline["findings"][0]["id"]
        report = self.audit("suggestions_only", {"suggestion": suggestion_payload(finding_id)})
        for before, after in zip(self.baseline["findings"], report["findings"]):
            self.assertEqual(before["severity"], after["severity"])
            self.assertEqual(before["suggested_action"]["priority"],
                             after["suggested_action"]["priority"])

    def test_deterministic_summary_is_the_fallback_and_is_preserved(self):
        finding_id = self.baseline["findings"][0]["id"]
        report = self.audit("suggestions_only", {"suggestion": suggestion_payload(finding_id)})
        enriched = report["findings"][0]
        self.assertEqual(enriched["suggested_action"]["summary"],
                         self.baseline["findings"][0]["suggested_action"]["summary"])
        self.assertTrue(enriched.get("llm_enriched"))
        self.assertEqual(enriched["suggested_action"]["detail"]["source"], "llm_enhanced")

    def test_a_rejected_suggestion_leaves_the_finding_alone(self):
        report = self.audit("suggestions_only",
                            {"suggestion": suggestion_payload("MKT-NOPE-99")})
        self.assertFalse(report["findings"][0].get("llm_enriched"))
        self.assertEqual(deterministic_core(report)["findings"][0]["suggested_action"]["summary"],
                         deterministic_core(self.baseline)["findings"][0]["suggested_action"]["summary"])

    def test_no_semantic_work_happens_in_this_mode(self):
        report = self.audit("suggestions_only", {"suggestion": {"suggestions": []}})
        self.assertNotIn("observations", report)
        self.assertEqual(report["audit_health"]["semantic_pages_analyzed"], 0)


class TestSemanticShadowMode(ModeTestBase):
    def test_shadow_observations_do_not_change_findings_or_scores(self):
        # Build a response that cites ids from the live pack; if the ids do not
        # resolve the observation is rejected, which would defeat the test, so
        # the assertion below also proves the ids were real.
        report = self.audit("semantic_shadow", {"semantic": _live_observation(self.url, self)})
        self.assertGreaterEqual(len(report.get("observations", [])), 1)
        self.assertEqual(deterministic_core(report), deterministic_core(self.baseline))

    def test_shadow_never_promotes(self):
        report = self.audit("semantic_shadow", {"semantic": _live_observation(self.url, self)})
        self.assertNotIn("promoted_observations", report)
        for observation in report["observations"]:
            self.assertEqual(observation["validation_status"], "observed")
            self.assertNotIn(SEMANTIC_CATEGORY, [f["category"] for f in report["findings"]])

    def test_shadow_does_not_enhance_suggestions(self):
        report = self.audit("semantic_shadow", {"semantic": {"observations": []},
                                                "suggestion": {"suggestions": []}})
        self.assertFalse(any(f.get("llm_enriched") for f in report["findings"]))

    def test_health_records_how_many_pages_were_analysed(self):
        report = self.audit("semantic_shadow", {"semantic": {"observations": []}})
        self.assertGreater(report["audit_health"]["semantic_pages_analyzed"], 0)
        self.assertEqual(report["audit_health"]["llm_mode"], "semantic_shadow")


class TestSemanticEnabledMode(ModeTestBase):
    def test_validated_observation_is_promoted_into_findings(self):
        report = self.audit("semantic_enabled", {"semantic": _live_observation(self.url, self),
                                                 "suggestion": {"suggestions": []}})
        promoted = [f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["finding_source"], "llm_semantic")
        self.assertTrue(promoted[0]["id"].startswith("MKT-SEM-"))
        self.assertIn("promotion_reason", promoted[0])

    def test_promoted_finding_keeps_its_evidence_ids(self):
        report = self.audit("semantic_enabled", {"semantic": _live_observation(self.url, self),
                                                 "suggestion": {"suggestions": []}})
        promoted = [f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY][0]
        self.assertTrue(promoted["proof"]["evidence_refs"])

    def test_deterministic_findings_are_never_removed_or_downgraded(self):
        with serve("js-heavy") as url:
            baseline = run_audit(url)
            report = run_audit(url, {"llm": {"mode": "semantic_enabled", "provider": "fake",
                                             "model": "fake-model"},
                                     "llm_fake_responses": {"semantic": {"observations": []},
                                                            "suggestion": {"suggestions": []}}})
        deterministic = [f for f in report["findings"] if f.get("finding_source") != "llm_semantic"]
        self.assertEqual([f["title"] for f in deterministic], [f["title"] for f in baseline["findings"]])
        for before, after in zip(baseline["findings"], deterministic):
            self.assertEqual(before["severity"], after["severity"])

    def test_low_confidence_observation_is_not_promoted(self):
        payload = _live_observation(self.url, self, confidence=0.5)
        report = self.audit("semantic_enabled", {"semantic": payload, "suggestion": {"suggestions": []}})
        self.assertEqual([f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY], [])


class TestFullMode(ModeTestBase):
    def test_full_mode_runs_the_verifier_for_high_severity(self):
        payload = _live_observation(self.url, self, severity="high", confidence=0.95, two_ids=True)
        report = self.audit("full", {
            "semantic": payload,
            "verifier": {"verdict": "confirm", "reason": "supported", "severity_supported": "high",
                         "confidence": 0.9},
            "suggestion": {"suggestions": []},
        })
        promoted = [f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY]
        self.assertEqual(len(promoted), 1)
        self.assertTrue(any(call["task"] == "verifier" for call in report["llm"]["call_log"]))

    def test_verifier_rejection_keeps_it_out_of_findings(self):
        payload = _live_observation(self.url, self, severity="high", confidence=0.95, two_ids=True)
        report = self.audit("full", {
            "semantic": payload,
            "verifier": {"verdict": "reject", "reason": "the sections do not show this",
                         "confidence": 0.9},
            "suggestion": {"suggestions": []},
        })
        self.assertEqual([f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY], [])
        self.assertEqual(report["observations"][0]["validation_status"], "abstained")

    def test_verifier_can_lower_severity_but_never_raise_it(self):
        payload = _live_observation(self.url, self, severity="high", confidence=0.95, two_ids=True)
        report = self.audit("full", {
            "semantic": payload,
            "verifier": {"verdict": "confirm", "reason": "weaker than claimed",
                         "severity_supported": "low", "confidence": 0.9},
            "suggestion": {"suggestions": []},
        })
        promoted = [f for f in report["findings"] if f["category"] == SEMANTIC_CATEGORY]
        self.assertEqual(promoted[0]["severity"], "low")

    def test_llm_failure_still_produces_the_deterministic_report(self):
        from lib.llm.provider import LLMError
        report = self.audit("full", {"*": LLMError("timeout", "timed out", retryable=True)})
        self.assertEqual(deterministic_core(report), deterministic_core(self.baseline))
        self.assertEqual(report["llm"]["status"], "failed")
        self.assertIn(report["audit_health"]["status"], ("complete", "partial"))


class TestProviderMisconfiguration(ModeTestBase):
    def test_missing_api_key_degrades_to_deterministic_with_a_warning(self):
        report = run_audit(self.url, {"llm": {"mode": "full", "provider": "anthropic", "model": ""}})
        self.assertEqual(deterministic_core(report), deterministic_core(self.baseline))
        self.assertEqual(report["llm"]["status"], "skipped")
        self.assertTrue(report["audit_health"]["warnings"])
        self.assertIn("llm_provider_unavailable", report["audit_health"]["fallbacks_used"])


# ---------------------------------------------------------------------------
# helper: build a response whose evidence ids come from the live crawl
# ---------------------------------------------------------------------------

_LIVE_PACK_CACHE: dict[str, object] = {}


def _live_pack(url: str):
    """Rebuild the evidence pack exactly as the live audit builds it.

    The crawler and the pack builder are both deterministic, so replaying them
    here yields the same evidence ids the skill will issue during the audit -
    which is what lets a scripted response cite ids that actually resolve.
    """
    if url not in _LIVE_PACK_CACHE:
        from lib.llm.evidence import build_evidence_pack
        from lib.loader import load_manifest, load_skills
        crawl_skill = next(s for s in load_skills(load_manifest(), kinds=("audit",))
                           if s.id == "crawl-render-audit")
        module = __import__(crawl_skill.run.__module__)
        snapshot = module.crawl(url, max_pages=12)
        _LIVE_PACK_CACHE[url] = build_evidence_pack(snapshot)
    return _LIVE_PACK_CACHE[url]


def _live_observation(url: str, case, *, two_ids: bool = False, **fields):
    pack = _live_pack(url)
    assert pack is not None, "the orchestrator must expose the evidence pack for tests"
    ids = ids_on_different_pages(pack, 2) if two_ids else first_evidence_ids(pack, 1)
    return observation_payload(evidence_refs=ids, **fields)


if __name__ == "__main__":
    unittest.main()
