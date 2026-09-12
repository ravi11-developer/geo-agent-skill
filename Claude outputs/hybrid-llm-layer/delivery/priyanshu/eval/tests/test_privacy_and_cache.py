#!/usr/bin/env python3
"""Redaction, cache keys and the safe-metadata guarantee."""

from __future__ import annotations

import os
import tempfile
import unittest

from .helpers import fake_config, pack_from_fixture

from lib.llm.cache import ResponseCache, cache_key, snapshot_hash
from lib.llm.config import LLMConfig
from lib.llm.observability import LLMTelemetry
from lib.llm.provider import LLMRequest, LLMResponse
from lib.llm.redaction import (
    FORBIDDEN_HEADERS, contains_secret, redact_headers, redact_mapping,
    redact_text, scrub_exception,
)


class TestRedaction(unittest.TestCase):
    def test_api_keys_are_removed(self):
        for secret in ("sk-ant-api03-abcdefghijklmnop", "sk-abcdefghijklmnopqrstuvwx",
                       "AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz",
                       "xoxb-1234567890-abcdefg"):
            cleaned, removed = redact_text(f"the key is {secret} ok")
            self.assertNotIn(secret, cleaned, secret)
            self.assertTrue(removed)

    def test_jwt_and_bearer_tokens_are_removed(self):
        text = "Authorization: Bearer abcdefghijklmnopqrs and eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJ"
        cleaned, _ = redact_text(text)
        self.assertNotIn("SflKxwRJ", cleaned)
        self.assertNotIn("abcdefghijklmnopqrs", cleaned)

    def test_personal_data_is_removed(self):
        cleaned, removed = redact_text("write to priya@example.com or call 98765 43210")
        self.assertNotIn("priya@example.com", cleaned)
        self.assertIn("email", removed)

    def test_card_numbers_are_removed_but_ordinary_numbers_survive(self):
        cleaned, _ = redact_text("card 4111 1111 1111 1111 costs 7499 rupees over 30 days")
        self.assertNotIn("4111", cleaned)
        self.assertIn("7499", cleaned)
        self.assertIn("30", cleaned)

    def test_credential_headers_are_dropped_entirely(self):
        safe = redact_headers({"Authorization": "Bearer abc", "Cookie": "s=1",
                               "X-Api-Key": "k", "Content-Type": "text/html"})
        self.assertEqual(set(safe), {"content-type"})
        for name in ("authorization", "cookie", "x-api-key"):
            self.assertIn(name, FORBIDDEN_HEADERS)

    def test_sensitive_keys_are_dropped_from_mappings(self):
        safe = redact_mapping({"password": "hunter2", "form": {"token": "abc", "city": "Jaipur"}})
        self.assertEqual(safe["password"], "[REDACTED:sensitive_key]")
        self.assertEqual(safe["form"]["token"], "[REDACTED:sensitive_key]")
        self.assertEqual(safe["form"]["city"], "Jaipur")

    def test_contains_secret_is_the_pre_send_guard(self):
        self.assertTrue(contains_secret("sk-ant-abcdefghijklmnop"))
        self.assertFalse(contains_secret("returns within 30 days"))

    def test_exception_scrubbing_removes_paths_and_secrets(self):
        cleaned = scrub_exception("Traceback: /home/user/app/run.py failed with key sk-ant-abcdefghijkl")
        self.assertNotIn("/home/user", cleaned)
        self.assertNotIn("sk-ant-abcdefghijkl", cleaned)

    def test_evidence_pack_never_carries_raw_html(self):
        pack = pack_from_fixture("brochure")
        blob = str(pack.as_dict())
        for marker in ("<script", "<div", "<html", "</p>"):
            self.assertNotIn(marker, blob)


