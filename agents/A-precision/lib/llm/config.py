"""Capability names and a config that can only ever resolve to 'off'."""

from __future__ import annotations

from typing import Any, Mapping

CAP_SEMANTIC = "semantic"
CAP_SUGGESTIONS = "suggestions"
CAP_PROMOTION = "promotion"
CAP_VERIFIER = "verifier"
CAP_ERROR_DIAGNOSIS = "error_diagnosis"

MODE_OFF = "off"


class CrawlBudget:
    """The shipped crawl scope. Mirrors the 'legacy' profile of the full layer."""

    soft_page_target = 12
    hard_page_limit = 12
    max_depth = None
    targeted_depth = None
    targeted_additions = 0
    expansion_loops = 0
    semantic_page_target = 0
    semantic_page_maximum = 0


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
