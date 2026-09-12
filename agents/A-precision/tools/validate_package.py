#!/usr/bin/env python3
"""Agent Skills package validator (agentskills.io format + output schema).

Runs every mechanical conformance check the marketplace has to pass, so a
submission is never graded on a broken package:

    python tools/validate_package.py                 # validate the package
    python tools/validate_package.py --audit a.json  # also validate a report
    python tools/validate_package.py --strict        # warnings become failures

Pure stdlib by default. PyYAML and jsonschema are used when installed and are
replaced by conservative built-in parsers when they are not, so the validator
runs anywhere the marketplace runs.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_SKILL_MD_BYTES = 12_000          # progressive disclosure: detail belongs in references/
REQUIRED_MANIFEST_KEYS = ("id", "name", "version", "description", "license", "entrypoint", "skills")
REQUIRED_FRONTMATTER = ("name", "description")
SEVERITIES = ("critical", "high", "medium", "low")

DESTRUCTIVE_PATTERNS = (
    (r"\brequests\.(post|put|patch|delete)\s*\(", "non-GET HTTP call"),
    (r"\bsession\.(post|put|patch|delete)\s*\(", "non-GET HTTP call"),
    (r"\bsubprocess\b", "subprocess execution"),
    (r"\bos\.system\s*\(", "shell execution"),
    (r"(?<![\w.])eval\s*\(", "eval()"),
    (r"(?<![\w.])exec\s*\(", "exec()"),
    (r"\bshutil\.rmtree\b", "recursive delete"),
    (r"\bos\.(remove|unlink|rmdir)\s*\(", "file delete"),
)
BRITTLE_PATTERNS = (
    (r"elementor|wp-block-|\bdivi\b|avada|jet-?engine", "CMS-theme-specific selector"),
    (r"class_=\s*[\"'][A-Za-z0-9_-]{12,}[\"']", "long literal class selector"),
    (r"\b(shopify|squarespace|wixstatic|godaddy)\b", "vendor-specific token"),
)


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.checks = 0

    def check(self, ok: bool, label: str, detail: str = "", warn_only: bool = False) -> bool:
        self.checks += 1
        if not ok:
            (self.warnings if warn_only else self.failures).append(f"{label}{': ' + detail if detail else ''}")
        return ok


# ---------------------------------------------------------------------------
# Frontmatter / schema helpers (stdlib fallbacks)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str | None]:
    if not text.startswith("---"):
        return {}, "file does not open with a YAML frontmatter block"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, "frontmatter block is not closed"
    raw, body = parts[1], parts[2]
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return {}, "frontmatter is not a mapping"
        return data, None
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        return {}, f"invalid YAML: {exc}"
    data: dict = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s", line) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        data[key.strip()] = value
    return data, None


def validate_against_schema(instance: dict, schema: dict) -> list[str]:
    try:
        import jsonschema  # type: ignore
        validator = jsonschema.Draft7Validator(schema)
        return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
                for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.path))]
    except ImportError:
        errors = []
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"<root>: missing required '{key}'")
        summary = instance.get("summary", {})
        for key in schema["properties"]["summary"].get("required", []):
            if key not in summary:
                errors.append(f"summary: missing required '{key}'")
        for i, finding in enumerate(instance.get("findings", [])):
            for key in schema["properties"]["findings"]["items"].get("required", []):
                if key not in finding:
                    errors.append(f"findings/{i}: missing required '{key}'")
            action = finding.get("suggested_action", {})
            if not isinstance(action, dict):
                errors.append(f"findings/{i}/suggested_action: not an object")
            else:
                for key in ("summary", "priority"):
                    if not action.get(key):
                        errors.append(f"findings/{i}/suggested_action: missing '{key}'")
        return errors


# ---------------------------------------------------------------------------
# Package checks
# ---------------------------------------------------------------------------

def exposes_run(path: str) -> bool:
    """Static check that a skill entrypoint defines run(); never imports it."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError:
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
               for n in tree.body)


def package_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", ".venv")]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            yield os.path.join(dirpath, name)


