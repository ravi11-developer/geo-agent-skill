#!/usr/bin/env python3
"""Manifest-driven skill loader.

The marketplace is *data*, not hard-wired imports: ``marketplace.json`` lists
the installed skills, and this loader resolves each one to a Python entrypoint
at run time.  Adding, removing or reordering a skill therefore requires no code
change in the orchestrator - which is what makes this a marketplace rather than
a monolith with four functions.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

MARKETPLACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(MARKETPLACE_ROOT, "marketplace.json")

# ``lib`` must be importable by the dynamically loaded skill modules.
if MARKETPLACE_ROOT not in sys.path:
    sys.path.insert(0, MARKETPLACE_ROOT)


@dataclass
class InstalledSkill:
    id: str
    name: str
    version: str
    kind: str
    path: str
    entrypoint: str
    provides: list[str]
    consumes: list[str]
    produces: list[str]
    run: Callable[[dict[str, Any]], Any]
    metadata: dict[str, Any]


def load_manifest(manifest_path: str | None = None) -> dict[str, Any]:
    with open(manifest_path or MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _load_module(skill_id: str, module_path: str):
    spec = importlib.util.spec_from_file_location(f"marketplace_skill_{skill_id.replace('-', '_')}", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load skill module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_skills(manifest: dict[str, Any] | None = None, kinds: tuple[str, ...] = ("audit",)) -> list[InstalledSkill]:
    """Load every enabled skill of the requested kind, in manifest order."""
    manifest = manifest or load_manifest()
    skills: list[InstalledSkill] = []

    for entry in manifest.get("skills", []):
        if not entry.get("enabled", True):
            continue
        if kinds and entry.get("kind") not in kinds:
            continue

        skill_dir = os.path.join(MARKETPLACE_ROOT, entry["path"])
        module_path = os.path.join(skill_dir, entry["entrypoint"])
        module = _load_module(entry["id"], module_path)
        runner = getattr(module, "run", None)
        if runner is None:
            raise ImportError(f"skill {entry['id']} exposes no run(context) function")

        skills.append(
            InstalledSkill(
                id=entry["id"],
                name=entry.get("name", entry["id"]),
                version=entry.get("version", "0.0.0"),
                kind=entry.get("kind", "audit"),
                path=entry["path"],
                entrypoint=entry["entrypoint"],
                provides=list(entry.get("provides", [])),
                consumes=list(entry.get("consumes", [])),
                produces=list(entry.get("produces", [])),
                run=runner,
                metadata=entry,
            )
        )

    return _order_by_dependency(skills)


def _order_by_dependency(skills: list[InstalledSkill]) -> list[InstalledSkill]:
    """Topologically order skills so producers run before consumers."""
    ordered: list[InstalledSkill] = []
    remaining = list(skills)
    available: set[str] = set()

    while remaining:
        progressed = False
        for skill in list(remaining):
            if all(dep in available for dep in skill.consumes):
                ordered.append(skill)
                available.update(skill.produces)
                remaining.remove(skill)
                progressed = True
        if not progressed:
            # Unsatisfiable dependency: run the rest in manifest order rather
            # than failing the whole audit.
            ordered.extend(remaining)
            break

    return ordered
