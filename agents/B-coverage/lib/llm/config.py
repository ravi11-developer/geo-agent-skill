#!/usr/bin/env python3
"""Feature flags and configuration for the optional hybrid LLM layer.

The marketplace is deterministic by default.  Everything in :mod:`lib.llm` is
inert unless a mode is switched on explicitly, and the *only* way to switch it
on is configuration - never a code path that decides for itself.

Modes
-----
``off``                deterministic behaviour only; no LLM object is ever built.
``suggestions_only``   validated deterministic findings may get a richer
                       ``suggested_action``; findings themselves are frozen.
``semantic_shadow``    semantic observations are produced and reported under
                       ``observations``; they touch nothing score-bearing.
``semantic_enabled``   observations that survive the evidence gate may be
                       promoted to findings, and suggestions are enhanced.
``full``               everything above plus the verifier, AI error diagnosis
                       and allowlisted recovery selection.

Every capability is a named member of :data:`MODE_CAPABILITIES`, so a mode is a
*set of capabilities* rather than a magic string scattered through the code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Versioning (part of the cache key; bump when behaviour changes)
# ---------------------------------------------------------------------------

PROMPT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
TAXONOMY_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Modes and capabilities
# ---------------------------------------------------------------------------

MODE_OFF = "off"
MODE_SUGGESTIONS_ONLY = "suggestions_only"
MODE_SEMANTIC_SHADOW = "semantic_shadow"
MODE_SEMANTIC_ENABLED = "semantic_enabled"
MODE_FULL = "full"

MODES: tuple[str, ...] = (
    MODE_OFF,
    MODE_SUGGESTIONS_ONLY,
    MODE_SEMANTIC_SHADOW,
    MODE_SEMANTIC_ENABLED,
    MODE_FULL,
)

# Capability names used throughout the LLM layer.
CAP_SUGGESTIONS = "suggestions"
CAP_SEMANTIC = "semantic"
CAP_PROMOTION = "promotion"
CAP_VERIFIER = "verifier"
CAP_ERROR_DIAGNOSIS = "error_diagnosis"
CAP_RECOVERY_SELECTION = "recovery_selection"

MODE_CAPABILITIES: dict[str, frozenset[str]] = {
    MODE_OFF: frozenset(),
    MODE_SUGGESTIONS_ONLY: frozenset({CAP_SUGGESTIONS}),
    # Shadow mode deliberately excludes suggestion enhancement: it must leave
    # every score-bearing field (findings, severities, suggested_action) exactly
    # as the deterministic pipeline produced them, so the ablation isolates the
    # semantic analyser and nothing else.
    MODE_SEMANTIC_SHADOW: frozenset({CAP_SEMANTIC}),
    MODE_SEMANTIC_ENABLED: frozenset({CAP_SUGGESTIONS, CAP_SEMANTIC, CAP_PROMOTION}),
    MODE_FULL: frozenset({
        CAP_SUGGESTIONS, CAP_SEMANTIC, CAP_PROMOTION,
        CAP_VERIFIER, CAP_ERROR_DIAGNOSIS, CAP_RECOVERY_SELECTION,
    }),
}

# Providers that spend money.  Used by the benchmark guard.
PAID_PROVIDERS = frozenset({"anthropic"})

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled", ""}


def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _as_int(raw: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _as_float(raw: Any, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# ---------------------------------------------------------------------------
# Crawl budget (introduced as configuration; legacy defaults are preserved)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CrawlBudget:
    """Crawl planning limits.

    ``legacy`` reproduces the shipped behaviour byte for byte (12 pages, plain
    breadth-first, no depth ceiling) and is the default, so no existing test or
    benchmark result moves.  ``extended`` is the Round-3 budget described in the
    hybrid design and must be requested explicitly.
    """

    profile: str = "legacy"
    soft_page_target: int = 12
    hard_page_limit: int = 12
    max_depth: int | None = None
    targeted_depth: int | None = None
    targeted_additions: int = 0
    expansion_loops: int = 0
    semantic_page_target: int = 10
    max_semantic_pages: int = 15
    total_budget_seconds: float = 300.0
    explore_deadline_seconds: float = 255.0
    # Stratified sampling: how many samples of each URL template are enough, and
    # how many consecutive fetches without a new template count as saturated.
    per_template_samples: int = 3
    saturation_window: int = 6

    @classmethod
    def legacy(cls) -> "CrawlBudget":
        return cls()

    @classmethod
    def extended(cls) -> "CrawlBudget":
        """The measured profile - see `bench/results/ksweep_*.csv` for the sweep.

        `hard_page_limit` sits at the knee of the findings-vs-pages curve rather
        than at a round number, and `explore_deadline_seconds` keeps the whole
        audit inside the handout's 5-minute ceiling even when every fetch is slow.
        """
        return cls(
            profile="extended",
            soft_page_target=16,
            hard_page_limit=30,
            max_depth=3,
            targeted_depth=3,
            targeted_additions=5,
            expansion_loops=1,
            per_template_samples=3,
            saturation_window=6,
            explore_deadline_seconds=210.0,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CrawlBudget":
        env = os.environ if env is None else env
        profile = str(env.get("AUDIT_CRAWL_PROFILE", "legacy")).strip().lower()
        base = cls.extended() if profile == "extended" else cls.legacy()
        raw_depth = env.get("AUDIT_MAX_DEPTH")
        # An absent variable keeps the profile's own default; only an explicit
        # value changes the depth ceiling, so `extended` stays depth-2.
        max_depth = (base.max_depth if raw_depth in (None, "")
                     else _as_int(raw_depth, base.max_depth or 2, 0, 10))
        return replace(
            base,
            soft_page_target=_as_int(env.get("AUDIT_SOFT_PAGE_TARGET"), base.soft_page_target, 1, 500),
            hard_page_limit=_as_int(env.get("AUDIT_HARD_PAGE_LIMIT"), base.hard_page_limit, 1, 500),
            max_depth=max_depth,
            per_template_samples=_as_int(env.get("AUDIT_PER_TEMPLATE_SAMPLES"), base.per_template_samples, 1, 20),
            saturation_window=_as_int(env.get("AUDIT_SATURATION_WINDOW"), base.saturation_window, 1, 50),
            semantic_page_target=_as_int(env.get("AUDIT_SEMANTIC_PAGE_TARGET"), base.semantic_page_target, 1, 50),
            max_semantic_pages=_as_int(env.get("AUDIT_MAX_SEMANTIC_PAGES"), base.max_semantic_pages, 1, 50),
            total_budget_seconds=_as_float(env.get("AUDIT_TOTAL_BUDGET_SECONDS"), base.total_budget_seconds, 5.0, 3600.0),
            explore_deadline_seconds=_as_float(env.get("AUDIT_EXPLORE_DEADLINE_SECONDS"), base.explore_deadline_seconds, 1.0, 3600.0),
        )


# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    """Immutable snapshot of the LLM feature configuration."""

    enabled: bool = False
    mode: str = MODE_OFF
    provider: str = "anthropic"
    model: str = ""              # never hard-coded: supplied by configuration
    timeout_seconds: float = 30.0
    max_retries: int = 1
    max_input_tokens: int = 12000
    max_output_tokens: int = 4000
    confidence_threshold: float = 0.80
    corroboration_threshold: float = 0.65
    verifier_enabled: bool = True
    cache_enabled: bool = True
    cache_dir: str | None = None
    max_calls: int = 6
    max_repairs: int = 1
    allow_benchmark_calls: bool = False
    api_key_present: bool = False
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    taxonomy_version: str = TAXONOMY_VERSION
    crawl: CrawlBudget = field(default_factory=CrawlBudget.legacy)

    # -- derived ---------------------------------------------------------
    @property
    def effective_mode(self) -> str:
        """The mode actually in force.

        ``LLM_ENABLED=false`` is a hard master switch: it pins the mode to
        ``off`` no matter what ``LLM_MODE`` says, so a stray environment
        variable can never turn on paid calls on its own.
        """
        if not self.enabled:
            return MODE_OFF
        return self.mode if self.mode in MODES else MODE_OFF

    @property
    def capabilities(self) -> frozenset[str]:
        return MODE_CAPABILITIES[self.effective_mode]

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def is_off(self) -> bool:
        return self.effective_mode == MODE_OFF

    @property
    def uses_paid_provider(self) -> bool:
        return self.provider.strip().lower() in PAID_PROVIDERS

    def with_overrides(self, **kwargs: Any) -> "LLMConfig":
        return replace(self, **kwargs)

    def cache_identity(self) -> tuple[str, ...]:
        """The configuration half of the cache key."""
        return (
            self.provider, self.model or "-", self.effective_mode,
            self.prompt_version, self.schema_version, self.taxonomy_version,
        )

    def as_dict(self) -> dict[str, Any]:
        """Safe-to-log view.  Never contains the API key."""
        return {
            "mode": self.effective_mode,
            "requested_mode": self.mode,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model or None,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_input_tokens": self.max_input_tokens,
            "confidence_threshold": self.confidence_threshold,
            "verifier_enabled": self.verifier_enabled,
            "cache_enabled": self.cache_enabled,
            "api_key_present": self.api_key_present,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "taxonomy_version": self.taxonomy_version,
            "crawl_profile": self.crawl.profile,
        }

    # -- construction ----------------------------------------------------
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None,
                 overrides: Mapping[str, Any] | None = None) -> "LLMConfig":
        """Build a config from environment variables, then per-run overrides.

        ``overrides`` is the ``config["llm"]`` dict a caller may pass to
        ``run_audit``; it wins over the environment so a benchmark harness can
        pin a mode without mutating the process environment.
        """
        env = os.environ if env is None else env
        overrides = dict(overrides or {})

        provider = str(overrides.get("provider", env.get("LLM_PROVIDER", "anthropic"))).strip().lower() or "anthropic"
        api_key = str(env.get("ANTHROPIC_API_KEY", "") or "").strip()

        mode = str(overrides.get("mode", env.get("LLM_MODE", MODE_OFF))).strip().lower()
        if mode not in MODES:
            mode = MODE_OFF

        enabled = _as_bool(overrides.get("enabled", env.get("LLM_ENABLED")), False)
        # A caller that names a working mode without setting LLM_ENABLED is
        # taken at its word only when it did so through `overrides` (an explicit
        # in-process decision); the environment always needs both.
        if "mode" in overrides and "enabled" not in overrides and mode != MODE_OFF:
            enabled = True

        config = cls(
            enabled=enabled,
            mode=mode,
            provider=provider,
            model=str(overrides.get("model", env.get("LLM_MODEL", ""))).strip(),
            timeout_seconds=_as_float(overrides.get("timeout_seconds", env.get("LLM_TIMEOUT_SECONDS")), 30.0, 1.0, 600.0),
            max_retries=_as_int(overrides.get("max_retries", env.get("LLM_MAX_RETRIES")), 1, 0, 5),
            max_input_tokens=_as_int(overrides.get("max_input_tokens", env.get("LLM_MAX_INPUT_TOKENS")), 12000, 500, 400000),
            max_output_tokens=_as_int(overrides.get("max_output_tokens", env.get("LLM_MAX_OUTPUT_TOKENS")), 4000, 256, 64000),
            confidence_threshold=_as_float(overrides.get("confidence_threshold", env.get("LLM_CONFIDENCE_THRESHOLD")), 0.80, 0.0, 1.0),
            corroboration_threshold=_as_float(overrides.get("corroboration_threshold", env.get("LLM_CORROBORATION_THRESHOLD")), 0.65, 0.0, 1.0),
            verifier_enabled=_as_bool(overrides.get("verifier_enabled", env.get("LLM_VERIFIER_ENABLED")), True),
            cache_enabled=_as_bool(overrides.get("cache_enabled", env.get("LLM_CACHE_ENABLED")), True),
            cache_dir=(overrides.get("cache_dir") or env.get("LLM_CACHE_DIR") or None),
            max_calls=_as_int(overrides.get("max_calls", env.get("LLM_MAX_CALLS")), 6, 1, 64),
            max_repairs=_as_int(overrides.get("max_repairs", env.get("LLM_MAX_REPAIRS")), 1, 0, 3),
            allow_benchmark_calls=_as_bool(env.get("LLM_ALLOW_BENCHMARK_CALLS"), False),
            api_key_present=bool(api_key),
            crawl=CrawlBudget.from_env(env),
        )
        return config

    @classmethod
    def off(cls) -> "LLMConfig":
        return cls()

    @classmethod
    def for_benchmark(cls, env: Mapping[str, str] | None = None,
                      overrides: Mapping[str, Any] | None = None) -> "LLMConfig":
        """Config for the large corpora (2,600 / 10,000 sites).

        A paid provider is refused here unless ``LLM_ALLOW_BENCHMARK_CALLS`` is
        explicitly true, so a benchmark can never quietly bill thousands of
        calls because someone left ``LLM_ENABLED=true`` in their shell.
        """
        config = cls.from_env(env, overrides)
        if config.uses_paid_provider and not config.allow_benchmark_calls:
            return config.with_overrides(enabled=False, mode=MODE_OFF)
        return config


def resolve_config(config: Mapping[str, Any] | None = None,
                   env: Mapping[str, str] | None = None) -> LLMConfig:
    """Resolve the LLM config for one audit run.

    ``config`` is the dict handed to ``run_audit``; ``config["llm"]`` may be an
    :class:`LLMConfig` already (tests do this) or a plain mapping of overrides.
    """
    raw = (config or {}).get("llm")
    if isinstance(raw, LLMConfig):
        return raw
    return LLMConfig.from_env(env, raw if isinstance(raw, Mapping) else None)
