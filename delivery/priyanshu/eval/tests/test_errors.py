#!/usr/bin/env python3
"""Error normalisation, recovery policy, audit health and provider failures."""

from __future__ import annotations

import unittest

from .helpers import fake_config, pack_from_fixture

from lib.llm.engine import HybridEngine
from lib.llm.errors import (
    AuditHealth, ErrorLog, MAX_GENERAL_RETRIES, RECOVERY_ALLOWLIST,
    apply_diagnosis, classify, limitation_notes, recovery_is_allowed,
)
from lib.llm.provider import LLMError


class TestClassification(unittest.TestCase):
    def setUp(self):
        self.log = ErrorLog()

    def test_timeout_is_transient_not_a_site_defect(self):
        event = self.log.record(phase="render", operation="wait_for_page",
                                exc=TimeoutError("networkidle timeout after 15000 ms"),
                                url="http://x/p", http_status=200)
        self.assertEqual(event.category, "transient_network")
        self.assertTrue(event.retryable)

    def test_http_429_is_transient(self):
        event = self.log.record(phase="crawl", operation="fetch", message="HTTP 429", http_status=429)
        self.assertEqual(event.category, "transient_network")

    def test_http_500_on_first_attempt_is_transient(self):
        event = self.log.record(phase="crawl", operation="fetch", message="HTTP 500", http_status=500)
        self.assertEqual(event.category, "transient_network")

    def test_http_500_on_a_retry_becomes_a_site_defect(self):
        event = self.log.record(phase="crawl", operation="fetch", message="HTTP 500",
                                http_status=500, attempt=2, max_attempts=2)
        self.assertEqual(event.category, "website_defect")

    def test_http_404_is_a_site_defect(self):
        event = self.log.record(phase="crawl", operation="fetch", message="HTTP 404", http_status=404)
        self.assertEqual(event.category, "website_defect")

    def test_dns_failure_is_transient(self):
        event = self.log.record(phase="crawl", operation="fetch", exc=ConnectionError("getaddrinfo failed"))
        self.assertEqual(event.category, "transient_network")

    def test_parser_failure_is_our_limitation(self):
        event = self.log.record(phase="parse", operation="decode",
                                exc=UnicodeDecodeError("utf-8", b"", 0, 1, "bad byte"))
        self.assertEqual(event.category, "auditor_limitation")

    def test_missing_credentials_is_a_configuration_error(self):
        event = self.log.record(phase="llm", operation="semantic",
                                message="ANTHROPIC_API_KEY is not set; credential missing")
        self.assertEqual(event.category, "configuration_error")
        self.assertFalse(event.retryable)

    def test_provider_outage_is_a_provider_failure(self):
        event = self.log.record(phase="llm", operation="semantic", message="service unavailable")
        self.assertEqual(event.category, "provider_failure")

    def test_stack_traces_and_paths_are_scrubbed(self):
        event = self.log.record(phase="crawl", operation="fetch",
                                message="OSError at C:\\adobe\\secret\\run.py: key sk-ant-abcdefghijklmnop")
        self.assertNotIn("C:\\adobe", event.message_sanitized)
        self.assertNotIn("sk-ant-abcdefghijklmnop", event.message_sanitized)


class TestRecoveryPolicy(unittest.TestCase):
    def setUp(self):
        self.log = ErrorLog()

    def test_allowlist_is_closed(self):
        self.assertFalse(recovery_is_allowed("transient_network", "RUN_SHELL_COMMAND"))
        self.assertFalse(recovery_is_allowed("transient_network", "DROP_TABLE"))
        self.assertTrue(recovery_is_allowed("transient_network", "RETRY_TRANSIENT"))

    def test_recovery_must_suit_the_category(self):
        self.assertFalse(recovery_is_allowed("auditor_limitation", "ABORT_AUDIT"))
        self.assertTrue(recovery_is_allowed("configuration_error", "ABORT_AUDIT"))

    def test_llm_cannot_turn_our_failure_into_a_site_defect(self):
        event = self.log.record(phase="parse", operation="decode", message="weird")
        apply_diagnosis(event, {"category": "website_defect", "recovery_code": "SKIP_PAGE_AND_CONTINUE"}, self.log)
        self.assertEqual(event.category, "auditor_limitation")

    def test_non_retryable_failure_is_never_retried(self):
        event = self.log.record(phase="llm", operation="semantic", message="invalid api key credential")
        outcome = apply_diagnosis(event, {"category": "configuration_error",
                                          "recovery_code": "RETRY_TRANSIENT"}, self.log)
        self.assertIn("refused", outcome)
        self.assertIsNone(event.recovery_code)

    def test_retry_budget_is_bounded(self):
        for _ in range(MAX_GENERAL_RETRIES):
            self.assertTrue(self.log.may_retry())
            self.log.note_retry()
        self.assertFalse(self.log.may_retry())

    def test_every_allowlisted_code_maps_to_at_least_one_category(self):
        from lib.llm.errors import CATEGORY_RECOVERIES
        allowed = set().union(*CATEGORY_RECOVERIES.values())
        self.assertEqual(set(RECOVERY_ALLOWLIST) - allowed, set())


