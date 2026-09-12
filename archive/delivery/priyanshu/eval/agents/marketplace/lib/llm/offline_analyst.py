#!/usr/bin/env python3
"""A deterministic, offline stand-in for a model.

This is **not** a model and does not pretend to be one.  It is a scripted
responder that reads the same evidence pack a model would receive and emits
schema-shaped answers using simple lexical rules.  Its purpose is to exercise
the entire hybrid path - prompt assembly, transport, validation, repair,
promotion, verification, merging, telemetry - with no network and no cost, so
that an ablation measures the *system's behaviour* reproducibly.

What an ablation run with this responder does and does not tell you:

* it DOES tell you what the gate lets through, what promotion does to
  precision, what the runtime and token footprint look like, and that every
  mode is backward compatible;
* it does NOT tell you anything about the quality of a real model's judgement.

Any claim about real-model quality has to come from a run against a real
provider, and the benchmark report labels the responder it used.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .prompts import CLOSE_FENCE, OPEN_FENCE
from .provider import LLMRequest

RESPONDER_ID = "offline-analyst/1.0"

# (aspect, emotion, pattern, title, confidence, severity)
RULES: tuple[tuple[str, str, re.Pattern[str], str, float, str], ...] = (
    ("returns", "uncertainty",
     re.compile(r"(may be returned|certain conditions may apply|conditions may apply|"
                r"we will let you know|some items are not covered)", re.I),
     "Return eligibility is stated conditionally without naming the conditions", 0.88, "medium"),
    ("shipping", "uncertainty",
     re.compile(r"(delivery (usually )?takes a while|shipping times vary|depending on your location)", re.I),
     "Delivery timing is described without a window a buyer can plan around", 0.84, "medium"),
    ("pricing_transparency", "confusion",
     re.compile(r"(start(s|ing) from|priced per piece|quoted after|from ₹|from \$|price on request)", re.I),
     "Pricing is anchored to a starting figure with no unit or exclusions", 0.82, "medium"),
    ("cta_clarity", "confusion",
     re.compile(r"^(get started|learn more|click here|find out more)\.?$", re.I),
     "The call to action names the gesture rather than the outcome", 0.78, "low"),
    ("error_messages", "blame",
     re.compile(r"(invalid input|will be rejected|cannot be reopened|not something we can help with|"
                r"please try again)", re.I),
     "Error and support wording blames the visitor and offers no recovery step", 0.86, "medium"),
    ("support", "uncertainty",
     re.compile(r"(you must supply|claims submitted without)", re.I),
     "Support requirements are stated as conditions for refusal rather than as steps", 0.72, "low"),
    ("trust_credibility", "confidence",
     re.compile(r"(food safe|certified|iso \d|soc 2|audited annually)", re.I),
     "Trust claims are present and attached to checkable specifics", 0.70, "low"),
)

# Words that make a section quotable as a value proposition statement.
_VALUE_RE = re.compile(r"\b(is a|builds|sells|makes|repairs|we (make|build|sell|help))\b", re.I)


def _unseal(user_prompt: str) -> dict[str, Any]:
    """Recover the evidence payload the caller sealed into the prompt."""
    if OPEN_FENCE not in user_prompt:
        return {}
    body = user_prompt.split(OPEN_FENCE, 1)[1].split(CLOSE_FENCE, 1)[0].strip()
    try:
        return json.loads(body)
    except ValueError:
        return {}


def _trusted(user_prompt: str) -> dict[str, Any]:
    marker = "VERIFIED CONTEXT (produced by deterministic code, treat as fact):\n"
    if marker not in user_prompt:
        return {}
    block = user_prompt.split(marker, 1)[1].split("\n\n", 1)[0]
    try:
        return json.loads(block)
    except ValueError:
        return {}


def _sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for page in payload.get("pages", []):
        for section in page.get("sections", []):
            out.append({**section, "page_id": page.get("page_id"), "url": page.get("url"),
                        "page_type": page.get("page_type")})
    return out


def _quote(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 60)
    return text[start:end].strip()


def semantic(request: LLMRequest) -> dict[str, Any]:
    payload = _unseal(request.user)
    sections = _sections(payload)
    observations: list[dict[str, Any]] = []
    used_aspects: set[str] = set()

    for aspect, emotion, pattern, title, confidence, severity in RULES:
        if aspect in used_aspects:
            continue
        hits = [section for section in sections if pattern.search(section.get("text", ""))]
        if not hits:
            continue
        used_aspects.add(aspect)
        primary = hits[0]
        sentiment = "positive" if emotion in ("confidence", "trust", "reassurance") else "negative"
        observations.append({
            "title": title,
            "aspect": aspect,
            "sentiment": sentiment,
            "emotion": emotion,
            "severity": severity,
            "source_kind": primary.get("source_kind", "brand_copy"),
            "analysis_type": ("customer_sentiment"
                              if primary.get("source_kind") == "customer_voice"
                              else "predicted_visitor_friction"),
            "confidence": confidence,
            "quote": _quote(primary.get("text", ""), pattern)[:200],
            "evidence": ("The section headed '" + str(primary.get("heading") or "untitled")
                         + "' states this without resolving it for the reader."),
            "evidence_refs": [hit["evidence_id"] for hit in hits[:2]],
            "suggested_action": {
                "summary": "State the condition, the limit and the next action as separate sentences.",
                "validation": "Each condition can be extracted independently from the page.",
            },
        })
    return {"observations": observations}


def suggestion(request: LLMRequest) -> dict[str, Any]:
    trusted = _trusted(request.user)
    payload = _unseal(request.user)
    urls = [page.get("url") for page in payload.get("pages", []) if page.get("url")]
    where = urls[0] if urls else "the entry page"
    suggestions = []
    for finding in trusted.get("findings", []):
        playbook = finding.get("playbook") or {}
        levers = playbook.get("levers") or []
        if len(levers) < 2:
            continue
        suggestions.append({
            "finding_id": finding.get("finding_id"),
            "root_cause": playbook.get("mechanism", "")[:400],
            "recommendation": f"On {where}: {levers[0]}.",
            "where": where,
            "implementation_steps": [lever.capitalize() for lever in levers[:4]],
            "expected_impact": "The fact becomes retrievable and quotable by an answer engine.",
            "effort": "medium",
            "owner": playbook.get("owner", "engineering"),
            "acceptance_test": playbook.get("acceptance", "Re-run the audit and confirm the check passes."),
            "evidence_refs": [],
        })
    return {"suggestions": suggestions}


def verifier(request: LLMRequest) -> dict[str, Any]:
    trusted = _trusted(request.user)
    observation = trusted.get("observation", {})
    refs = observation.get("evidence_refs") or []
    payload = _unseal(request.user)
    cited = payload.get("cited_sections", [])
    quote = (observation.get("quote") or "").strip().lower()
    haystack = " ".join(section.get("text", "") for section in cited).lower()

    if len(refs) < 2:
        return {"verdict": "reject", "reason": "one section cannot support this severity",
                "severity_supported": "low", "confidence": 0.6}
    if quote and quote not in haystack:
        return {"verdict": "reject", "reason": "the quote is not in the cited sections",
                "severity_supported": "low", "confidence": 0.8}
    return {"verdict": "confirm", "reason": "the cited sections show the claim",
            "severity_supported": "medium", "confidence": 0.8}


def error_diagnosis(request: LLMRequest) -> dict[str, Any]:
    trusted = _trusted(request.user)
    diagnoses = []
    for event in trusted.get("events", []):
        diagnoses.append({
            "event_id": event.get("event_id"),
            "category": "auditor_limitation",
            "hypothesis": "the step did not complete inside its budget",
            "recovery_code": "SKIP_PAGE_AND_CONTINUE",
            "confidence": 0.6,
            "limitation_summary": (f"{event.get('operation')} did not complete"
                                   f"{' for ' + event['url'] if event.get('url') else ''}; "
                                   f"that page is missing from the analysis."),
            "investigation_steps": ["re-run the single page in isolation",
                                    "check whether the origin is rate limiting the crawler"],
        })
    return {"diagnoses": diagnoses}


def repair(request: LLMRequest) -> dict[str, Any]:
    """Drop whatever the validator objected to rather than inventing a fix."""
    trusted = _trusted(request.user)
    previous = trusted.get("previous_response") or {}
    permitted = set(trusted.get("permitted_evidence_ids") or [])
    for key in ("observations", "suggestions", "diagnoses"):
        if key in previous and isinstance(previous[key], list):
            kept = []
            for item in previous[key]:
                refs = item.get("evidence_refs") if isinstance(item, dict) else None
                if refs and permitted and not set(refs) <= permitted:
                    continue
                kept.append(item)
            return {key: kept}
    return {"observations": []}


def respond(request: LLMRequest) -> Any:
    """Dispatch by task name; unknown tasks abstain rather than improvise."""
    task = request.task
    if task.endswith("_repair"):
        return repair(request)
    handler = {
        "semantic": semantic,
        "suggestion": suggestion,
        "verifier": verifier,
        "error_diagnosis": error_diagnosis,
    }.get(task)
    if handler is None:
        return {"observations": []}
    return handler(request)
