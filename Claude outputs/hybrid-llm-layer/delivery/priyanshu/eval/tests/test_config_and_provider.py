#!/usr/bin/env python3
"""Feature flags, provider abstraction and the benchmark cost guard."""

from __future__ import annotations

import unittest

from .helpers import fake_config  # noqa: F401  (import side effect: sys.path)

from lib.llm.config import (
    CAP_ERROR_DIAGNOSIS, CAP_PROMOTION, CAP_SEMANTIC, CAP_SUGGESTIONS, CAP_VERIFIER,
    CrawlBudget, LLMConfig, MODES,
)
from lib.llm.provider import (
    AnthropicClient, DisabledClient, FakeClient, LLMError, LLMRequest,
    build_client, extract_json, unavailable_reason,
)


class TestConfig(unittest.TestCase):
    def test_default_is_off_and_needs_no_api_key(self):
        config = LLMConfig.from_env({})
        self.assertTrue(config.is_off)
        self.assertEqual(config.effective_mode, "off")
        self.assertEqual(config.capabilities, frozenset())
        self.assertFalse(config.api_key_present)

    def test_enabled_false_pins_mode_to_off(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "false", "LLM_MODE": "full"})
        self.assertEqual(config.effective_mode, "off")

    def test_env_mode_alone_does_not_enable(self):
        # A stray LLM_MODE in a shell must never start spending money.
        config = LLMConfig.from_env({"LLM_MODE": "full"})
        self.assertTrue(config.is_off)

    def test_capability_sets_per_mode(self):
        expected = {
            "off": set(),
            "suggestions_only": {CAP_SUGGESTIONS},
            "semantic_shadow": {CAP_SEMANTIC},
            "semantic_enabled": {CAP_SUGGESTIONS, CAP_SEMANTIC, CAP_PROMOTION},
            "full": {CAP_SUGGESTIONS, CAP_SEMANTIC, CAP_PROMOTION, CAP_VERIFIER,
                     CAP_ERROR_DIAGNOSIS, "recovery_selection"},
        }
        for mode in MODES:
            config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": mode})
            self.assertEqual(set(config.capabilities), expected[mode], mode)

    def test_shadow_mode_cannot_promote_or_enhance(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "semantic_shadow"})
        self.assertTrue(config.has(CAP_SEMANTIC))
        self.assertFalse(config.has(CAP_PROMOTION))
        self.assertFalse(config.has(CAP_SUGGESTIONS))

    def test_unknown_mode_falls_back_to_off(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "turbo"})
        self.assertEqual(config.effective_mode, "off")

    def test_thresholds_read_from_env(self):
        config = LLMConfig.from_env({
            "LLM_ENABLED": "true", "LLM_MODE": "full",
            "LLM_CONFIDENCE_THRESHOLD": "0.9", "LLM_MAX_RETRIES": "3",
            "LLM_TIMEOUT_SECONDS": "12", "LLM_VERIFIER_ENABLED": "false",
        })
        self.assertEqual(config.confidence_threshold, 0.9)
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.timeout_seconds, 12.0)
        self.assertFalse(config.verifier_enabled)

    def test_no_model_identifier_is_hard_coded(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "full"})
        self.assertEqual(config.model, "")
        self.assertIsNotNone(unavailable_reason(config))
        config = config.with_overrides(api_key_present=True)
        self.assertIn("LLM_MODEL", unavailable_reason(config) or "")

    def test_api_key_never_appears_in_the_logged_view(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "full",
                                     "ANTHROPIC_API_KEY": "sk-ant-secretvalue123456"})
        self.assertNotIn("secretvalue", str(config.as_dict()))
        self.assertTrue(config.as_dict()["api_key_present"])

    def test_benchmark_guard_refuses_paid_provider(self):
        env = {"LLM_ENABLED": "true", "LLM_MODE": "full", "LLM_PROVIDER": "anthropic",
               "LLM_MODEL": "some-model", "ANTHROPIC_API_KEY": "sk-ant-x" * 4}
        self.assertTrue(LLMConfig.from_env(env).effective_mode == "full")
        self.assertTrue(LLMConfig.for_benchmark(env).is_off)

    def test_benchmark_guard_can_be_opened_explicitly(self):
        env = {"LLM_ENABLED": "true", "LLM_MODE": "full", "LLM_PROVIDER": "anthropic",
               "LLM_MODEL": "some-model", "ANTHROPIC_API_KEY": "sk-ant-x" * 4,
               "LLM_ALLOW_BENCHMARK_CALLS": "true"}
        self.assertFalse(LLMConfig.for_benchmark(env).is_off)

    def test_benchmark_guard_leaves_the_fake_provider_alone(self):
        env = {"LLM_ENABLED": "true", "LLM_MODE": "full", "LLM_PROVIDER": "fake"}
        self.assertEqual(LLMConfig.for_benchmark(env).effective_mode, "full")


