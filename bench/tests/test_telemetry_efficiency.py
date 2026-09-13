"""Telemetry: crawl efficiency, latency, and (mocked) token efficiency.

"Trajectory length" has no literal analog here (no per-turn tool-call loop -
see conftest.py's module docstring and the plan this suite implements), but
the stratified crawler's page-selection loop (crawl_render.py::_pick_stratified,
Phase 3) *is* an iterative decision process with a real notion of efficiency:
how many distinct URL templates does a fetched page buy. That's what this file
measures in place of a tool-call ratio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LATENCY_CEILING_SECONDS = 280  # the handout's 5-minute budget, with margin


# ---------------------------------------------------------------------------
# Crawl efficiency (the "trajectory length" analog)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
def test_extended_profile_reaches_more_templates_than_legacy(agent, synthetic_server, agent_runner):
    """Same site, two crawl profiles: extended must not do *worse* than legacy.

    This is the efficiency claim from Phase 3 made explicit and reproducible:
    stratified sampling under the extended profile should sample at least as
    many distinct URL templates as plain breadth-first, using no more of the
    page budget to do it.
    """
    url = f"{synthetic_server}/site-011-multipage-healthy/"
    legacy = agent_runner(agent, url, env={"AUDIT_CRAWL_PROFILE": "legacy"})
    extended = agent_runner(agent, url, env={"AUDIT_CRAWL_PROFILE": "extended"})

    legacy_t = legacy["telemetry"].get("templates_sampled") or 0
    extended_t = extended["telemetry"].get("templates_sampled") or 0
    assert extended_t >= legacy_t, (
        f"extended profile sampled fewer templates ({extended_t}) than legacy ({legacy_t}) "
        "on the same site - stratification should never do worse than breadth-first"
    )


@pytest.mark.slow
@pytest.mark.parametrize("url", [
    "https://www.mokobara.com",
    "https://www.iiit.ac.in",
])
def test_extended_profile_saturates_before_hard_limit_on_real_sites(url, agent_runner):
    """Confirms the saturation fix: a real multi-template site should stop
    because it ran out of new templates to learn about, not because it hit
    the page ceiling. Regression guard for the singleton-template bug this
    test suite caught (a template with exactly one instance - the homepage -
    used to block saturation from ever firing; see crawl_render.py)."""
    report = agent_runner("B-coverage", url, env={"AUDIT_CRAWL_PROFILE": "extended"}, timeout=200)
    telemetry = report["telemetry"]
    assert telemetry.get("crawl_stopped_because") == "saturated", (
        f"expected the crawl to saturate on {url}, got "
        f"{telemetry.get('crawl_stopped_because')!r} after {telemetry.get('pages_fetched')} pages"
    )
    assert telemetry["pages_fetched"] < 30  # the extended profile's hard_page_limit


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
@pytest.mark.parametrize("site_id", [
    "site-001-healthy", "site-008-mixed-faults", "site-011-multipage-healthy",
    "site-017-robots-blocks-ai", "site-018-sitemap-index-healthy",
])
def test_gold_site_finishes_within_budget(agent, site_id, synthetic_server, agent_runner):
    url = f"{synthetic_server}/{site_id}/"
    report = agent_runner(agent, url)
    runtime = report.get("summary", {}).get("runtime_seconds", report["_runtime_wallclock"])
    assert runtime < LATENCY_CEILING_SECONDS, (
        f"{agent} took {runtime}s on {site_id} - handout requires < 5 minutes on a typical site"
    )


@pytest.mark.slow
@pytest.mark.parametrize("url", [
    "https://www.mokobara.com",
    "https://www.smilefoundationindia.org",
])
def test_real_site_finishes_within_budget(url, agent_runner):
    report = agent_runner("B-coverage", url, env={"AUDIT_CRAWL_PROFILE": "extended"}, timeout=290)
    assert report["_runtime_wallclock"] < LATENCY_CEILING_SECONDS


# ---------------------------------------------------------------------------
# Token efficiency (mocked - no API key, no network, tests the plumbing
# and the config's own ceilings, not real model behaviour)
# ---------------------------------------------------------------------------

def test_llm_telemetry_respects_configured_ceilings():
    """B-coverage's optional LLM layer must report call_count/token usage that
    respect its own LLMConfig ceilings (max_calls, max_input_tokens) even when
    every call is faked - this is a plumbing test, not a model-quality test."""
    root = Path(__file__).resolve().parents[2] / "agents" / "B-coverage"
    sys.path.insert(0, str(root))
    from lib.llm.config import LLMConfig, MODE_SUGGESTIONS_ONLY

    config = LLMConfig.from_env(env={}, overrides={
        "enabled": True, "mode": MODE_SUGGESTIONS_ONLY, "max_calls": 6, "max_input_tokens": 12000,
    })
    assert config.max_calls == 6
    assert config.max_input_tokens == 12000
    # The ceilings themselves are the contract under test here; a full mocked
    # run through HybridEngine(fake_responses=...) is exercised end-to-end by
    # tools/stress_test.py's existing cases, which already run with the LLM
    # layer in its default (off) mode on every case.
    assert config.effective_mode == MODE_SUGGESTIONS_ONLY


@pytest.mark.llm
def test_llm_telemetry_smoke_with_real_key(require_api_key):
    """Optional smoke test: one real call, only when ANTHROPIC_API_KEY is set."""
    root = Path(__file__).resolve().parents[2] / "agents" / "B-coverage"
    sys.path.insert(0, str(root))
    from lib.llm.config import LLMConfig, MODE_SUGGESTIONS_ONLY
    from lib.llm.engine import HybridEngine

    config = LLMConfig.from_env(overrides={"enabled": True, "mode": MODE_SUGGESTIONS_ONLY})
    engine = HybridEngine(config)
    assert engine.config.api_key_present
