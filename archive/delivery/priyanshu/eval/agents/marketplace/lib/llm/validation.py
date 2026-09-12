#!/usr/bin/env python3
"""The gate.

Nothing a model returns reaches ``findings`` without passing through here.  The
rule the whole hybrid design rests on is enforced in this module: *Python decides
what is true*.  A semantic result is a proposal, and a proposal is accepted only
when every claim in it can be traced back to evidence the deterministic side
issued.

Checks applied, in order (the first failure stops that item, not the batch):

1.  shape          - JSON object, required keys, correct types
2.  vocabulary     - aspect / sentiment / emotion / severity / source_kind /
                     analysis_type are all from the closed lists
3.  citation       - every ``evidence_refs`` entry is an id this run issued
4.  attribution    - cited ids belong to the page the result names
5.  quotation      - ``quote`` is a verbatim substring of a cited section
6.  numeracy       - every number in the prose appears in the cited text
7.  technology     - no technology named that the evidence does not name
8.  locality       - no URL outside the crawled set
9.  consistency    - no contradiction of a deterministic measurement
10. novelty        - not a restatement of a deterministic finding
11. sufficiency    - severity is supported by the number of independent sections

Then, separately, :func:`promotion_decision` decides whether an accepted result
is strong enough to leave ``observations`` and become a finding.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import (
    ANALYSIS_TYPES,
    ASPECTS,
    CUSTOMER_SENTIMENT_SOURCES,
    EMOTIONS,
    EvidencePack,
    SENTIMENTS,
    SEVERITIES,
    SEVERITY_RANK,
    SOURCE_KINDS,
    is_evidence_id,
    make_observation,
)
from .config import LLMConfig
from .playbook import owners

# Technology names a model is prone to assert without support.  Loaded from a
# data file so the list can be edited without touching code - see the header of
# technology-vocabulary.txt for why it does not live here as a constant.
_TECH_VOCAB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "technology-vocabulary.txt")


def _load_tech_vocab(path: str = _TECH_VOCAB_PATH) -> tuple[str, ...]:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip().lower() for line in handle]
    except OSError:
        # A missing vocabulary weakens one check; it must not break validation.
        return ()
    return tuple(line for line in lines if line and not line.startswith("#"))


TECH_VOCAB = _load_tech_vocab()

# Assertions that duplicate a deterministic category.  Used twice: to reject a
# semantic restatement of something already measured, and to reject a semantic
# claim that contradicts a category the deterministic side measured as clean.
CATEGORY_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "structured_data": ("json-ld", "jsonld", "schema.org", "structured data", "microdata"),
    "rendering": ("javascript", "client-side render", "server-side render", "hydrat", "spa shell"),
    "crawlability": ("robots.txt", "noindex", "http 4", "http 5", "not retrievable", "blocked crawler"),
    "content_extraction": ("no extractable text", "empty body text", "no body copy at all"),
    "non_text_facts": ("alt text", "image-only", "only in an image", "locked in an image"),
    "freshness": ("copyright year", "last updated", "datemodified", "stale date"),
    "entity_identity": ("organization schema", "sameas", "entity name mismatch"),
    "engagement": ("<nav>", "nav landmark", "internal link count", "breadcrumblist"),
}

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
# Prose puts punctuation straight after a URL ("see https://x/pricing.") and a
# greedy match swallows it, which would make a perfectly good in-site link look
# foreign.  Trailing punctuation is stripped before any comparison.
_URL_TRAILING = ".,;:!?\u2019\u201d'\")]}>"
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_MAX_QUOTE_CHARS = 200
_MAX_TITLE_CHARS = 160
_MAX_TEXT_CHARS = 1200


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    code: str
    message: str
    item: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "item": self.item}


@dataclass
class ValidationResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    rejected: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def issue(self, code: str, message: str, item: str = "") -> None:
        self.issues.append(ValidationIssue(code, message, item))

    def violation_summary(self, limit: int = 8) -> list[dict[str, Any]]:
        """The only thing ever fed back into a repair prompt."""
        return [issue.as_dict() for issue in self.issues[:limit]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": len(self.accepted),
            "rejected": self.rejected,
            "issues": [issue.as_dict() for issue in self.issues],
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _urls_in(text: str) -> set[str]:
    """URLs mentioned in prose, normalised for comparison against known URLs."""
    found = set()
    for raw in _URL_RE.findall(text or ""):
        found.add(raw.rstrip(_URL_TRAILING).rstrip("/"))
    return found


def _known_url_set(urls: Any) -> set[str]:
    return {str(url).rstrip(_URL_TRAILING).rstrip("/") for url in urls}


def _clean_str(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalise_for_quote(text: str) -> str:
    text = re.sub(r"[‘’‚‛]", "'", text or "")
    text = re.sub(r"[“”„‟]", '"', text)
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER_RE.finditer(text or "")}


def _confidence(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 1.0 and value <= 100.0:      # a model that answered in percent
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        return None
    return round(value, 4)


# ---------------------------------------------------------------------------
# Semantic observations
# ---------------------------------------------------------------------------

def validate_observations(
    payload: Any,
    pack: EvidencePack,
    *,
    deterministic_findings: Sequence[Mapping[str, Any]] = (),
    coverage: Mapping[str, str] | None = None,
    config: LLMConfig | None = None,
) -> ValidationResult:
    """Validate a semantic response against the evidence pack."""
    result = ValidationResult()
    threshold = (config.corroboration_threshold if config else 0.65)

    if not isinstance(payload, Mapping):
        result.issue("shape", "response is not a JSON object")
        return result
    raw_items = payload.get("observations")
    if raw_items is None:
        result.issue("shape", "response has no 'observations' key")
        return result
    if not isinstance(raw_items, list):
        result.issue("shape", "'observations' must be a list")
        return result

    flagged = {f.get("category") for f in deterministic_findings}
    clean = {name for name, state in (coverage or {}).items() if state == "clean"}
    seen_signatures: set[tuple[str, str]] = set()
    counter = 0

    for position, raw in enumerate(raw_items):
        label = f"observations[{position}]"
        if not isinstance(raw, Mapping):
            result.issue("shape", "observation is not an object", label)
            result.rejected += 1
            continue

        # -- 1. shape / required fields ----------------------------------
        title = _clean_str(raw.get("title"), _MAX_TITLE_CHARS)
        aspect = _clean_str(raw.get("aspect"), 64).lower()
        sentiment = _clean_str(raw.get("sentiment"), 32).lower()
        emotion = _clean_str(raw.get("emotion"), 32).lower()
        severity = _clean_str(raw.get("severity"), 32).lower()
        source_kind = _clean_str(raw.get("source_kind"), 32).lower() or "brand_copy"
        analysis_type = _clean_str(raw.get("analysis_type"), 48).lower() or "content_tone"
        evidence_text = _clean_str(raw.get("evidence"), _MAX_TEXT_CHARS)
        quote = _clean_str(raw.get("quote"), _MAX_QUOTE_CHARS)
        refs_raw = raw.get("evidence_refs") or []
        action = raw.get("suggested_action") or {}

        if not title or not evidence_text:
            result.issue("required_field", "title and evidence are required", label)
            result.rejected += 1
            continue
        if not isinstance(refs_raw, list) or not refs_raw:
            result.issue("required_field", "evidence_refs must be a non-empty list", label)
            result.rejected += 1
            continue
        if not isinstance(action, Mapping) or not _clean_str(action.get("summary"), 600):
            result.issue("required_field", "suggested_action.summary is required", label)
            result.rejected += 1
            continue

        confidence = _confidence(raw.get("confidence"))
        if confidence is None:
            result.issue("required_field", "confidence must be a number between 0 and 1", label)
            result.rejected += 1
            continue

        # -- 2. closed vocabularies --------------------------------------
        if aspect not in ASPECTS:
            result.issue("enum", f"aspect {aspect!r} is not in the allowed list", label)
            result.rejected += 1
            continue
        if sentiment not in SENTIMENTS:
            result.issue("enum", f"sentiment {sentiment!r} is not in the allowed list", label)
            result.rejected += 1
            continue
        if emotion not in EMOTIONS:
            result.issue("enum", f"emotion {emotion!r} is not in the allowed list", label)
            result.rejected += 1
            continue
        if severity not in SEVERITIES:
            result.issue("enum", f"severity {severity!r} is not in the allowed list", label)
            result.rejected += 1
            continue
        if source_kind not in SOURCE_KINDS:
            result.issue("enum", f"source_kind {source_kind!r} is not in the allowed list", label)
            result.rejected += 1
            continue
        if analysis_type not in ANALYSIS_TYPES:
            result.issue("enum", f"analysis_type {analysis_type!r} is not in the allowed list", label)
            result.rejected += 1
            continue

        # -- 3. citation: ids must have been issued by this run ----------
        refs = [_clean_str(ref, 32) for ref in refs_raw]
        bad_shape = [ref for ref in refs if not is_evidence_id(ref)]
        if bad_shape:
            result.issue("evidence_id_format", f"malformed evidence ids: {bad_shape[:3]}", label)
            result.rejected += 1
            continue
        unknown = [ref for ref in refs if ref not in pack.sections_by_id]
        if unknown:
            result.issue("unknown_evidence_id", f"evidence ids were never issued: {unknown[:3]}", label)
            result.rejected += 1
            continue
        refs = list(dict.fromkeys(refs))
        sections = [pack.sections_by_id[ref] for ref in refs]

        # -- 4. attribution: the named page must own the cited sections --
        claimed_pages = raw.get("pages") or []
        cited_pages = {pack.page_of_section[ref] for ref in refs}
        if isinstance(claimed_pages, list) and claimed_pages:
            stray = [str(p) for p in claimed_pages if str(p) not in cited_pages]
            if stray:
                result.issue("evidence_page_mismatch",
                             f"pages {stray[:3]} are not the pages the cited evidence belongs to", label)
                result.rejected += 1
                continue

        # -- 5. quotation ------------------------------------------------
        haystack = _normalise_for_quote(" \n ".join(section.text for section in sections))
        if quote:
            if _normalise_for_quote(quote) not in haystack:
                result.issue("quote_not_found",
                             "quote is not a verbatim substring of any cited section", label)
                result.rejected += 1
                continue

        # -- 6. numeracy: no invented measurements -----------------------
        supported_numbers = _numbers(" ".join(section.text for section in sections))
        supported_numbers |= _numbers(" ".join(section.heading for section in sections))
        claimed_numbers = _numbers(evidence_text) | _numbers(title)
        invented = sorted(claimed_numbers - supported_numbers)
        if invented:
            result.issue("unsupported_number",
                         f"numbers not present in the cited evidence: {invented[:3]}", label)
            result.rejected += 1
            continue

        # -- 7. technology claims ----------------------------------------
        blob = f"{title} {evidence_text}".lower()
        evidence_blob = " ".join(section.text for section in sections).lower()
        asserted_tech = [name for name in TECH_VOCAB if name in blob and name not in evidence_blob]
        if asserted_tech:
            result.issue("unsupported_technology",
                         f"technology claimed without evidence: {asserted_tech[:3]}", label)
            result.rejected += 1
            continue

        # -- 8. locality: only crawled URLs ------------------------------
        cited_urls = _urls_in(f"{title} {evidence_text} {action.get('summary', '')}")
        foreign = sorted(cited_urls - _known_url_set(pack.known_urls))
        if foreign:
            result.issue("foreign_url", f"URLs outside the crawled set: {foreign[:2]}", label)
            result.rejected += 1
            continue

        # -- 9. consistency with deterministic measurements --------------
        contradicted = [
            category for category in clean
            if any(token in blob for token in CATEGORY_ASSERTIONS.get(category, ()))
        ]
        if contradicted:
            result.issue("contradicts_deterministic",
                         f"asserts a defect in {contradicted[:2]}, which was measured clean", label)
            result.rejected += 1
            continue

        # -- 10. novelty: not a restatement of a deterministic finding ---
        duplicated = [
            category for category in flagged
            if any(token in blob for token in CATEGORY_ASSERTIONS.get(category, ()))
        ]
        if duplicated:
            result.issue("duplicate_of_deterministic",
                         f"restates the deterministic {duplicated[0]} finding", label)
            result.rejected += 1
            continue

        signature = (aspect, _normalise_for_quote(title)[:60])
        if signature in seen_signatures:
            result.issue("duplicate_observation", "same aspect and claim already returned", label)
            result.rejected += 1
            continue

        # -- 11. sufficiency ---------------------------------------------
        if analysis_type == "customer_sentiment" and any(
            section.source_kind not in CUSTOMER_SENTIMENT_SOURCES for section in sections
        ):
            result.issue("source_kind_mismatch",
                         "customer_sentiment claimed over copy the site wrote about itself", label)
            result.rejected += 1
            continue
        if SEVERITY_RANK[severity] >= SEVERITY_RANK["high"] and len(refs) < 2:
            result.issue("insufficient_evidence",
                         f"severity {severity} needs two independent evidence sections, got {len(refs)}", label)
            result.rejected += 1
            continue
        if confidence < threshold:
            result.issue("abstain_low_confidence",
                         f"confidence {confidence} is below the abstention floor {threshold}", label)
            result.rejected += 1
            continue

        # -- accepted ----------------------------------------------------
        seen_signatures.add(signature)
        counter += 1
        try:
            observation = make_observation(
                observation_id=f"SEM-{counter:03d}",
                title=title,
                aspect=aspect,
                sentiment=sentiment,
                emotion=emotion,
                severity=severity,
                confidence=confidence,
                evidence=evidence_text,
                evidence_refs=refs,
                action_summary=_clean_str(action.get("summary"), 600),
                validation=_clean_str(action.get("validation"), 400) or "Re-read the cited sections after the change.",
                source_kind=source_kind,
                analysis_type=analysis_type,
                pages=sorted(cited_pages),
            )
        except ValueError as exc:
            result.issue("contract", str(exc), label)
            result.rejected += 1
            counter -= 1
            continue

        if quote:
            observation["quote"] = quote
        observation["evidence_sections"] = [
            {"evidence_id": section.evidence_id, "selector": section.selector,
             "page_id": pack.page_of_section[section.evidence_id]}
            for section in sections
        ]
        observation["independent_sections"] = len({
            pack.page_of_section[ref] for ref in refs
        })
        result.accepted.append(observation)

    return result


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

def validate_suggestions(
    payload: Any,
    findings: Sequence[Mapping[str, Any]],
    pack: EvidencePack | None = None,
) -> ValidationResult:
    """Validate suggestion enhancements for existing deterministic findings.

    A suggestion may only *describe* a finding that already exists; it can never
    introduce one, and anything it says about ids, URLs or evidence has to match
    what the deterministic side issued.
    """
    result = ValidationResult()
    known_ids = {str(f.get("id")) for f in findings if f.get("id")}
    known_urls = set(pack.known_urls) if pack else set()
    for finding in findings:
        known_urls.update(str(loc) for loc in finding.get("locations", []) or [])

    if not isinstance(payload, Mapping):
        result.issue("shape", "response is not a JSON object")
        return result
    raw_items = payload.get("suggestions")
    if not isinstance(raw_items, list):
        result.issue("shape", "'suggestions' must be a list")
        return result

    seen: set[str] = set()
    for position, raw in enumerate(raw_items):
        label = f"suggestions[{position}]"
        if not isinstance(raw, Mapping):
            result.issue("shape", "suggestion is not an object", label)
            result.rejected += 1
            continue

        finding_id = _clean_str(raw.get("finding_id"), 64)
        if finding_id not in known_ids:
            result.issue("unknown_finding_id",
                         f"finding_id {finding_id!r} is not one of the supplied findings", label)
            result.rejected += 1
            continue
        if finding_id in seen:
            result.issue("duplicate_suggestion", f"{finding_id} suggested twice", label)
            result.rejected += 1
            continue

        recommendation = _clean_str(raw.get("recommendation"), 900)
        acceptance = _clean_str(raw.get("acceptance_test"), 400)
        where = _clean_str(raw.get("where"), 400)
        steps_raw = raw.get("implementation_steps") or []
        steps = [_clean_str(step, 300) for step in steps_raw if _clean_str(step, 300)][:6]

        # A suggestion that does not name the place, the change and the check is
        # exactly the generic advice the deterministic text already gives, so it
        # is discarded rather than merged.
        if not recommendation or not acceptance or not where or len(steps) < 2:
            result.issue("generic_suggestion",
                         "needs where, recommendation, >=2 implementation steps and an acceptance test", label)
            result.rejected += 1
            continue

        blob = " ".join([recommendation, where, acceptance, " ".join(steps)])
        foreign = sorted(_urls_in(blob) - _known_url_set(known_urls))
        if foreign:
            result.issue("foreign_url", f"URLs outside the audited site: {foreign[:2]}", label)
            result.rejected += 1
            continue

        refs = [_clean_str(ref, 32) for ref in (raw.get("evidence_refs") or [])]
        if pack is not None:
            unknown = [ref for ref in refs if ref and ref not in pack.sections_by_id]
            if unknown:
                result.issue("unknown_evidence_id", f"evidence ids were never issued: {unknown[:3]}", label)
                result.rejected += 1
                continue

        owner = _clean_str(raw.get("owner"), 32).lower()
        effort = _clean_str(raw.get("effort"), 16).lower()
        seen.add(finding_id)
        result.accepted.append({
            "finding_id": finding_id,
            "root_cause": _clean_str(raw.get("root_cause"), 500),
            "recommendation": recommendation,
            "where": where,
            "implementation_steps": steps,
            "example": _clean_str(raw.get("example"), 400) or None,
            "expected_impact": _clean_str(raw.get("expected_impact"), 400),
            "effort": effort if effort in ("low", "medium", "high") else "medium",
            "owner": owner if owner in owners() else "engineering",
            "acceptance_test": acceptance,
            "evidence_refs": [ref for ref in refs if ref],
        })

    return result


# ---------------------------------------------------------------------------
# Error diagnoses
# ---------------------------------------------------------------------------

def validate_diagnoses(payload: Any, events: Sequence[Mapping[str, Any]],
                       allowed_codes: Iterable[str],
                       allowed_categories: Iterable[str]) -> ValidationResult:
    """Validate AI error diagnoses against the events that were actually raised."""
    result = ValidationResult()
    known = {str(event.get("event_id")) for event in events}
    codes = set(allowed_codes)
    categories = set(allowed_categories)

    if not isinstance(payload, Mapping):
        result.issue("shape", "response is not a JSON object")
        return result
    raw_items = payload.get("diagnoses")
    if not isinstance(raw_items, list):
        result.issue("shape", "'diagnoses' must be a list")
        return result

    for position, raw in enumerate(raw_items):
        label = f"diagnoses[{position}]"
        if not isinstance(raw, Mapping):
            result.issue("shape", "diagnosis is not an object", label)
            result.rejected += 1
            continue
        event_id = _clean_str(raw.get("event_id"), 32)
        if event_id not in known:
            result.issue("unknown_event_id", f"event_id {event_id!r} was never raised", label)
            result.rejected += 1
            continue
        category = _clean_str(raw.get("category"), 32).lower()
        if category not in categories:
            result.issue("enum", f"category {category!r} is not allowed", label)
            result.rejected += 1
            continue
        code = _clean_str(raw.get("recovery_code"), 40).upper()
        if code not in codes:
            result.issue("recovery_not_allowlisted", f"recovery_code {code!r} is not on the allowlist", label)
            result.rejected += 1
            continue
        confidence = _confidence(raw.get("confidence"))
        if confidence is None:
            result.issue("required_field", "confidence must be a number between 0 and 1", label)
            result.rejected += 1
            continue
        steps = [_clean_str(step, 200) for step in (raw.get("investigation_steps") or [])]
        result.accepted.append({
            "event_id": event_id,
            "category": category,
            "hypothesis": _clean_str(raw.get("hypothesis"), 400),
            "recovery_code": code,
            "confidence": confidence,
            "limitation_summary": _clean_str(raw.get("limitation_summary"), 300),
            "investigation_steps": [step for step in steps if step][:5],
        })
    return result


# ---------------------------------------------------------------------------
# Promotion policy
# ---------------------------------------------------------------------------

PROMOTE = "promote"
OBSERVE = "observe"
ABSTAIN = "abstain"


def promotion_decision(
    observation: Mapping[str, Any],
    *,
    config: LLMConfig,
    verifier: Mapping[str, Any] | None = None,
    deterministic_categories: Iterable[str] = (),
) -> tuple[str, str]:
    """Decide whether an accepted observation may become a finding.

    Returns ``(decision, reason)``.  Self-reported confidence is never the only
    signal: it is combined with how many independent sections back the claim,
    whether those sections came from different pages, and - for anything high or
    critical - an explicit verifier pass.
    """
    confidence = float(observation.get("confidence", 0.0))
    severity = str(observation.get("severity", "low"))
    refs = observation.get("evidence_refs") or []
    independent_pages = int(observation.get("independent_sections", 1) or 1)
    corroborated = bool(set(deterministic_categories) & {observation.get("corroborates")})

    if confidence < config.corroboration_threshold:
        return ABSTAIN, f"confidence {confidence} below the abstention floor {config.corroboration_threshold}"

    high = SEVERITY_RANK[severity] >= SEVERITY_RANK["high"]
    if high:
        if len(refs) < 2:
            return OBSERVE, "high severity requires two evidence sections"
        if config.verifier_enabled:
            if not verifier:
                return OBSERVE, "high severity requires a verifier pass and none was performed"
            if str(verifier.get("verdict")) != "confirm":
                return ABSTAIN, f"verifier rejected: {verifier.get('reason', 'no reason given')}"
    elif verifier and str(verifier.get("verdict")) == "reject":
        return ABSTAIN, f"verifier rejected: {verifier.get('reason', 'no reason given')}"

    if confidence >= config.confidence_threshold:
        return PROMOTE, f"confidence {confidence} at or above {config.confidence_threshold} with validated evidence"

    # 0.65 - 0.79: needs corroboration rather than confidence.
    if len(refs) >= 2 and independent_pages >= 2:
        return PROMOTE, "two independent evidence sections on different pages"
    if corroborated:
        return PROMOTE, "corroborated by a deterministic finding in the same area"
    return OBSERVE, (
        f"confidence {confidence} is below {config.confidence_threshold} and the claim rests on "
        f"{len(refs)} section(s) from {independent_pages} page(s)"
    )
