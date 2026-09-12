#!/usr/bin/env python3
"""Agent discovery for the benchmark.

An agent is a directory under ``agents/`` containing an ``agent.json``.  The
bench finds them by scanning, so adding or removing a candidate is a filesystem
operation, not a code edit -- which is what the previous hard-coded
``__import__(f"eval.agents.{name}.run")`` made impossible.

Each agent runs in its own subprocess.  That is not incidental: every agent is a
self-contained marketplace with its own ``lib`` package, and importing two of
them into one interpreter would collide on module names.  The subprocess
boundary is also what lets the bench measure an agent exactly as a judge would
invoke it -- ``python run.py <url> <out.json>``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(REPO_ROOT, "agents")


@dataclass
class Agent:
    id: str
    name: str
    root: str
    entrypoint: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def run_script(self) -> str:
        return os.path.join(self.root, self.entrypoint)

    @property
    def skills(self) -> list[str]:
        manifest = os.path.join(self.root, "marketplace.json")
        if not os.path.exists(manifest):
            return []
        with open(manifest, encoding="utf-8") as fh:
            return [s["id"] for s in json.load(fh).get("skills", [])]

    def run(self, url: str, timeout: int = 300) -> dict[str, Any]:
        """Invoke the agent the way a judge would. Never raises."""
        out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, self.run_script, url, out],
                capture_output=True, text=True, timeout=timeout,
            )
            elapsed = time.monotonic() - started
            if proc.returncode != 0:
                return {"agent": self.id, "url": url, "report": None,
                        "runtime_seconds": round(elapsed, 3),
                        "error": f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"}
            with open(out, encoding="utf-8") as fh:
                report = json.load(fh)
            return {"agent": self.id, "url": url, "report": report,
                    "runtime_seconds": round(elapsed, 3), "error": None}
        except subprocess.TimeoutExpired:
            return {"agent": self.id, "url": url, "report": None,
                    "runtime_seconds": round(time.monotonic() - started, 3),
                    "error": f"timeout after {timeout}s"}
        except Exception as exc:  # noqa: BLE001
            return {"agent": self.id, "url": url, "report": None,
                    "runtime_seconds": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if os.path.exists(out):
                os.unlink(out)


def discover(agents_dir: str = AGENTS_DIR) -> dict[str, Agent]:
    """Every agent on disk, keyed by id, in directory order."""
    found: dict[str, Agent] = {}
    if not os.path.isdir(agents_dir):
        return found
    for entry in sorted(os.listdir(agents_dir)):
        manifest_path = os.path.join(agents_dir, entry, "agent.json")
        if not os.path.exists(manifest_path):
            continue
        with open(manifest_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        agent_id = meta.get("id", entry)
        found[agent_id] = Agent(
            id=agent_id,
            name=meta.get("name", agent_id),
            root=os.path.join(agents_dir, entry),
            entrypoint=meta.get("entrypoint", "run.py"),
            metadata=meta,
        )
    return found


def load(agent_id: str) -> Agent:
    agents = discover()
    if agent_id not in agents:
        raise ValueError(f"unknown agent {agent_id!r}; available: {sorted(agents)}")
    return agents[agent_id]


if __name__ == "__main__":
    for agent in discover().values():
        meta = agent.metadata
        print(f"{agent.id:<14} {meta.get('strategy', '?'):<16} "
              f"llm={str(meta.get('uses_llm')):<5} skills={len(agent.skills):<2} {agent.name}")
