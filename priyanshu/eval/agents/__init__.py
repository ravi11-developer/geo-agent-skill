#!/usr/bin/env python3
"""Common agent interface for the benchmarking framework.

Every agent must implement the `run_audit(url) -> dict` interface.
The returned dict must conform to the Adobe Round 3 report schema.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any

# Agent registry: maps agent name to module path
AGENT_REGISTRY = {
    "baseline": "eval.agents.baseline.run",
    "deterministic": "eval.agents.deterministic.run",
    "reasoning": "eval.agents.reasoning.run",
    "specialist": "eval.agents.specialist.run",
    "hybrid": "eval.agents.hybrid.run",
}


def get_agent(agent_name: str):
    """Load and return an agent module by name."""
    if agent_name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent: {agent_name}. Available: {list(AGENT_REGISTRY.keys())}")
    module_path = AGENT_REGISTRY[agent_name]
    return importlib.import_module(module_path)


def run_agent_with_metrics(agent_name: str, url: str, timeout: int = 300) -> dict[str, Any]:
    """Run an agent and capture output + metrics.

    Returns a dict with:
        - agent: agent name
        - url: target URL
        - report: the agent's audit report (or None on failure)
        - runtime_seconds: how long the agent took
        - error: error message if agent failed
        - started_at: ISO timestamp
        - finished_at: ISO timestamp
    """
    result = {
        "agent": agent_name,
        "url": url,
        "report": None,
        "runtime_seconds": 0.0,
        "error": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }

    try:
        agent_module = get_agent(agent_name)
        start = time.monotonic()
        report = agent_module.run_audit(url)
        elapsed = time.monotonic() - start
        result["report"] = report
        result["runtime_seconds"] = round(elapsed, 2)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        result["runtime_seconds"] = round(time.monotonic() - start, 2) if 'start' in dir() else 0.0
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()

    return result