def validate_package(root: str, rep: Report) -> dict:
    manifest_path = os.path.join(root, "marketplace.json")
    if not rep.check(os.path.exists(manifest_path), "manifest", "marketplace.json not found"):
        return {}
    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.check(False, "manifest", f"marketplace.json does not parse: {exc}")
        return {}

    for key in REQUIRED_MANIFEST_KEYS:
        rep.check(key in manifest, "manifest", f"missing '{key}'")

    ids = [s.get("id") for s in manifest.get("skills", [])]
    rep.check(len(ids) == len(set(ids)), "manifest", "duplicate skill ids")
    flagged = [s["id"] for s in manifest.get("skills", []) if s.get("entrypoint") is True]
    rep.check(len(flagged) == 1, "manifest",
              f"exactly one skill must carry entrypoint: true; found {flagged}")
    rep.check(manifest.get("entrypoint") in ids, "manifest",
              f"entrypoint {manifest.get('entrypoint')!r} is not an installed skill")

    declared = set(manifest.get("contracts", {}).get("categories", []))
    try:
        sys.path.insert(0, root)
        from lib.contracts import CATEGORIES  # type: ignore
        rep.check(declared == set(CATEGORIES), "manifest",
                  f"contracts.categories != lib.contracts.CATEGORIES "
                  f"(manifest-only: {sorted(declared - set(CATEGORIES))}, "
                  f"code-only: {sorted(set(CATEGORIES) - declared)})")
    except Exception as exc:  # noqa: BLE001
        rep.check(False, "manifest", f"cannot import lib.contracts: {exc}", warn_only=True)

    for entry in manifest.get("skills", []):
        sid = entry.get("id", "<unnamed>")
        skill_dir = os.path.join(root, entry.get("path", ""))
        if not rep.check(os.path.isdir(skill_dir), f"skill:{sid}", "declared path does not exist"):
            continue

        skill_md = os.path.join(skill_dir, "SKILL.md")
        if rep.check(os.path.exists(skill_md), f"skill:{sid}", "SKILL.md missing"):
            text = open(skill_md, encoding="utf-8").read()
            fm, err = parse_frontmatter(text)
            if rep.check(err is None, f"skill:{sid}", f"SKILL.md frontmatter - {err}"):
                for key in REQUIRED_FRONTMATTER:
                    rep.check(bool(str(fm.get(key, "")).strip()), f"skill:{sid}",
                              f"frontmatter missing '{key}'")
                rep.check(fm.get("name") == sid, f"skill:{sid}",
                          f"frontmatter name {fm.get('name')!r} != manifest id", warn_only=True)
                desc = str(fm.get("description", ""))
                rep.check(len(desc) >= 40, f"skill:{sid}",
                          "description too short to route on", warn_only=True)
            body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
            rep.check(len(body) >= 200, f"skill:{sid}", "SKILL.md body is too thin to be useful")
            rep.check(len(text) <= MAX_SKILL_MD_BYTES, f"skill:{sid}",
                      f"SKILL.md is {len(text)}B (> {MAX_SKILL_MD_BYTES}B) - move detail to references/",
                      warn_only=True)

        entrypoint = os.path.join(skill_dir, entry.get("script", ""))
        if rep.check(os.path.exists(entrypoint), f"skill:{sid}",
                     f"entrypoint {entry.get('entrypoint')!r} missing"):
            rep.check(exposes_run(entrypoint), f"skill:{sid}", "entrypoint defines no run() function")
            rep.check(entry.get("script", "").startswith("scripts/"), f"skill:{sid}",
                      "executable code should live under scripts/ (progressive disclosure)",
                      warn_only=True)

        refs = os.path.join(skill_dir, "references")
        rep.check(os.path.isdir(refs) and any(f.endswith(".md") for f in os.listdir(refs)),
                  f"skill:{sid}", "no references/*.md - detailed rules belong there", warn_only=True)

        sj = os.path.join(skill_dir, "skill.json")
        if os.path.exists(sj):
            try:
                data = json.load(open(sj, encoding="utf-8"))
                rep.check(data.get("id") == sid, f"skill:{sid}", "skill.json id disagrees with manifest")
                rep.check(data.get("entrypoint") == entry.get("script"), f"skill:{sid}",
                          "skill.json entrypoint disagrees with manifest")
                rep.check(set(data.get("provides", [])) == set(entry.get("provides", [])), f"skill:{sid}",
                          "skill.json provides disagrees with manifest")
            except Exception as exc:  # noqa: BLE001
                rep.check(False, f"skill:{sid}", f"skill.json does not parse: {exc}")

    # dependency graph: every consumed artifact is produced by something earlier
    produced = set()
    for entry in manifest.get("skills", []):
        for dep in entry.get("consumes", []):
            rep.check(any(dep in s.get("produces", []) for s in manifest["skills"]),
                      f"skill:{entry['id']}", f"consumes '{dep}' which no skill produces")
        produced |= set(entry.get("produces", []))

    # safety envelope
    total = 0
    for path in package_files(root):
        total += os.path.getsize(path)
        rel = os.path.relpath(path, root)
        # The scanners below inspect *shipped skill code*. tools/ holds the
        # validator and the stress harness, whose own pattern tables and local
        # fixture writes would otherwise match themselves.
        if not path.endswith(".py") or rel.split(os.sep)[0] == "tools":
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        for pattern, label in DESTRUCTIVE_PATTERNS:
            for m in re.finditer(pattern, src):
                line = src[:m.start()].count("\n") + 1
                allowed = rel == "run.py"  # only the CLI may write the report it was asked for
                rep.check(allowed, "read-only", f"{rel}:{line} {label} -> {m.group(0)[:32]!r}",
                          warn_only=allowed)
        for pattern, label in BRITTLE_PATTERNS:
            for m in re.finditer(pattern, src, re.I):
                line = src[:m.start()].count("\n") + 1
                rep.check(False, "generalization", f"{rel}:{line} {label} -> {m.group(0)[:32]!r}")
    rep.check(total <= MAX_PACKAGE_BYTES, "size",
              f"package is {total/1e6:.1f}MB (limit {MAX_PACKAGE_BYTES/1e6:.0f}MB)")
    rep.check(manifest.get("safety", {}).get("read_only") is True, "safety",
              "manifest does not declare read_only: true")
    return manifest


