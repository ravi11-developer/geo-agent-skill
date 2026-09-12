#!/usr/bin/env python3
"""Shared fixtures for the marketplace test suite.

Every test runs offline: local HTML fixtures are served from a background
thread, and the LLM layer always uses :class:`FakeClient`.  Nothing here needs
an API key, and nothing here can spend money - which is the point, because a
test suite that needs a paid provider is a test suite nobody runs.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
import threading
from typing import Any

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(TESTS_DIR)
PROJECT_ROOT = os.path.dirname(EVAL_DIR)
MARKETPLACE_ROOT = os.path.join(EVAL_DIR, "agents", "marketplace")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
SYNTHETIC = os.path.join(EVAL_DIR, "sites", "synthetic")

for path in (MARKETPLACE_ROOT, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from lib.contracts import Page, SiteSnapshot  # noqa: E402
from lib.llm.config import LLMConfig  # noqa: E402
from lib.llm.evidence import build_evidence_pack  # noqa: E402


# ---------------------------------------------------------------------------
# Local fixture server
# ---------------------------------------------------------------------------

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - silence the test run
        pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FixtureServer:
    """Serves a directory on an ephemeral port for the life of a `with` block."""

    def __init__(self, directory: str):
        self.directory = directory
        self._server: _Server | None = None

    def __enter__(self) -> str:
        handler = lambda *a, **k: _Quiet(*a, directory=self.directory, **k)  # noqa: E731
        self._server = _Server(("127.0.0.1", 0), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def serve(name: str) -> FixtureServer:
    """Serve one of the bundled fixture sites by directory name."""
    directory = os.path.join(FIXTURES, name)
    if not os.path.isdir(directory):
        directory = os.path.join(SYNTHETIC, name)
    return FixtureServer(directory)


# ---------------------------------------------------------------------------
# Snapshots and packs without any network at all
# ---------------------------------------------------------------------------

def page_from_file(path: str, url: str, *, is_entry: bool = False, depth: int = 0) -> Page:
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    return Page(url=url, status_code=200, content_type="text/html; charset=utf-8",
                html=html, is_entry=is_entry, depth=depth)


def snapshot_from_fixture(name: str, base: str = "http://fixture.test") -> SiteSnapshot:
    """Build a SiteSnapshot straight from disk - no server, no requests."""
    directory = os.path.join(FIXTURES, name)
    files = sorted(os.listdir(directory))
    ordered = ["index.html"] + [f for f in files if f != "index.html" and f.endswith(".html")]
    pages = []
    for position, filename in enumerate(ordered):
        full = os.path.join(directory, filename)
        if not os.path.exists(full):
            continue
        url = f"{base}/" if filename == "index.html" else f"{base}/{filename}"
        pages.append(page_from_file(full, url, is_entry=(position == 0), depth=0 if position == 0 else 1))
    return SiteSnapshot(base_url=base, entry_url=f"{base}/", pages=pages)


def pack_from_fixture(name: str):
    return build_evidence_pack(snapshot_from_fixture(name))


# ---------------------------------------------------------------------------
# LLM configuration helpers
# ---------------------------------------------------------------------------

def fake_config(mode: str = "full", **overrides: Any) -> LLMConfig:
    """A config wired to the fake provider, with the real thresholds intact."""
    base = {"mode": mode, "provider": "fake", "model": "fake-model", "enabled": True}
    base.update(overrides)
    return LLMConfig.from_env({}, base)


def observation_payload(**fields: Any) -> dict[str, Any]:
    """A semantic response that passes validation, unless a field is overridden."""
    observation = {
        "title": "Return eligibility language leaves the outcome unresolved",
        "aspect": "returns",
        "sentiment": "negative",
        "emotion": "uncertainty",
        "severity": "medium",
        "source_kind": "brand_copy",
        "analysis_type": "predicted_visitor_friction",
        "confidence": 0.88,
        "quote": "",
        "evidence": "Eligibility and exclusions are described as conditional without stating either.",
        "evidence_refs": [],
        "suggested_action": {"summary": "State eligibility, deadline, exclusions and the next action separately.",
                             "validation": "Each condition can be extracted independently."},
    }
    observation.update(fields)
    return {"observations": [observation]}


def suggestion_payload(finding_id: str, **fields: Any) -> dict[str, Any]:
    suggestion = {
        "finding_id": finding_id,
        "root_cause": "The fact exists only in a form a text extractor cannot read.",
        "recommendation": "Restate the fact as HTML text directly beneath the heading.",
        "where": "the entry page",
        "implementation_steps": ["Add the fact as a paragraph", "Re-run the audit and confirm it is extractable"],
        "expected_impact": "The fact becomes quotable by an answer engine.",
        "effort": "low",
        "owner": "content",
        "acceptance_test": "The fact appears in the raw HTML response body.",
        "evidence_refs": [],
    }
    suggestion.update(fields)
    return {"suggestions": [suggestion]}


def first_evidence_ids(pack, count: int = 2) -> list[str]:
    ids = list(pack.sections_by_id)
    return ids[:count]


def ids_on_different_pages(pack, count: int = 2) -> list[str]:
    """Evidence ids drawn from distinct pages, for corroboration tests."""
    chosen: list[str] = []
    seen_pages: set[str] = set()
    for page in pack.pages:
        if not page.sections:
            continue
        if page.page_id in seen_pages:
            continue
        chosen.append(page.sections[0].evidence_id)
        seen_pages.add(page.page_id)
        if len(chosen) == count:
            break
    return chosen
