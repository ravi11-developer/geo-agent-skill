"""Evidence packing exists only to feed a model, so here it does nothing."""

from __future__ import annotations

from typing import Any


def build_evidence_pack(snapshot: Any = None, budget: Any = None) -> None:  # noqa: ARG001
    return None


def select_semantic_pages(snapshot: Any = None, budget: Any = None) -> list:  # noqa: ARG001
    return []
