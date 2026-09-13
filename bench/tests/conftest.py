"""Shared fixtures for bench/tests/.

This suite is evaluation-only: it lives under bench/, not agents/*/tools/, so
pytest and any judge-model dependency never enter the submission zip that
tools/validate_package.py size-checks.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS = ["A-precision", "B-coverage"]
SYNTHETIC_DIR = REPO_ROOT / "bench" / "sites" / "synthetic"
GOLD_DIR = REPO_ROOT / "bench" / "gold"
SYNTHETIC_PORT = 9700  # distinct from run_suite.py's 9500 and run_real_suite.py's 9600


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: hits the live internet, real sites")
    config.addinivalue_line("markers", "llm: needs ANTHROPIC_API_KEY, skipped otherwise")


@pytest.fixture(scope="session")
def synthetic_server():
    """One HTTP server for the whole test session, serving bench/sites/synthetic/*."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SYNTHETIC_DIR), **kwargs)

        def log_message(self, *a):  # noqa: A003 - silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", SYNTHETIC_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://localhost:{SYNTHETIC_PORT}"
    server.shutdown()


@pytest.fixture(scope="session")
def gold_standards() -> dict[str, dict]:
    golds = {}
    for path in sorted(GOLD_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        golds[data["site_id"]] = data
    return golds


def run_agent(agent: str, url: str, timeout: int = 300, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Invoke an agent exactly as the benchmark and a judge would: as a subprocess CLI.

    Mirrors bench/registry.py::Agent.run - kept separate here rather than
    imported, so this test suite has no import-time dependency on the
    benchmark harness beyond reading its gold files.
    """
    out_path = REPO_ROOT / "bench" / "results" / f"_pytest_tmp_{os.getpid()}_{abs(hash(url))}.json"
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agents" / agent / "run.py"), url, str(out_path)],
        capture_output=True, text=True, env=run_env, cwd=REPO_ROOT, timeout=timeout,
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"{agent} failed on {url}: {(proc.stderr or '')[-500:]}")
    report = json.loads(out_path.read_text(encoding="utf-8"))
    out_path.unlink(missing_ok=True)
    report["_runtime_wallclock"] = elapsed
    return report


@pytest.fixture(scope="session")
def agent_runner():
    """Exposes run_agent() to tests without a fragile cross-file import."""
    return run_agent


@pytest.fixture(scope="session")
def api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture
def require_api_key(api_key_available: bool) -> None:
    """Depend on this fixture to auto-skip a test when no judge/router key is set."""
    if not api_key_available:
        pytest.skip("ANTHROPIC_API_KEY not set - skipping LLM-backed test")