class TestCacheKey(unittest.TestCase):
    def setUp(self):
        self.request = LLMRequest("semantic", "system", "user")

    def test_key_changes_with_every_component(self):
        base = fake_config()
        key = cache_key(base, "snap", self.request)
        self.assertNotEqual(key, cache_key(base, "other-snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(model="m2"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(provider="other"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(mode="semantic_shadow"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(prompt_version="9"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(schema_version="9"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base.with_overrides(taxonomy_version="9"), "snap", self.request))
        self.assertNotEqual(key, cache_key(base, "snap", LLMRequest("verifier", "system", "user")))

    def test_key_is_stable_for_identical_input(self):
        base = fake_config()
        self.assertEqual(cache_key(base, "snap", self.request), cache_key(base, "snap", self.request))

    def test_snapshot_hash_is_content_derived(self):
        first = snapshot_hash([("http://x/", 200, "aaa"), ("http://x/b", 200, "bbb")])
        reordered = snapshot_hash([("http://x/b", 200, "bbb"), ("http://x/", 200, "aaa")])
        changed = snapshot_hash([("http://x/", 200, "aaa"), ("http://x/b", 200, "ccc")])
        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed)


class TestCacheBehaviour(unittest.TestCase):
    def test_memory_cache_serves_a_second_lookup(self):
        config = fake_config()
        cache = ResponseCache(config)
        key = cache_key(config, "snap", LLMRequest("semantic", "s", "u"))
        self.assertIsNone(cache.get(key, "semantic"))
        cache.put(key, LLMResponse("semantic", {"observations": []}))
        hit = cache.get(key, "semantic")
        self.assertIsNotNone(hit)
        self.assertTrue(hit.cached)
        self.assertEqual(cache.stats.hits, 1)

    def test_disk_cache_survives_a_new_process_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = fake_config(cache_dir=tmp)
            key = cache_key(config, "snap", LLMRequest("semantic", "s", "u"))
            ResponseCache(config).put(key, LLMResponse("semantic", {"a": 1}))
            fresh = ResponseCache(config)
            self.assertEqual(fresh.get(key, "semantic").data, {"a": 1})

    def test_cache_disabled_never_stores(self):
        config = fake_config(cache_enabled=False)
        cache = ResponseCache(config)
        key = cache_key(config, "snap", LLMRequest("semantic", "s", "u"))
        cache.put(key, LLMResponse("semantic", {"a": 1}))
        self.assertIsNone(cache.get(key, "semantic"))

    def test_unwritable_cache_directory_degrades_to_a_miss(self):
        config = fake_config(cache_dir=os.path.join(os.sep, "definitely", "not", "writable", "xyz"))
        cache = ResponseCache(config)
        key = cache_key(config, "snap", LLMRequest("semantic", "s", "u"))
        cache.put(key, LLMResponse("semantic", {"a": 1}))
        self.assertIsNotNone(cache.get(key, "semantic"))  # memory layer still works


class TestObservability(unittest.TestCase):
    def test_telemetry_records_identity_without_the_key(self):
        config = LLMConfig.from_env({"LLM_ENABLED": "true", "LLM_MODE": "full",
                                     "LLM_PROVIDER": "fake", "LLM_MODEL": "fake-model",
                                     "ANTHROPIC_API_KEY": "sk-ant-topsecretvalue1234"})
        telemetry = LLMTelemetry(config)
        telemetry.record_response(LLMResponse("semantic", {}, input_tokens=10, output_tokens=5))
        record = telemetry.as_dict()
        self.assertNotIn("topsecret", str(record))
        self.assertEqual(record["mode"], "full")
        self.assertEqual(record["input_tokens"], 10)
        self.assertIn("prompt_version", record)
        self.assertIn("schema_version", record)
        self.assertIn("taxonomy_version", record)

    def test_status_reflects_what_happened(self):
        config = fake_config()
        self.assertEqual(LLMTelemetry(LLMConfig.off()).status(), "disabled")
        self.assertEqual(LLMTelemetry(config).status(), "skipped")
        telemetry = LLMTelemetry(config)
        telemetry.record_response(LLMResponse("semantic", {}))
        self.assertEqual(telemetry.status(), "completed")


if __name__ == "__main__":
    unittest.main()