class TestCrawlBudget(unittest.TestCase):
    def test_legacy_is_the_default_and_matches_shipped_behaviour(self):
        budget = CrawlBudget.from_env({})
        self.assertEqual(budget.profile, "legacy")
        self.assertEqual(budget.hard_page_limit, 12)
        self.assertIsNone(budget.max_depth)

    def test_extended_profile_is_opt_in(self):
        budget = CrawlBudget.from_env({"AUDIT_CRAWL_PROFILE": "extended"})
        self.assertEqual(budget.soft_page_target, 20)
        self.assertEqual(budget.hard_page_limit, 30)
        self.assertEqual(budget.max_depth, 2)
        self.assertEqual(budget.targeted_depth, 3)

    def test_semantic_page_window(self):
        budget = CrawlBudget.from_env({})
        self.assertEqual(budget.semantic_page_target, 10)
        self.assertEqual(budget.max_semantic_pages, 15)

    def test_individual_overrides(self):
        budget = CrawlBudget.from_env({"AUDIT_HARD_PAGE_LIMIT": "7", "AUDIT_MAX_DEPTH": "1"})
        self.assertEqual(budget.hard_page_limit, 7)
        self.assertEqual(budget.max_depth, 1)


class TestProvider(unittest.TestCase):
    def test_off_mode_builds_a_disabled_client(self):
        client = build_client(LLMConfig.off())
        self.assertIsInstance(client, DisabledClient)
        self.assertFalse(client.available)
        with self.assertRaises(LLMError):
            client.complete_json(LLMRequest("semantic", "s", "u"))

    def test_anthropic_without_key_degrades_to_disabled(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "full",
                                     "LLM_PROVIDER": "anthropic", "LLM_MODEL": "m"})
        self.assertIsInstance(build_client(config), DisabledClient)

    def test_fake_client_returns_scripted_json(self):
        client = build_client(fake_config(), fake_responses={"semantic": {"observations": []}})
        self.assertIsInstance(client, FakeClient)
        self.assertEqual(client.complete_json(LLMRequest("semantic", "s", "u")).data, {"observations": []})

    def test_fake_client_can_script_a_sequence(self):
        client = build_client(fake_config(), fake_responses={"semantic": ["not json at all", {"observations": []}]})
        with self.assertRaises(LLMError):
            client.complete_json(LLMRequest("semantic", "s", "u"))
        self.assertEqual(client.complete_json(LLMRequest("semantic", "s", "u")).data, {"observations": []})

    def test_call_budget_is_enforced(self):
        client = build_client(fake_config(max_calls=2), fake_responses={"*": {"ok": True}})
        client.complete_json(LLMRequest("a", "s", "u"))
        client.complete_json(LLMRequest("a", "s", "u"))
        with self.assertRaises(LLMError) as caught:
            client.complete_json(LLMRequest("a", "s", "u"))
        self.assertEqual(caught.exception.kind, "budget_exhausted")

    def test_oversized_payload_is_refused_before_it_is_sent(self):
        client = build_client(fake_config(max_input_tokens=600), fake_responses={"*": {}})
        with self.assertRaises(LLMError) as caught:
            client.complete_json(LLMRequest("a", "s", "u" * 40000))
        self.assertEqual(caught.exception.kind, "payload_too_large")
        self.assertEqual(client.calls, 0)

    def test_secret_in_payload_is_refused_before_it_is_sent(self):
        client = build_client(fake_config(), fake_responses={"*": {}})
        with self.assertRaises(LLMError):
            client.complete_json(LLMRequest("a", "s", "token sk-ant-abcdefghijklmnop"))
        self.assertEqual(client.calls, 0)

    def test_json_extraction_survives_fences_and_prose(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure!\n{"a": [1, 2]}\nHope that helps'), {"a": [1, 2]})
        with self.assertRaises(LLMError):
            extract_json("no json here")

    def test_provider_errors_are_normalised_not_leaked(self):
        class Boom(Exception):
            status_code = 429

        error = AnthropicClient._normalise_error(Boom("slow down"))
        self.assertEqual(error.kind, "rate_limited")
        self.assertTrue(error.retryable)

        class Auth(Exception):
            status_code = 401

        error = AnthropicClient._normalise_error(Auth("bad key"))
        self.assertEqual(error.kind, "invalid_credentials")
        self.assertFalse(error.retryable)


if __name__ == "__main__":
    unittest.main()