class TestAuditHealth(unittest.TestCase):
    def test_clean_run_is_complete(self):
        health = AuditHealth(pages_discovered=3, pages_requested=12, pages_analyzed=3)
        self.assertEqual(health.status(), "complete")

    def test_a_site_with_broken_links_is_still_a_complete_audit(self):
        # A 404 on one of the site's own links is a measurement about the site,
        # not a gap in our coverage.
        log = ErrorLog()
        log.record(phase="crawl", operation="fetch", message="HTTP 404",
                   http_status=404, url="http://x/missing")
        health = AuditHealth(pages_discovered=3, pages_requested=12, pages_analyzed=3,
                             errors=log.as_list())
        self.assertEqual(health.status(), "complete")

    def test_a_small_site_is_not_partial_merely_for_being_small(self):
        health = AuditHealth(pages_discovered=2, pages_requested=12, pages_analyzed=2)
        self.assertEqual(health.status(), "complete")

    def test_no_pages_is_failed(self):
        self.assertEqual(AuditHealth(pages_analyzed=0).status(), "failed")

    def test_a_crawl_failure_makes_the_run_partial(self):
        log = ErrorLog()
        log.record(phase="crawl", operation="fetch", exc=TimeoutError("timed out"), url="http://x/p")
        health = AuditHealth(pages_discovered=5, pages_requested=12, pages_analyzed=4, errors=log.as_list())
        self.assertEqual(health.status(), "partial")

    def test_a_failed_skill_makes_the_run_partial(self):
        health = AuditHealth(pages_analyzed=3, skills_failed=["entity-freshness-audit"])
        self.assertEqual(health.status(), "partial")

    def test_limitation_notes_exclude_site_defects(self):
        log = ErrorLog()
        log.record(phase="crawl", operation="fetch", message="HTTP 404", http_status=404, url="http://x/gone")
        log.record(phase="crawl", operation="fetch", exc=TimeoutError("timed out"), url="http://x/slow")
        notes = limitation_notes(log)
        self.assertEqual(len(notes), 1)
        self.assertIn("slow", notes[0])


class TestEngineFailurePaths(unittest.TestCase):
    """A model that fails must cost nothing but the enhancement itself."""

    def setUp(self):
        self.pack = pack_from_fixture("brochure")
        self.findings = [{"id": "MKT-EXTR-01", "category": "content_extraction",
                          "title": "t", "severity": "high", "evidence": "e",
                          "locations": ["http://fixture.test/"],
                          "suggested_action": {"summary": "original", "priority": "high"}}]

    def engine(self, responses):
        return HybridEngine(fake_config(), fake_responses=responses)

    def test_timeout_yields_no_suggestions_and_no_exception(self):
        engine = self.engine({"suggestion": LLMError("timeout", "timed out", retryable=True)})
        self.assertEqual(engine.enhance_suggestions(self.findings, self.pack), {})
        self.assertEqual(engine.telemetry.error_count, 1)

    def test_rate_limit_is_recorded_not_raised(self):
        engine = self.engine({"semantic": LLMError("rate_limited", "429", retryable=True)})
        observations, pages = engine.analyse_semantics(self.pack)
        self.assertEqual(observations, [])
        self.assertEqual(engine.telemetry.calls[0].error_kind, "rate_limited")

    def test_provider_unavailable_falls_back_to_deterministic_only(self):
        engine = self.engine({"semantic": LLMError("invalid_credentials", "no key")})
        engine.analyse_semantics(self.pack)
        self.assertIn("USE_DETERMINISTIC_ONLY", engine.telemetry.fallbacks)

    def test_oversized_payload_notes_the_split_fallback(self):
        engine = HybridEngine(fake_config(max_input_tokens=600), fake_responses={"semantic": {"observations": []}})
        engine.analyse_semantics(self.pack)
        self.assertIn("SPLIT_LLM_PAYLOAD", engine.telemetry.fallbacks)

    def test_unparseable_json_is_a_transport_failure_not_a_repair(self):
        # A body that is not JSON at all never reaches the validator, so there
        # is nothing to repair: it is recorded as an invalid response and the
        # deterministic result stands.
        engine = self.engine({"semantic": "this is not json"})
        observations, _ = engine.analyse_semantics(self.pack)
        self.assertEqual(observations, [])
        self.assertEqual(engine.telemetry.calls[0].error_kind, "invalid_response")
        self.assertEqual(engine.telemetry.status(), "failed")

    def test_schema_violation_gets_exactly_one_repair_attempt(self):
        from .helpers import first_evidence_ids, observation_payload
        good_ids = first_evidence_ids(self.pack, 1)
        engine = self.engine({
            "semantic": observation_payload(evidence_refs=["P999-S999"]),
            "semantic_repair": observation_payload(evidence_refs=good_ids),
        })
        observations, _ = engine.analyse_semantics(self.pack)
        self.assertEqual(len(observations), 1)
        self.assertEqual(engine.telemetry.repairs, 1)
        self.assertEqual(engine.telemetry.calls[-1].validation, "repaired")

    def test_failed_repair_falls_back_to_deterministic_output(self):
        from .helpers import observation_payload
        engine = self.engine({
            "semantic": observation_payload(evidence_refs=["P999-S999"]),
            "semantic_repair": observation_payload(evidence_refs=["P888-S888"]),
        })
        observations, _ = engine.analyse_semantics(self.pack)
        self.assertEqual(observations, [])
        self.assertEqual(engine.telemetry.repairs, 1)
        self.assertIn("repair_failed", engine.telemetry.fallbacks)

    def test_a_disabled_engine_makes_no_calls_at_all(self):
        from lib.llm.config import LLMConfig
        engine = HybridEngine(LLMConfig.off())
        self.assertFalse(engine.active)
        self.assertEqual(engine.enhance_suggestions(self.findings, self.pack), {})
        self.assertEqual(engine.analyse_semantics(self.pack), ([], []))
        self.assertEqual(engine.diagnose_errors(ErrorLog()), [])
        self.assertEqual(engine.telemetry.call_count, 0)


if __name__ == "__main__":
    unittest.main()
