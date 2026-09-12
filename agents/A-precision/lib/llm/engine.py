"""An engine that never runs a model.

Exposes the exact surface the orchestrator uses -- ``can``, ``config``,
``decide_promotions``, ``diagnose_errors``, ``enhance_suggestions``, ``finish``
-- so the orchestrator needs no edit, and every method takes the deterministic
path unconditionally.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .config import NullLLMConfig, resolve_config


class HybridEngine:
    def __init__(self, config: NullLLMConfig | None = None, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        self.config = config if isinstance(config, NullLLMConfig) else resolve_config()

    def can(self, capability: str) -> bool:  # noqa: ARG002
        return False

    def decide_promotions(self, observations: Sequence[Any] = (), *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """No observation is ever promoted: there is nothing to promote from."""
        return []

    def diagnose_errors(self, error_log: Any = None, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """Errors keep whatever the deterministic recovery already decided."""
        return None

    def enhance_suggestions(self, findings: Sequence[Any] = (), *args: Any, **kwargs: Any) -> list:  # noqa: ARG002
        """Suggested actions are returned exactly as the skills wrote them."""
        return list(findings)

    def finish(self, snapshot_id: str = "", *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {
            "status": "absent",
            "mode": "off",
            "reason": "this package ships no model layer",
            "calls": 0,
            "fallbacks_used": [],
            "warnings": [],
            "snapshot_id": snapshot_id,
        }
