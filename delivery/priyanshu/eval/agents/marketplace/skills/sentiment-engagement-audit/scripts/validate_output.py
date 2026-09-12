#!/usr/bin/env python3
"""Standalone validator for this skill's observation output.

Usable two ways:

    python skills/sentiment-engagement-audit/scripts/validate_output.py obs.json
    python skills/sentiment-engagement-audit/scripts/validate_output.py obs.json --pack pack.json

Without a pack it checks shape and vocabulary.  With a pack it additionally
resolves every ``evidence_refs`` entry and re-checks the quotes, which is the
check that actually matters: it is what proves the citations are real.

Exit code 0 = every observation is valid, 1 = at least one is not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKETPLACE_ROOT = os.path.dirname(os.path.dirname(SKILL_DIR))
if MARKETPLACE_ROOT not in sys.path:
    sys.path.insert(0, MARKETPLACE_ROOT)

from lib.contracts import (  # noqa: E402
    ANALYSIS_TYPES, ASPECTS, EMOTIONS, EvidencePack, EvidenceSection,
    PageEvidence, SENTIMENTS, SEVERITIES, SOURCE_KINDS, is_evidence_id,
)
from lib.llm.validation import validate_observations  # noqa: E402

REQUIRED = ("id", "title", "category", "aspect", "sentiment", "emotion",
            "severity", "confidence", "evidence", "evidence_refs", "suggested_action")

ENUMS = {
    "aspect": ASPECTS, "sentiment": SENTIMENTS, "emotion": EMOTIONS,
    "severity": SEVERITIES, "source_kind": SOURCE_KINDS, "analysis_type": ANALYSIS_TYPES,
}


def check_shape(observations: list) -> list[str]:
    problems: list[str] = []
    for index, observation in enumerate(observations):
        label = f"observations[{index}]"
        if not isinstance(observation, dict):
            problems.append(f"{label}: not an object")
            continue
        for key in REQUIRED:
            if key not in observation:
                problems.append(f"{label}: missing required field {key!r}")
        for key, allowed in ENUMS.items():
            value = observation.get(key)
            if value is not None and value not in allowed:
                problems.append(f"{label}: {key}={value!r} is not in the allowed list")
        refs = observation.get("evidence_refs") or []
        if not refs:
            problems.append(f"{label}: evidence_refs is empty")
        for ref in refs:
            if not is_evidence_id(str(ref)):
                problems.append(f"{label}: malformed evidence id {ref!r}")
        try:
            confidence = float(observation.get("confidence", -1))
        except (TypeError, ValueError):
            confidence = -1.0
        if not 0.0 <= confidence <= 1.0:
            problems.append(f"{label}: confidence must be between 0 and 1")
        if observation.get("analysis_type") == "customer_sentiment" and \
                observation.get("source_kind") != "customer_voice":
            problems.append(f"{label}: customer_sentiment claimed over {observation.get('source_kind')!r}")
    return problems


def load_pack(path: str) -> EvidencePack:
    raw = json.load(open(path, encoding="utf-8"))
    pages = []
    for page in raw.get("pages", []):
        sections = [
            EvidenceSection(
                evidence_id=section["evidence_id"], selector=section.get("selector", ""),
                heading=section.get("heading", ""), text=section.get("text", ""),
                source_kind=section.get("source_kind", "brand_copy"),
                language=section.get("language", "en"),
                word_count=len(str(section.get("text", "")).split()),
            )
            for section in page.get("sections", [])
        ]
        pages.append(PageEvidence(
            page_id=page["page_id"], url=page.get("url", ""),
            page_type=page.get("page_type", "other"), depth=int(page.get("depth", 0)),
            status_code=page.get("status_code"), title=page.get("title", ""),
            sections=sections,
        ))
    return EvidencePack(site_url=raw.get("site_url", ""),
                        snapshot_id=raw.get("snapshot_id", ""), pages=pages)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", help="JSON file: a list, or {'observations': [...]}")
    parser.add_argument("--pack", help="evidence pack JSON, enabling citation and quote checks")
    args = parser.parse_args()

    payload = json.load(open(args.observations, encoding="utf-8"))
    observations = payload.get("observations", payload) if isinstance(payload, dict) else payload
    if not isinstance(observations, list):
        print("FAIL: expected a list of observations")
        return 1

    problems = check_shape(observations)

    if args.pack:
        pack = load_pack(args.pack)
        result = validate_observations({"observations": observations}, pack)
        problems.extend(f"{issue.item or 'response'}: [{issue.code}] {issue.message}"
                        for issue in result.issues)
        print(f"citation check: {len(result.accepted)} accepted, {result.rejected} rejected")

    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: {len(observations)} observation(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
