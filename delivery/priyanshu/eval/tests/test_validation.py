#!/usr/bin/env python3
"""The evidence gate: structured-output validation, repair and abstention."""

from __future__ import annotations

import unittest

from .helpers import (
    fake_config, first_evidence_ids, ids_on_different_pages,
    observation_payload, pack_from_fixture, suggestion_payload,
)

from lib.llm.validation import (
    ABSTAIN, OBSERVE, PROMOTE, promotion_decision,
    validate_diagnoses, validate_observations, validate_suggestions,
)
from lib.llm.errors import ERROR_CATEGORIES, RECOVERY_ALLOWLIST


def codes(result):
    return {issue.code for issue in result.issues}


class TestObservationValidation(unittest.TestCase):
    def setUp(self):
        self.pack = pack_from_fixture("brochure")
        self.config = fake_config()
        self.ids = first_evidence_ids(self.pack, 2)

    def check(self, payload, **kwargs):
        return validate_observations(payload, self.pack, config=self.config, **kwargs)

    # -- happy path ------------------------------------------------------
    def test_valid_response_is_accepted(self):
        result = self.check(observation_payload(evidence_refs=self.ids[:1]))
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.accepted[0]["id"], "SEM-001")
        self.assertEqual(result.accepted[0]["category"], "engagement_sentiment")

    def test_empty_observation_list_is_valid(self):
        result = self.check({"observations": []})
        self.assertEqual(result.accepted, [])
        self.assertEqual(result.rejected, 0)

    # -- shape -----------------------------------------------------------
    def test_malformed_response_is_rejected(self):
        self.assertIn("shape", codes(self.check("not an object")))
        self.assertIn("shape", codes(self.check({"results": []})))
        self.assertIn("shape", codes(self.check({"observations": {}})))

    def test_missing_required_fields_are_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1])
        del payload["observations"][0]["title"]
        self.assertIn("required_field", codes(self.check(payload)))

    def test_missing_evidence_refs_are_rejected(self):
        self.assertIn("required_field", codes(self.check(observation_payload(evidence_refs=[]))))

    # -- vocabulary ------------------------------------------------------
    def test_invalid_enum_is_rejected(self):
        for field, bad in (("aspect", "vibes"), ("sentiment", "grumpy"),
                           ("emotion", "ennui"), ("severity", "catastrophic")):
            payload = observation_payload(evidence_refs=self.ids[:1], **{field: bad})
            self.assertIn("enum", codes(self.check(payload)), field)

    # -- citation --------------------------------------------------------
    def test_unknown_evidence_id_is_rejected(self):
        result = self.check(observation_payload(evidence_refs=["P999-S999"]))
        self.assertIn("unknown_evidence_id", codes(result))
        self.assertEqual(result.accepted, [])

    def test_malformed_evidence_id_is_rejected(self):
        self.assertIn("evidence_id_format", codes(self.check(observation_payload(evidence_refs=["page-3"]))))

    def test_evidence_from_another_page_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1], pages=["P003"])
        self.assertIn("evidence_page_mismatch", codes(self.check(payload)))

    # -- quotation -------------------------------------------------------
    def test_verbatim_quote_is_accepted(self):
        section = self.pack.sections_by_id[self.ids[0]]
        quote = " ".join(section.text.split()[:8])
        result = self.check(observation_payload(evidence_refs=self.ids[:1], quote=quote))
        self.assertEqual(len(result.accepted), 1)

    def test_invented_quote_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1],
                                      quote="We guarantee same day delivery worldwide")
        self.assertIn("quote_not_found", codes(self.check(payload)))

    # -- numeracy / technology / locality --------------------------------
    def test_invented_number_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1],
                                      evidence="The page lists 97 separate exclusions.")
        self.assertIn("unsupported_number", codes(self.check(payload)))

    def test_number_present_in_the_evidence_is_allowed(self):
        pack = pack_from_fixture("brochure")
        ids = [s.evidence_id for p in pack.pages for s in p.sections
               if "800 rupees" in s.text]
        self.assertTrue(ids, "fixture should contain a priced section")
        result = validate_observations(
            observation_payload(evidence_refs=ids[:1], aspect="pricing_transparency",
                                evidence="Mugs start from 800 rupees with no unit or exclusions stated."),
            pack, config=self.config)
        self.assertEqual(len(result.accepted), 1)

    def test_unsupported_technology_claim_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1],
                                      evidence="The WordPress theme buries the policy in an accordion.")
        self.assertIn("unsupported_technology", codes(self.check(payload)))

    def test_a_site_url_followed_by_punctuation_is_not_foreign(self):
        # "see http://fixture.test/pricing.html." - the trailing full stop must
        # not turn an in-site link into a foreign one.
        payload = observation_payload(
            evidence_refs=self.ids[:1],
            evidence="The same claim appears on http://fixture.test/pricing.html.")
        result = self.check(payload)
        self.assertEqual(len(result.accepted), 1, [i.message for i in result.issues])

    def test_foreign_url_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1],
                                      evidence="See https://evil.example.com/more for context.")
        self.assertIn("foreign_url", codes(self.check(payload)))

    # -- consistency and novelty ----------------------------------------
    def test_contradicting_a_clean_measurement_is_rejected(self):
        payload = observation_payload(
            evidence_refs=self.ids[:1],
            evidence="The page ships no JSON-LD structured data at all.")
        result = self.check(payload, coverage={"structured_data": "clean"})
        self.assertIn("contradicts_deterministic", codes(result))

    def test_restating_a_deterministic_finding_is_rejected(self):
        payload = observation_payload(
            evidence_refs=self.ids[:1],
            evidence="Business copy is only present after JavaScript hydration.")
        result = self.check(payload, deterministic_findings=[{"category": "rendering"}])
        self.assertIn("duplicate_of_deterministic", codes(result))

    def test_duplicate_observations_are_collapsed(self):
        payload = observation_payload(evidence_refs=self.ids[:1])
        payload["observations"].append(dict(payload["observations"][0]))
        result = self.check(payload)
        self.assertEqual(len(result.accepted), 1)
        self.assertIn("duplicate_observation", codes(result))

    # -- sufficiency -----------------------------------------------------
    def test_high_severity_needs_two_sections(self):
        payload = observation_payload(evidence_refs=self.ids[:1], severity="high", confidence=0.95)
        self.assertIn("insufficient_evidence", codes(self.check(payload)))
        payload = observation_payload(evidence_refs=self.ids[:2], severity="high", confidence=0.95)
        self.assertEqual(len(self.check(payload).accepted), 1)

    def test_low_confidence_abstains(self):
        result = self.check(observation_payload(evidence_refs=self.ids[:1], confidence=0.4))
        self.assertIn("abstain_low_confidence", codes(result))
        self.assertEqual(result.accepted, [])

    def test_customer_sentiment_over_brand_copy_is_rejected(self):
        payload = observation_payload(evidence_refs=self.ids[:1],
                                      analysis_type="customer_sentiment", source_kind="customer_voice")
        self.assertIn("source_kind_mismatch", codes(self.check(payload)))

    def test_customer_sentiment_over_customer_voice_is_allowed(self):
        pack = pack_from_fixture("ecommerce")
        voice = [s.evidence_id for p in pack.pages for s in p.sections
                 if s.source_kind == "customer_voice"]
        self.assertTrue(voice, "fixture should contain a reviews section")
        result = validate_observations(
            observation_payload(evidence_refs=voice[:1], aspect="reviews_testimonials",
                                sentiment="mixed", emotion="trust",
                                analysis_type="customer_sentiment", source_kind="customer_voice",
                                evidence="A verified buyer reports a late delivery alongside praise for support."),
            pack, config=self.config)
        self.assertEqual(len(result.accepted), 1)

    def test_independent_section_count_is_recorded(self):
        ids = ids_on_different_pages(self.pack, 2)
        result = self.check(observation_payload(evidence_refs=ids, severity="medium"))
        self.assertEqual(result.accepted[0]["independent_sections"], 2)


