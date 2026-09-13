"""Capability names and a config that can only ever resolve to 'off'."""

from __future__ import annotations

import os
from typing import Any, Mapping

CAP_SEMANTIC = "semantic"
CAP_SUGGESTIONS = "suggestions"
CAP_PROMOTION = "promotion"
CAP_VERIFIER = "verifier"
CAP_ERROR_DIAGNOSIS = "error_diagnosis"

MODE_OFF = "off"


class CrawlBudget:
    """The shipped crawl scope.

    Crawl planning is pure arithmetic over URLs - no model is involved - so this
    build gets the same stratified profile as the full layer. It lives under
    ``lib/llm`` only because that is where the configuration object already sat.

    ``AUDIT_CRAWL_PROFILE=legacy`` restores plain breadth-first over 12 pages.
    """

    profile = "extended"
    soft_page_target = 16
    hard_page_limit = 30
    max_depth = 3
    targeted_depth = 3
    targeted_additions = 5
    expansion_loops = 1
    per_template_samples = 3
    saturation_window = 6
    semantic_page_target = 0
    semantic_page_maximum = 0
    total_budget_seconds = 300.0
    explore_deadline_seconds = 210.0

    def __init__(self) -> None:
        if str(os.environ.get("AUDIT_CRAWL_PROFILE", "")).strip().lower() == "legacy":
            self.profile = "legacy"
            self.soft_page_target = 12
            self.hard_page_limit = 12
            self.max_depth = None


class NullLLMConfig:
    """Always off. `has()` answers False for every capability."""

    enabled = False
    mode = MODE_OFF
    effective_mode = MODE_OFF

    def __init__(self) -> None:
        self.crawl = CrawlBudget()

    def has(self, capability: str) -> bool:  # noqa: ARG002
        return False

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset()


def resolve_config(config: Mapping[str, Any] | None = None) -> NullLLMConfig:  # noqa: ARG001
    return NullLLMConfig()
