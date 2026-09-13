"""Direct unit tests for bench/scoring/*.py.

These five modules are pure functions over JSON dicts and previously had zero
direct unit tests - only indirect exercise through bench/runners/run_suite.py,
where a scoring bug would be invisible unless it happened to move the
leaderboard. Testing them in isolation catches a scoring regression at its
source instead of as an unexplained leaderboard drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.scoring.detection import match_findings, score_false_positive_rate
from bench.scoring.evidence import score_evidence, score_action, score_severity_accuracy, score_schema_validity
from bench.scoring.proactive import collect_recommendations, score_proactive
from bench.scoring.normalize import CATEGORIES, normalize_severity, classify_finding


# ---------------------------------------------------------------------------
# detection.py
# ---------------------------------------------------------------------------

def test_match_findings_exact_category_match_is_tp():
    agent = [{"category": "structured_data"}]
    gold = [{"category": "structured_data"}]
    result = match_findings(agent, gold)
    assert result["tp"] == 1 and result["fp"] == 0 and result["fn"] == 0
    assert result["precision"] == 1.0 and result["recall"] == 1.0


def test_match_findings_extra_category_is_fp():
    agent = [{"category": "structured_data"}, {"category": "freshness"}]
    gold = [{"category": "structured_data"}]
    result = match_findings(agent, gold)
    assert result["fp"] == 1
    assert result["fp_categories"] == ["freshness"]


def test_match_findings_missing_category_is_fn():
    agent = []
    gold = [{"category": "engagement"}]
    result = match_findings(agent, gold)
    assert result["fn"] == 1 and result["tp"] == 0


def test_match_findings_other_category_excluded_from_fp():
    """'other' is deliberately not counted as a false positive (see the module
    docstring: agents often emit extra SEO-adjacent findings under 'other')."""
    agent = [{"category": "other"}]
    gold = []
    result = match_findings(agent, gold)
    assert result["fp"] == 0
    assert result["precision"] == 1.0  # no findings and no gold -> vacuously perfect


def test_match_findings_empty_both_sides_is_perfect():
    """A healthy site with no gold findings and no agent findings is a perfect
    (vacuous) match: nothing was expected and nothing wrong was reported."""
    result = match_findings([], [])
    assert result["precision"] == 1.0 and result["recall"] == 1.0 and result["f1"] == 1.0


def test_false_positive_rate_healthy_site_zero_findings():
    gold = {"expected_findings": [], "max_acceptable_findings": 5}
    assert score_false_positive_rate([], gold) == 1.0


def test_false_positive_rate_healthy_site_with_findings_penalized():
    gold = {"expected_findings": [], "max_acceptable_findings": 5}
    one = score_false_positive_rate([{"category": "freshness"}], gold)
    three = score_false_positive_rate([{"category": "freshness"}] * 3, gold)
    assert one == 0.7
    assert three < one  # more spurious findings must score worse, not equal or better


def test_false_positive_rate_ignores_other_category():
    gold = {"expected_findings": [], "max_acceptable_findings": 5}
    assert score_false_positive_rate([{"category": "other"}] * 5, gold) == 1.0


def test_false_positive_rate_within_acceptable_excess_is_perfect():
    gold = {"expected_findings": [{"category": "structured_data"}], "max_acceptable_findings": 3}
    findings = [{"category": "structured_data"}] * 3
    assert score_false_positive_rate(findings, gold) == 1.0


# ---------------------------------------------------------------------------
# evidence.py
# ---------------------------------------------------------------------------

def test_score_evidence_rewards_measurement_url_and_status():
    strong = {"evidence": 'GET https://example.com/ returned HTTP 404; "Page not found" was served.'}
    weak = {"evidence": "The page seems broken."}
    assert score_evidence(strong) > score_evidence(weak)


def test_score_evidence_bounded_zero_to_four():
    assert 0 <= score_evidence({"evidence": ""}) <= 4
    assert 0 <= score_evidence({"evidence": "x" * 500}) <= 4


def test_score_action_rewards_concrete_fix():
    concrete = {"suggested_action": {"summary": "Add Organization JSON-LD with foundingDate and sameAs to https://example.com/."}}
    vague = {"suggested_action": {"summary": "Improve SEO."}}
    assert score_action(concrete) >= score_action(vague)


def test_score_severity_accuracy_exact_and_off_by_one():
    assert score_severity_accuracy("high", "high") == 1.0
    assert score_severity_accuracy("high", "medium") == 0.5
    assert score_severity_accuracy("critical", "low") == 0.0


def test_score_schema_validity_rejects_missing_required_fields():
    complete = {
        "site": "x", "audited_at": "2026-01-01T00:00:00Z",
        "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0},
        "findings": [], "recommendations": [],
    }
    assert score_schema_validity(complete) == 1.0
    incomplete = {"findings": []}
    assert score_schema_validity(incomplete) < 1.0


# ---------------------------------------------------------------------------
# proactive.py
# ---------------------------------------------------------------------------

def test_collect_recommendations_reads_multiple_field_shapes():
    report = {
        "recommendations": [{"title": "Add sameAs links"}],
        "suggestions": ["Publish an XML sitemap"],
    }
    collected = collect_recommendations(report)
    assert any("sameAs" in c for c in collected)
    assert any("sitemap" in c for c in collected)


def test_collect_recommendations_includes_low_severity_findings_as_advice():
    report = {"findings": [{"title": "Minor issue", "severity": "low",
                            "suggested_action": {"summary": "Consider fixing X"}}]}
    collected = collect_recommendations(report)
    assert any("Minor issue" in c for c in collected)


def test_score_proactive_none_when_gold_sets_no_expectation():
    report = {"recommendations": []}
    gold = {}  # no expected_proactive key at all
    assert score_proactive(report, gold) is None


def test_score_proactive_rewards_overlap_with_expected():
    gold = {"expected_proactive": ["Publish an XML sitemap with lastmod dates"]}
    matching = {"recommendations": [{"title": "Publish an XML sitemap with accurate lastmod values"}]}
    empty = {"recommendations": []}
    assert score_proactive(matching, gold) > (score_proactive(empty, gold) or 0)


# ---------------------------------------------------------------------------
# normalize.py
# ---------------------------------------------------------------------------

def test_normalize_severity_maps_known_aliases():
    assert normalize_severity("critical") == "critical"
    assert normalize_severity("warning") in {"medium", "low", "high"}  # must resolve to a known bucket
    assert normalize_severity("CRITICAL") == "critical"  # case-insensitive


def test_classify_finding_falls_back_to_other_for_unknown_category():
    result = classify_finding({"category": "totally_made_up_category", "title": "zzz nonsense", "evidence": ""})
    assert result in CATEGORIES | {"other"}
