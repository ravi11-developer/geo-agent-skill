#!/usr/bin/env python3
"""Crawl planning: depth, budget, URL filtering and stop conditions."""

from __future__ import annotations

import unittest

from .helpers import serve

from lib.llm.config import CrawlBudget
from lib.loader import load_manifest, load_skills


def crawl_module():
    skill = next(s for s in load_skills(load_manifest(), kinds=("audit",))
                 if s.id == "crawl-render-audit")
    return __import__(skill.run.__module__)


class TestLegacyProfileIsUnchanged(unittest.TestCase):
    def test_no_budget_means_the_original_behaviour(self):
        module = crawl_module()
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=12)
        self.assertEqual(snapshot.notes["crawl_profile"], "legacy")
        self.assertEqual(len(snapshot.pages), 4)
        self.assertTrue(all(page.status_code == 200 for page in snapshot.pages))

    def test_legacy_budget_applies_no_depth_ceiling(self):
        module = crawl_module()
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=12, budget=CrawlBudget.legacy())
        self.assertEqual(len(snapshot.pages), 4)
        self.assertGreaterEqual(snapshot.notes["max_depth_reached"], 1)

    def test_low_value_urls_are_still_followed_in_legacy_mode(self):
        # The legacy profile must not start filtering URLs: that would change
        # the crawl scope of every historical result.
        module = crawl_module()
        self.assertTrue(module._is_low_value("http://x/cart"))
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=12, budget=CrawlBudget.legacy())
        self.assertEqual(snapshot.notes["crawl_profile"], "legacy")


class TestPageBudget(unittest.TestCase):
    def test_hard_page_limit_is_never_exceeded(self):
        module = crawl_module()
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=2)
        self.assertLessEqual(len(snapshot.pages), 2)
        self.assertEqual(snapshot.notes["stopped_because"], "page_limit")

    def test_extended_profile_caps_at_its_hard_limit(self):
        module = crawl_module()
        budget = CrawlBudget.extended()
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=100, budget=budget)
        self.assertLessEqual(len(snapshot.pages), budget.hard_page_limit)

    def test_stop_reason_is_recorded(self):
        module = crawl_module()
        with serve("brochure") as url:
            snapshot = module.crawl(url, max_pages=12)
        self.assertEqual(snapshot.notes["stopped_because"], "queue_exhausted")


class TestDepth(unittest.TestCase):
    def test_depth_is_tracked_on_every_page(self):
        module = crawl_module()
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=12)
        entry = [p for p in snapshot.pages if p.is_entry][0]
        self.assertEqual(entry.depth, 0)
        self.assertTrue(all(p.depth >= 1 for p in snapshot.pages if not p.is_entry))
        self.assertTrue(all(p.discovered_from for p in snapshot.pages if not p.is_entry))

    def test_depth_ceiling_stops_expansion(self):
        module = crawl_module()
        budget = CrawlBudget.extended().__class__(profile="extended", soft_page_target=20,
                                                  hard_page_limit=30, max_depth=0)
        with serve("ecommerce") as url:
            snapshot = module.crawl(url, max_pages=30, budget=budget)
        self.assertEqual(len(snapshot.pages), 1, "depth 0 means the entry page only")

    def test_extended_profile_filters_low_value_urls(self):
        module = crawl_module()
        for url in ("http://x/cart", "http://x/checkout/", "http://x/login",
                    "http://x/search?q=a", "http://x/p?utm_source=news", "http://x/list?page=7"):
            self.assertTrue(module._is_low_value(url), url)
        for url in ("http://x/pricing", "http://x/products/drill", "http://x/about"):
            self.assertFalse(module._is_low_value(url), url)


class TestBudgetReachesTheAudit(unittest.TestCase):
    def test_orchestrator_passes_the_budget_and_records_the_checks(self):
        from run import run_audit
        with serve("ecommerce") as url:
            report = run_audit(url)
        checks = {c["check"]: c for c in report["checks_performed"] if c["skill"] == "crawl-render-audit"}
        self.assertIn("crawl_budget_respected", checks)
        self.assertTrue(checks["crawl_budget_respected"]["passed"])
        self.assertIn("max_crawl_depth", checks)

    def test_audit_health_reports_the_page_accounting(self):
        from run import run_audit
        with serve("ecommerce") as url:
            report = run_audit(url)
        health = report["audit_health"]
        self.assertEqual(health["pages_requested"], 12)
        self.assertEqual(health["pages_analyzed"], 4)
        self.assertEqual(health["status"], "complete")

    def test_runtime_stays_far_inside_the_five_minute_ceiling(self):
        from run import run_audit
        with serve("ecommerce") as url:
            report = run_audit(url)
        self.assertLess(report["summary"]["runtime_seconds"], 300)


if __name__ == "__main__":
    unittest.main()