class TestSuggestionValidation(unittest.TestCase):
    def setUp(self):
        self.pack = pack_from_fixture("brochure")
        self.findings = [{"id": "MKT-EXTR-01", "category": "content_extraction",
                          "locations": ["http://fixture.test/"],
                          "suggested_action": {"summary": "original"}}]

    def test_valid_suggestion_is_accepted(self):
        result = validate_suggestions(suggestion_payload("MKT-EXTR-01"), self.findings, self.pack)
        self.assertEqual(len(result.accepted), 1)

    def test_unknown_finding_id_is_rejected(self):
        result = validate_suggestions(suggestion_payload("MKT-FAKE-99"), self.findings, self.pack)
        self.assertIn("unknown_finding_id", codes(result))

    def test_generic_suggestion_is_rejected(self):
        payload = suggestion_payload("MKT-EXTR-01", acceptance_test="", implementation_steps=[])
        self.assertIn("generic_suggestion", codes(validate_suggestions(payload, self.findings, self.pack)))

    def test_suggestion_may_reference_an_audited_url_in_prose(self):
        payload = suggestion_payload(
            "MKT-EXTR-01",
            where="http://fixture.test/",
            recommendation="On http://fixture.test/: restate the fact as HTML text.")
        result = validate_suggestions(payload, self.findings, self.pack)
        self.assertEqual(len(result.accepted), 1, [i.message for i in result.issues])

    def test_suggestion_cannot_introduce_a_foreign_url(self):
        payload = suggestion_payload("MKT-EXTR-01", where="https://competitor.example.com/guide")
        self.assertIn("foreign_url", codes(validate_suggestions(payload, self.findings, self.pack)))

    def test_suggestion_cannot_cite_an_unissued_evidence_id(self):
        payload = suggestion_payload("MKT-EXTR-01", evidence_refs=["P404-S001"])
        self.assertIn("unknown_evidence_id", codes(validate_suggestions(payload, self.findings, self.pack)))

    def test_duplicate_suggestions_are_rejected(self):
        payload = suggestion_payload("MKT-EXTR-01")
        payload["suggestions"].append(dict(payload["suggestions"][0]))
        result = validate_suggestions(payload, self.findings, self.pack)
        self.assertEqual(len(result.accepted), 1)
        self.assertIn("duplicate_suggestion", codes(result))