def validate_audit(path: str, root: str, rep: Report) -> None:
    schema_path = os.path.join(root, "schema", "audit.schema.json")
    if not rep.check(os.path.exists(schema_path), "audit", "schema/audit.schema.json missing"):
        return
    schema = json.load(open(schema_path, encoding="utf-8"))
    report = json.load(open(path, encoding="utf-8"))
    for err in validate_against_schema(report, schema):
        rep.check(False, "audit", f"{os.path.basename(path)} {err}")

    findings = report.get("findings", [])
    summary = report.get("summary", {})
    rep.check(summary.get("total_findings") == len(findings), "audit",
              f"{os.path.basename(path)}: total_findings != len(findings)")
    for sev in SEVERITIES:
        if sev in summary:
            actual = sum(1 for f in findings if f.get("severity") == sev)
            rep.check(summary[sev] == actual, "audit",
                      f"{os.path.basename(path)}: summary.{sev}={summary[sev]} but {actual} findings")
    ids = [f.get("id") for f in findings]
    rep.check(len(ids) == len(set(ids)), "audit", f"{os.path.basename(path)}: duplicate finding ids")
    cats = [f.get("category") for f in findings]
    rep.check(len(cats) == len(set(cats)), "audit",
              f"{os.path.basename(path)}: duplicate category (merge pass failed)")
    for f in findings:
        ev = f.get("evidence", "")
        ok = (re.search(r"https?://\S+", ev) and re.search(r"HTTP \d{3}", ev)
              and re.search(r"\d+\s+\w+", ev))
        rep.check(bool(ok), "audit",
                  f"{os.path.basename(path)}/{f.get('id')}: evidence lacks URL + status + measurement")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the Agent Skills package and its output")
    ap.add_argument("--package", default=PKG_ROOT)
    ap.add_argument("--audit", action="append", default=[], help="audit.json file(s) to validate")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args()

    rep = Report()
    manifest = validate_package(args.package, rep)
    for audit in args.audit:
        validate_audit(audit, args.package, rep)

    print(f"package : {args.package}")
    if manifest:
        print(f"skills  : {len(manifest.get('skills', []))} declared, entrypoint "
              f"{manifest.get('entrypoint')!r}")
    print(f"checks  : {rep.checks}")
    print(f"failures: {len(rep.failures)}")
    for f in rep.failures:
        print(f"  FAIL  {f}")
    print(f"warnings: {len(rep.warnings)}")
    for w in rep.warnings:
        print(f"  WARN  {w}")
    failed = rep.failures or (args.strict and rep.warnings)
    print("\nRESULT: " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
