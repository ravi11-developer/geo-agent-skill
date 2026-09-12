#!/usr/bin/env python3
"""Evidence-pack construction: cleaning, stable ids, classification, ranking."""

from __future__ import annotations

import unittest

from .helpers import pack_from_fixture, snapshot_from_fixture

from lib.contracts import Page, SiteSnapshot, is_evidence_id
from lib.llm.config import CrawlBudget
from lib.llm.evidence import (
    build_evidence_pack, classify_page, classify_source_kind,
    repeated_block_digests, select_semantic_pages, template_id,
)


class TestTextCleaning(unittest.TestCase):
    def setUp(self):
        self.pack = pack_from_fixture("brochure")
        self.text = " ".join(s.text for p in self.pack.pages for s in p.sections).lower()

    def test_scripts_and_styles_never_reach_the_pack(self):
        self.assertNotIn("schema.org", self.text)
        self.assertNotIn("<script", self.text)

    def test_cookie_banner_is_removed(self):
        self.assertNotIn("accept all cookies", self.text)

    def test_navigation_and_footer_chrome_is_removed(self):
        self.assertNotIn("© 2026", self.text)

    def test_page_substance_survives(self):
        self.assertIn("hand-thrown stoneware", self.text)
        self.assertIn("return", self.text)

    def test_repeated_blocks_are_detected_as_boilerplate(self):
        snapshot = snapshot_from_fixture("ecommerce")
        digests = repeated_block_digests(snapshot.ok_pages)
        self.assertIsInstance(digests, set)
        # "Get started today." appears on both product pages and must not be
        # offered twice as though it were distinct evidence.
        texts = [s.text for p in build_evidence_pack(snapshot).pages for s in p.sections]
        self.assertLessEqual(sum(1 for t in texts if t.strip() == "Get started today."), 1)

    def test_secrets_are_redacted_out_of_sections(self):
        page = Page(url="http://x/", status_code=200, is_entry=True, html=(
            "<html><body><main><h1>Contact</h1>"
            "<p>Write to sales@example.com or use key sk-ant-abcdefghijklmnopqrst to test the API. "
            "This paragraph is long enough to survive the minimum section length filter comfortably.</p>"
            "</main></body></html>"))
        pack = build_evidence_pack(SiteSnapshot(base_url="http://x", entry_url="http://x/", pages=[page]))
        blob = " ".join(s.text for p in pack.pages for s in p.sections)
        self.assertNotIn("sk-ant-abcdefghijklmnopqrst", blob)
        self.assertIn("[REDACTED:api_key]", blob)


class TestEvidenceIds(unittest.TestCase):
    def test_ids_are_well_formed_and_unique(self):
        pack = pack_from_fixture("brochure")
        ids = [s.evidence_id for p in pack.pages for s in p.sections]
        self.assertTrue(all(is_evidence_id(i) for i in ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_ids_are_stable_across_identical_builds(self):
        first = [s.evidence_id for p in pack_from_fixture("brochure").pages for s in p.sections]
        second = [s.evidence_id for p in pack_from_fixture("brochure").pages for s in p.sections]
        self.assertEqual(first, second)

    def test_snapshot_id_is_content_derived_and_stable(self):
        self.assertEqual(pack_from_fixture("brochure").snapshot_id,
                         pack_from_fixture("brochure").snapshot_id)
        self.assertNotEqual(pack_from_fixture("brochure").snapshot_id,
                            pack_from_fixture("ecommerce").snapshot_id)

    def test_section_resolves_back_to_its_page(self):
        pack = pack_from_fixture("brochure")
        for page in pack.pages:
            for section in page.sections:
                self.assertEqual(pack.page_of_section[section.evidence_id], page.page_id)


class TestClassification(unittest.TestCase):
    def test_page_types_from_url(self):
        pack = pack_from_fixture("brochure")
        types = {p.url.rsplit("/", 1)[-1] or "index": p.page_type for p in pack.pages}
        self.assertEqual(types["index"], "home")
        self.assertEqual(types["pricing.html"], "pricing")
        self.assertEqual(types["returns.html"], "shipping_returns")

    def test_entry_page_is_home(self):
        page = Page(url="http://x/", status_code=200, html="<html><body><p>hi</p></body></html>", is_entry=True)
        self.assertEqual(classify_page(page, "http://x/"), "home")

    def test_source_kind_detects_customer_voice(self):
        self.assertEqual(classify_source_kind("Reviews", "great drill", "div.customer-reviews"), "customer_voice")
        self.assertEqual(classify_source_kind("About us", "we make pots", "main"), "brand_copy")
        self.assertEqual(classify_source_kind("Invalid input", "Please try again.", "p.form-error"), "system_message")

    def test_duplicate_templates_share_a_template_id(self):
        snapshot = snapshot_from_fixture("ecommerce")
        by_url = {p.url: template_id(p) for p in snapshot.pages}
        product_ids = [tid for url, tid in by_url.items() if url.endswith(("p1.html", "p2.html"))]
        self.assertEqual(len(set(product_ids)), 1, "the two product pages use one template")


class TestSelection(unittest.TestCase):
    def test_selection_respects_the_maximum(self):
        pack = pack_from_fixture("ecommerce")
        chosen = select_semantic_pages(pack, CrawlBudget.legacy())
        self.assertLessEqual(len(chosen), CrawlBudget.legacy().max_semantic_pages)
        self.assertTrue(all(page.sections for page in chosen))

    def test_selection_prefers_high_value_page_types(self):
        pack = pack_from_fixture("brochure")
        chosen = select_semantic_pages(pack, CrawlBudget.legacy())
        self.assertEqual(chosen[0].page_type, "home")

    def test_pages_without_sections_are_never_selected(self):
        snapshot = snapshot_from_fixture("brochure")
        snapshot.pages.append(Page(url="http://fixture.test/empty.html", status_code=200,
                                   html="<html><body><nav>menu</nav></body></html>", depth=1))
        pack = build_evidence_pack(snapshot)
        chosen = select_semantic_pages(pack, CrawlBudget.legacy())
        self.assertNotIn("empty.html", " ".join(page.url for page in chosen))

    def test_only_retrievable_pages_contribute(self):
        snapshot = snapshot_from_fixture("brochure")
        snapshot.pages.append(Page(url="http://fixture.test/gone.html", status_code=404, html=""))
        pack = build_evidence_pack(snapshot)
        self.assertNotIn("http://fixture.test/gone.html", pack.known_urls)


if __name__ == "__main__":
    unittest.main()
