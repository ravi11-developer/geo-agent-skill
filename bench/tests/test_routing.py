"""Skill selection & parameter extraction.

This system has no internal tool-routing loop: the orchestrator always runs
every installed skill in the fixed order marketplace.json declares (see
conftest.py's module docstring and bench/registry.py). The one real routing
decision in this system happens one layer up, exactly where RUNNING.md
documents the intended usage: a general-purpose AI agent reads this skill's
SKILL.md and decides whether a user's request means "invoke this skill", then
extracts the URL parameter from prose. test_skill_routing_accuracy simulates
that host-agent decision with a small model. test_marketplace_wiring_matches_orchestrator
is the cheap, no-LLM half: a config/wiring regression guard, not a routing eval.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

MOCK_QUERIES = json.loads((Path(__file__).parent / "fixtures" / "mock_queries.json").read_text())


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path if path != '/' else ''}"


# ---------------------------------------------------------------------------
# 1b. Internal wiring - marketplace.json vs what the orchestrator actually runs
# ---------------------------------------------------------------------------

_WIRING_CHECK_SCRIPT = """
import json, sys
from lib.loader import load_manifest, load_skills
loaded = load_skills(load_manifest(), kinds=("audit",))
print(json.dumps([s.id for s in loaded]))
"""


@pytest.mark.parametrize("agent", ["A-precision", "B-coverage"])
def test_marketplace_wiring_matches_orchestrator(agent):
    """The declared skill sequence must be exactly what load_skills(kind='audit')
    would run, in the same order - a wiring regression guard, not a routing eval,
    since the sequence is 100% static (see lib/loader.py::load_skills).

    Run as a subprocess, not an in-process import: A-precision and B-coverage
    both define a top-level `lib` package, and Python caches the first one it
    imports in sys.modules - a second in-process import silently reuses it and
    tries to load one agent's skills through the other's loader. This is
    exactly the collision bench/registry.py's own docstring calls out as the
    reason every agent invocation in this repo is a subprocess.
    """
    import subprocess

    root = REPO_ROOT / "agents" / agent
    manifest = json.loads((root / "marketplace.json").read_text())

    entrypoints = [s for s in manifest["skills"] if s.get("entrypoint")]
    assert len(entrypoints) == 1, "exactly one entrypoint skill is a hard submission requirement"
    assert entrypoints[0]["id"] == manifest["entrypoint"]

    declared_audit_skills = [s["id"] for s in manifest["skills"]
                             if s.get("enabled", True) and s.get("kind") == "audit"]

    proc = subprocess.run([sys.executable, "-c", _WIRING_CHECK_SCRIPT],
                          cwd=root, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    actual_order = json.loads(proc.stdout.strip())

    assert actual_order == declared_audit_skills, (
        f"orchestrator would run {actual_order}, marketplace.json declares {declared_audit_skills}"
    )


# ---------------------------------------------------------------------------
# 1a. Skill selection & parameter extraction, at the host-agent layer
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = """You are deciding whether to invoke ONE installed skill, described below by its \
SKILL.md. Given a user's message, decide (a) whether this skill is the right one to invoke for that message, \
and (b) if so, what URL to pass it as input.

Respond with ONLY a JSON object, no other text: {{"invoke": true|false, "url": "<url or null>"}}

If invoking, the url must be a fully-qualified URL (add "https://" to a bare domain). If the message does not \
clearly ask to audit, check, or review a specific website's AI visibility/discoverability/citation behavior, \
set invoke to false and url to null - do not invoke this skill for general questions, coding help, or requests \
about unrelated topics, even if they mention a URL incidentally.

--- SKILL.md ---
{skill_md}
--- end SKILL.md ---

User message: {query}"""


def _ask_router(skill_md: str, query: str) -> dict:
    import anthropic  # local import: only needed when this test actually runs

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": ROUTING_SYSTEM_PROMPT.format(skill_md=skill_md, query=query)}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


@pytest.mark.llm
def test_skill_routing_accuracy(require_api_key):
    """Accuracy over the mock query set: invocation decision + exact-match URL
    extraction, mirroring `assert agent.tool_calls[0].name == "..."` from a
    conventional tool-routing eval, adapted to a skill that carries its own
    routing metadata in SKILL.md rather than living in a fixed tool registry."""
    skill_md = (REPO_ROOT / "agents" / "B-coverage" / "skills" / "audit-orchestrator" / "SKILL.md").read_text()

    invoke_correct = 0
    url_correct = 0
    url_applicable = 0
    failures = []

    for case in MOCK_QUERIES:
        result = _ask_router(skill_md, case["query"])
        invoke_ok = result.get("invoke") == case["expect_invoke"]
        invoke_correct += invoke_ok
        if case["expect_invoke"]:
            url_applicable += 1
            if _normalize_url(result.get("url")) == _normalize_url(case["expect_url"]):
                url_correct += 1
            elif invoke_ok:
                failures.append(f"URL mismatch on {case['query']!r}: got {result.get('url')!r}")
        if not invoke_ok:
            failures.append(f"invoke mismatch on {case['query']!r}: got {result}")

    invoke_accuracy = invoke_correct / len(MOCK_QUERIES)
    url_accuracy = url_correct / url_applicable if url_applicable else 1.0
    print(f"\nrouting invocation accuracy: {invoke_accuracy:.0%} ({invoke_correct}/{len(MOCK_QUERIES)})")
    print(f"URL extraction accuracy:    {url_accuracy:.0%} ({url_correct}/{url_applicable})")

    assert invoke_accuracy >= 0.9, "\n".join(failures)
    assert url_accuracy >= 0.9, "\n".join(failures)
