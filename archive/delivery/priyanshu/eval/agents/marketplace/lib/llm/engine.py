#!/usr/bin/env python3
"""The hybrid engine: the only place that actually calls a model.

Everything above it (the orchestrator, the skills) sees plain data structures
and cannot tell whether a model was involved.  Everything below it (the provider
adapters) sees prompts and JSON and knows nothing about audits.

Every public method here obeys the same three rules:

* it returns a usable value even when the model, the network or the schema
  fails - the deterministic result is always the fallback;
* it never raises into the caller;
* it records what happened in :class:`~lib.llm.observability.LLMTelemetry`, so a
  reader of the final report can see exactly how much of it was model-assisted.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..contracts import EvidencePack, PageEvidence, SEVERITY_RANK
from .cache import ResponseCache, cache_key
from .config import (
    CAP_ERROR_DIAGNOSIS,
    CAP_PROMOTION,
    CAP_SEMANTIC,
    CAP_SUGGESTIONS,
    CAP_VERIFIER,
    LLMConfig,
)
from .errors import (
    ERROR_CATEGORIES,
    ErrorLog,
    RECOVERY_ALLOWLIST,
    apply_diagnosis,
)
from .evidence import pack_for_pages, select_semantic_pages
from .observability import LLMTelemetry
from .playbook import excerpt_for_aspect, excerpt_for_category
from .prompts import system_prompt, user_prompt
from .provider import LLMClient, LLMError, LLMRequest, LLMResponse, build_client, unavailable_reason
from .validation import (
    ABSTAIN,
    OBSERVE,
    PROMOTE,
    ValidationResult,
    promotion_decision,
    validate_diagnoses,
    validate_observations,
    validate_suggestions,
)

MAX_FINDINGS_PER_SUGGESTION_CALL = 12
MAX_VERIFIER_CALLS = 3


class HybridEngine:
    """Owns the client, the cache, the repair loop and the telemetry."""

    def __init__(self, config: LLMConfig, *, client: LLMClient | None = None,
                 fake_responses: Any = None, telemetry: LLMTelemetry | None = None):
        self.config = config
        self.telemetry = telemetry or LLMTelemetry(config)
        self.cache = ResponseCache(config)
        self.client = build_client(config, fake_responses=fake_responses, injected=client)

        reason = unavailable_reason(config)
        if not config.is_off and not self.client.available:
            self.telemetry.note_fallback("llm_provider_unavailable")
            self.telemetry.note_warning(
                f"LLM layer requested in mode {config.effective_mode!r} but the provider is "
                f"unusable ({reason or 'unknown reason'}); the audit ran deterministically."
            )

    # -- state -----------------------------------------------------------
    @property
    def active(self) -> bool:
        return not self.config.is_off and self.client.available

    def can(self, capability: str) -> bool:
        return self.active and self.config.has(capability)

    # -- transport -------------------------------------------------------
    def _call(self, task: str, snapshot_id: str, system: str, user: str,
              *, max_output_tokens: int = 4000) -> LLMResponse | None:
        """One cached, bounded, never-raising request."""
        request = LLMRequest(task=task, system=system, user=user, max_output_tokens=max_output_tokens)
        key = cache_key(self.config, snapshot_id, request)

        cached = self.cache.get(key, task)
        if cached is not None:
            self.telemetry.record_response(cached)
            return cached

        try:
            response = self.client.complete_json(request)
        except LLMError as exc:
            self.telemetry.record_error(task, exc)
            if exc.kind == "payload_too_large":
                self.telemetry.note_fallback("SPLIT_LLM_PAYLOAD")
            elif exc.kind in ("invalid_credentials", "disabled"):
                self.telemetry.note_fallback("USE_DETERMINISTIC_ONLY")
            return None
        except Exception as exc:  # noqa: BLE001 - a provider bug must not fail the audit
            self.telemetry.record_error(task, LLMError("provider_unavailable", str(exc)))
            return None

        self.cache.put(key, response)
        self.telemetry.record_response(response)
        return response

    def _call_with_repair(self, task: str, snapshot_id: str, system: str, user: str,
                          validate, *, max_output_tokens: int = 4000,
                          allowed_ids: Sequence[str] = ()) -> ValidationResult:
        """Call, validate, and allow exactly one structured repair attempt.

        The repair prompt receives only the schema violations, the model's own
        previous answer and the list of ids it is allowed to cite - never new
        evidence, and never a hint about what answer would be accepted.
        """
        response = self._call(task, snapshot_id, system, user, max_output_tokens=max_output_tokens)
        if response is None:
            return ValidationResult()

        result = validate(response.data)
        if result.accepted or not result.issues or self.config.max_repairs <= 0:
            self.telemetry.calls[-1].validation = "accepted" if result.accepted else "rejected"
            self.telemetry.rejections += result.rejected
            return result

        self.telemetry.rejections += result.rejected
        repair_user = user_prompt(
            instructions=(
                "Your previous response failed validation. Return a corrected document in the "
                "same schema, fixing only the violations listed below."
            ),
            trusted={
                "schema_violations": result.violation_summary(),
                "permitted_evidence_ids": list(allowed_ids)[:400],
                "previous_response": response.data,
            },
        )
        repaired = self._call(f"{task}_repair", snapshot_id, system_prompt("repair") + "\n\n" + system,
                              repair_user, max_output_tokens=max_output_tokens)
        self.telemetry.repairs += 1
        if repaired is None:
            self.telemetry.note_fallback("repair_failed")
            return result

        second = validate(repaired.data)
        self.telemetry.calls[-1].validation = "repaired" if second.accepted else "rejected"
        self.telemetry.rejections += second.rejected
        if not second.accepted:
            self.telemetry.note_fallback("repair_failed")
        return second

    # -- 1. suggestion enhancement ---------------------------------------
    def enhance_suggestions(self, findings: Sequence[Mapping[str, Any]],
                            pack: EvidencePack) -> dict[str, dict[str, Any]]:
        """Richer remediation for findings that are already confirmed.

        Returns ``{finding_id: suggestion}``.  Findings the model omitted, or
        whose suggestion failed validation, simply keep the deterministic text -
        which is why this can never make a report worse.
        """
        if not self.can(CAP_SUGGESTIONS) or not findings:
            return {}

        payload = []
        for finding in list(findings)[:MAX_FINDINGS_PER_SUGGESTION_CALL]:
            category = str(finding.get("category", ""))
            payload.append({
                "finding_id": finding.get("id"),
                "category": category,
                "title": finding.get("title"),
                "severity": finding.get("severity"),
                "priority": (finding.get("suggested_action") or {}).get("priority"),
                "evidence": finding.get("evidence"),
                "locations": list(finding.get("locations", []))[:5],
                "page_types": sorted({
                    page.page_type for page in pack.pages
                    if page.url in set(finding.get("locations", []) or [])
                }) or None,
                "current_recommendation": (finding.get("suggested_action") or {}).get("summary"),
                "playbook": excerpt_for_category(category),
            })

        request_user = user_prompt(
            instructions=("Improve the remediation guidance for each confirmed finding below. "
                          "Do not change severity or priority, and do not add or remove findings."),
            trusted={"site": pack.site_url, "findings": payload,
                     "permitted_evidence_ids": [s for s in pack.sections_by_id][:400]},
            untrusted={"pages": [{"page_id": page.page_id, "url": page.url,
                                  "page_type": page.page_type, "title": page.title}
                                 for page in pack.pages]},
        )
        result = self._call_with_repair(
            "suggestion", pack.snapshot_id, system_prompt("suggestion"), request_user,
            lambda data: validate_suggestions(data, findings, pack),
            allowed_ids=list(pack.sections_by_id),
        )
        self.telemetry.enrichments += len(result.accepted)
        return {item["finding_id"]: item for item in result.accepted}

    # -- 2. semantic analysis --------------------------------------------
    def analyse_semantics(self, pack: EvidencePack,
                          *, deterministic_findings: Sequence[Mapping[str, Any]] = (),
                          coverage: Mapping[str, str] | None = None,
                          ) -> tuple[list[dict[str, Any]], list[PageEvidence]]:
        """One batched, site-level semantic request.

        Deliberately not one request per page: a single call sees the whole
        journey, which is the only way cross-page contradictions and tone drift
        are visible at all.
        """
        if not self.can(CAP_SEMANTIC) or not pack.pages:
            return [], []

        selected = select_semantic_pages(pack, self.config.crawl)
        if not selected:
            return [], []

        allowed_ids = [section.evidence_id for page in selected for section in page.sections]
        request_user = user_prompt(
            instructions=("Analyse the copy below aspect by aspect. Cite only the evidence ids "
                          "listed as permitted. Return an empty list when the copy gives you "
                          "nothing solid."),
            trusted={
                "site": pack.site_url,
                "permitted_evidence_ids": allowed_ids,
                "already_reported_by_deterministic_checks": sorted(
                    {str(f.get("category")) for f in deterministic_findings}),
                "measured_clean_do_not_contradict": sorted(
                    name for name, state in (coverage or {}).items() if state == "clean"),
                "aspect_playbook": {aspect: excerpt_for_aspect(aspect)
                                    for aspect in ("value_proposition", "pricing_transparency",
                                                   "returns", "shipping", "support",
                                                   "cta_clarity", "error_messages")},
            },
            untrusted=pack_for_pages(pack, selected),
        )
        result = self._call_with_repair(
            "semantic", pack.snapshot_id, system_prompt("semantic"), request_user,
            lambda data: validate_observations(
                data, pack, deterministic_findings=deterministic_findings,
                coverage=coverage, config=self.config),
            allowed_ids=allowed_ids,
        )
        self.telemetry.abstentions += sum(
            1 for issue in result.issues if issue.code.startswith("abstain"))
        return result.accepted, selected

    # -- 3. verification --------------------------------------------------
    def verify(self, observation: Mapping[str, Any], pack: EvidencePack,
               deterministic_findings: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any] | None:
        """Adversarial second read for a high-severity observation."""
        if not self.can(CAP_VERIFIER) or not self.config.verifier_enabled:
            return None

        refs = [ref for ref in observation.get("evidence_refs", []) if ref in pack.sections_by_id]
        if not refs:
            return None
        sections = [pack.sections_by_id[ref] for ref in refs]

        request_user = user_prompt(
            instructions="Verify the observation below against its cited evidence.",
            trusted={
                "observation": {
                    key: observation.get(key) for key in
                    ("title", "aspect", "sentiment", "emotion", "severity", "confidence",
                     "evidence", "evidence_refs", "quote", "source_kind", "analysis_type")
                },
                "deterministic_findings": [
                    {"category": f.get("category"), "title": f.get("title")}
                    for f in deterministic_findings
                ],
            },
            untrusted={"cited_sections": [section.as_dict() for section in sections]},
        )
        response = self._call("verifier", pack.snapshot_id, system_prompt("verifier"),
                              request_user, max_output_tokens=800)
        if response is None or not isinstance(response.data, Mapping):
            return None

        verdict = str(response.data.get("verdict", "")).lower()
        if verdict not in ("confirm", "reject"):
            self.telemetry.note_warning("verifier returned an unusable verdict; observation not promoted")
            return {"verdict": "reject", "reason": "verifier response was not a valid verdict"}
        supported = str(response.data.get("severity_supported", "")).lower()
        return {
            "verdict": verdict,
            "reason": str(response.data.get("reason", ""))[:300],
            "severity_supported": supported if supported in SEVERITY_RANK else None,
            "confidence": response.data.get("confidence"),
        }

    # -- 4. promotion ------------------------------------------------------
    def decide_promotions(self, observations: Sequence[dict[str, Any]], pack: EvidencePack,
                          deterministic_findings: Sequence[Mapping[str, Any]] = (),
                          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split validated observations into ``(promoted, kept_as_observations)``.

        Without :data:`CAP_PROMOTION` every observation stays an observation, so
        shadow mode is the same code path with the last gate closed.
        """
        promoted: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        verifier_calls = 0
        det_categories = {str(f.get("category")) for f in deterministic_findings}

        for observation in observations:
            if not self.config.has(CAP_PROMOTION):
                observation["validation_status"] = "observed"
                observation["promotion_reason"] = "shadow mode: promotion is disabled"
                kept.append(observation)
                continue

            verdict = None
            needs_verifier = (
                SEVERITY_RANK[str(observation.get("severity", "low"))] >= SEVERITY_RANK["high"]
                and self.config.verifier_enabled
            )
            if needs_verifier and verifier_calls < MAX_VERIFIER_CALLS:
                verdict = self.verify(observation, pack, deterministic_findings)
                verifier_calls += 1
                if verdict and verdict.get("severity_supported") and \
                        SEVERITY_RANK[verdict["severity_supported"]] < SEVERITY_RANK[str(observation["severity"])]:
                    observation["severity_note"] = (
                        f"Lowered from {observation['severity']} to {verdict['severity_supported']} "
                        f"by the verifier: {verdict.get('reason', '')}".strip()
                    )
                    observation["severity"] = verdict["severity_supported"]
                    observation["suggested_action"]["priority"] = verdict["severity_supported"]

            decision, reason = promotion_decision(
                observation, config=self.config, verifier=verdict,
                deterministic_categories=det_categories,
            )
            observation["promotion_reason"] = reason
            if verdict:
                observation["verifier"] = verdict

            if decision == PROMOTE:
                observation["validation_status"] = "accepted"
                promoted.append(observation)
                self.telemetry.promotions += 1
            elif decision == OBSERVE:
                observation["validation_status"] = "observed"
                kept.append(observation)
            else:
                observation["validation_status"] = "abstained"
                self.telemetry.abstentions += 1
                kept.append(observation)

        return promoted, kept

    # -- 5. error diagnosis ------------------------------------------------
    def diagnose_errors(self, log: ErrorLog) -> list[dict[str, Any]]:
        """Ask about the failures deterministic rules could not place."""
        if not self.can(CAP_ERROR_DIAGNOSIS):
            return []
        ambiguous = log.ambiguous()
        if not ambiguous:
            return []

        events = [event.as_dict() for event in ambiguous][:10]
        request_user = user_prompt(
            instructions="Diagnose the failures below and recommend one allowlisted recovery for each.",
            trusted={
                "events": events,
                "allowed_categories": list(ERROR_CATEGORIES),
                "allowed_recovery_codes": list(RECOVERY_ALLOWLIST),
            },
        )
        result = self._call_with_repair(
            "error_diagnosis", "errors", system_prompt("error_diagnosis"), request_user,
            lambda data: validate_diagnoses(data, events, RECOVERY_ALLOWLIST, ERROR_CATEGORIES),
            max_output_tokens=1500,
        )

        by_id = {event.event_id: event for event in ambiguous}
        applied: list[dict[str, Any]] = []
        for diagnosis in result.accepted:
            event = by_id.get(diagnosis["event_id"])
            if event is None:
                continue
            outcome = apply_diagnosis(event, diagnosis, log)
            applied.append({**diagnosis, "outcome": outcome})
        return applied

    # -- teardown ----------------------------------------------------------
    def finish(self, snapshot_id: str = "") -> dict[str, Any]:
        self.telemetry.snapshot_id = snapshot_id or self.telemetry.snapshot_id
        self.telemetry.cache_stats = self.cache.stats.as_dict()
        return self.telemetry.as_dict()
