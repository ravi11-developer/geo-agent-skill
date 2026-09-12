#!/usr/bin/env python3
"""Build the submission package from the live agent tree.

The agent lives at ``eval/agents/marketplace/`` with each skill's executable at
the skill root, which is what the benchmark loader expects.  The submission
package wants the agentskills.io progressive-disclosure layout, with executables
under ``scripts/``.  Keeping two hand-maintained copies is how they drift, so
this script derives one from the other:

    python eval/agents/marketplace/tools/build_submission.py            # ../../../marketplace_submission
    python eval/agents/marketplace/tools/build_submission.py --out DIR --zip

It rewrites every ``entrypoint`` in ``marketplace.json`` and in each
``skill.json`` to the new path, copies only what belongs in a submission, then
runs the package validator and reports the packed size against the 50MB limit.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile

AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(AGENT_ROOT, "..", "..", ".."))
DEFAULT_OUT = os.path.join(REPO_ROOT, "marketplace_submission")
MAX_BYTES = 50 * 1024 * 1024

# Copied verbatim from the agent root.
TOP_LEVEL_FILES = ("run.py", "__init__.py", "requirements.txt", "README.md")
TOP_LEVEL_DIRS = ("lib", "schema", "examples", "tools")

# Never shipped: caches, results, editor droppings, local LLM caches.
EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", ".pytest_cache", ".mypy_cache",
                "node_modules", "llm_cache", "results", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".orig", ".rej", ".swp")
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", ".env", ".env.local", "secrets.json"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    dropped = set()
    for name in names:
        if name in EXCLUDE_DIRS or name in EXCLUDE_NAMES:
            dropped.add(name)
        elif name.endswith(EXCLUDE_SUFFIXES):
            dropped.add(name)
    return dropped


def build(out_dir: str) -> dict:
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    for name in TOP_LEVEL_FILES:
        source = os.path.join(AGENT_ROOT, name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(out_dir, name))
    for name in TOP_LEVEL_DIRS:
        source = os.path.join(AGENT_ROOT, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(out_dir, name), ignore=_ignore)

    with open(os.path.join(AGENT_ROOT, "marketplace.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)

    for entry in manifest.get("skills", []):
        skill_id = entry["id"]
        source_dir = os.path.join(AGENT_ROOT, entry["path"])
        target_dir = os.path.join(out_dir, entry["path"])
        os.makedirs(os.path.join(target_dir, "scripts"), exist_ok=True)

        for name in sorted(os.listdir(source_dir)):
            source = os.path.join(source_dir, name)
            if name in EXCLUDE_DIRS or name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES):
                continue
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(target_dir, name),
                                ignore=_ignore, dirs_exist_ok=True)
            elif name.endswith(".py"):
                shutil.copy2(source, os.path.join(target_dir, "scripts", name))
            else:
                shutil.copy2(source, os.path.join(target_dir, name))

        entrypoint = entry.get("entrypoint", "")
        if entrypoint and not entrypoint.startswith("scripts/"):
            entry["entrypoint"] = f"scripts/{os.path.basename(entrypoint)}"

        skill_json = os.path.join(target_dir, "skill.json")
        if os.path.exists(skill_json):
            with open(skill_json, encoding="utf-8") as handle:
                data = json.load(handle)
            data["entrypoint"] = entry["entrypoint"]
            with open(skill_json, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)

        init_path = os.path.join(target_dir, "scripts", "__init__.py")
        if not os.path.exists(init_path):
            open(init_path, "w", encoding="utf-8").close()

    with open(os.path.join(out_dir, "marketplace.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    return manifest


def measure(root: str) -> tuple[int, int]:
    total = files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            total += os.path.getsize(os.path.join(dirpath, name))
            files += 1
    return total, files


def make_zip(out_dir: str) -> str:
    archive = out_dir.rstrip(os.sep) + ".zip"
    if os.path.exists(archive):
        os.remove(archive)
    base = os.path.basename(out_dir.rstrip(os.sep))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(out_dir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                zf.write(full, os.path.join(base, os.path.relpath(full, out_dir)))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--zip", action="store_true", help="also write <out>.zip")
    parser.add_argument("--skip-validate", action="store_true")
    args = parser.parse_args()

    manifest = build(args.out)
    total, files = measure(args.out)
    print(f"built {args.out}")
    print(f"  skills     : {len(manifest['skills'])}, entrypoint {manifest['entrypoint']!r}")
    print(f"  files      : {files}")
    print(f"  unpacked   : {total / 1e6:.2f} MB of {MAX_BYTES / 1e6:.0f} MB")

    status = 0
    if not args.skip_validate:
        validator = os.path.join(args.out, "tools", "validate_package.py")
        if os.path.exists(validator):
            print("\n--- package validator -------------------------------------------")
            completed = subprocess.run([sys.executable, validator, "--strict"],
                                       cwd=args.out, capture_output=True, text=True)
            print(completed.stdout.strip() or completed.stderr.strip())
            status = completed.returncode

    if args.zip:
        archive = make_zip(args.out)
        size = os.path.getsize(archive)
        print(f"\n  zip        : {archive} ({size / 1e6:.2f} MB)")
        if size > MAX_BYTES:
            print(f"  FAIL: zip exceeds the {MAX_BYTES / 1e6:.0f} MB submission limit")
            status = status or 1
        else:
            print(f"  OK: within the {MAX_BYTES / 1e6:.0f} MB submission limit")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