class TestDiagnosisValidation(unittest.TestCase):
    def setUp(self):
        self.events = [{"event_id": "ERR-001", "phase": "render"}]

    def test_valid_diagnosis_is_accepted(self):
        payload = {"diagnoses": [{"event_id": "ERR-001", "category": "transient_network",
                                  "recovery_code": "RETRY_TRANSIENT", "confidence": 0.7,
                                  "hypothesis": "slow origin", "limitation_summary": "one page missing"}]}
        result = validate_diagnoses(payload, self.events, RECOVERY_ALLOWLIST, ERROR_CATEGORIES)
        self.assertEqual(len(result.accepted), 1)

    def test_unknown_event_is_rejected(self):
        payload = {"diagnoses": [{"event_id": "ERR-777", "category": "transient_network",
                                  "recovery_code": "RETRY_TRANSIENT", "confidence": 0.7}]}
        self.assertIn("unknown_event_id",
                      codes(validate_diagnoses(payload, self.events, RECOVERY_ALLOWLIST, ERROR_CATEGORIES)))

    def test_recovery_outside_the_allowlist_is_rejected(self):
        payload = {"diagnoses": [{"event_id": "ERR-001", "category": "transient_network",
                                  "recovery_code": "RUN_SHELL_COMMAND", "confidence": 0.9}]}
        self.assertIn("recovery_not_allowlisted",
                      codes(validate_diagnoses(payload, self.events, RECOVERY_ALLOWLIST, ERROR_CATEGORIES)))


class TestPromotionPolicy(unittest.TestCase):
    def setUp(self):
        self.config = fake_config()

    def observation(self, **fields):
        base = {"confidence": 0.9, "severity": "medium", "evidence_refs": ["P001-S001"],
                "independent_sections": 1}
        base.update(fields)
        return base

    def test_high_confidence_promotes(self):
        decision, _ = promotion_decision(self.observation(), config=self.config)
        self.assertEqual(decision, PROMOTE)

    def test_below_the_floor_abstains(self):
        decision, _ = promotion_decision(self.observation(confidence=0.4), config=self.config)
        self.assertEqual(decision, ABSTAIN)

    def test_middle_band_needs_corroboration(self):
        decision, _ = promotion_decision(self.observation(confidence=0.7), config=self.config)
        self.assertEqual(decision, OBSERVE)
        decision, _ = promotion_decision(
            self.observation(confidence=0.7, evidence_refs=["P001-S001", "P002-S001"],
                             independent_sections=2), config=self.config)
        self.assertEqual(decision, PROMOTE)

    def test_high_severity_requires_a_verifier_pass(self):
        observation = self.observation(severity="high", evidence_refs=["P001-S001", "P002-S001"],
                                       independent_sections=2)
        decision, _ = promotion_decision(observation, config=self.config)
        self.assertEqual(decision, OBSERVE)
        decision, _ = promotion_decision(observation, config=self.config,
                                         verifier={"verdict": "confirm"})
        self.assertEqual(decision, PROMOTE)

    def test_verifier_rejection_abstains(self):
        observation = self.observation(severity="high", evidence_refs=["P001-S001", "P002-S001"],
                                       independent_sections=2)
        decision, reason = promotion_decision(observation, config=self.config,
                                              verifier={"verdict": "reject", "reason": "not supported"})
        self.assertEqual(decision, ABSTAIN)
        self.assertIn("not supported", reason)

    def test_high_severity_with_one_section_never_promotes(self):
        decision, _ = promotion_decision(self.observation(severity="high"), config=self.config,
                                         verifier={"verdict": "confirm"})
        self.assertEqual(decision, OBSERVE)


if __name__ == "__main__":
    unittest.main()
