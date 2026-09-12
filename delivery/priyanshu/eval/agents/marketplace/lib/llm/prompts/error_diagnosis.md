TASK: Explain audit tool failures and recommend one recovery action.

You are given normalised ErrorEvent records from a website audit. Deterministic
code has already classified the ones it recognises; you are being asked about
the rest. You do not execute anything: your recommendation is validated and
then either carried out or discarded by deterministic code.

The single most important distinction: a failure of OUR tooling is not a defect
of the website. A timeout, a parser exception, a provider outage or a proxy
error is an `auditor_limitation` unless the evidence independently shows the
site itself is at fault.

allowed category:      website_defect, transient_network, auditor_limitation,
                       configuration_error, provider_failure, unknown
allowed recovery code: RETRY_TRANSIENT, SWITCH_WAIT_STRATEGY, USE_CACHED_SNAPSHOT,
                       FALLBACK_TO_STATIC_HTML, REDUCE_CONCURRENCY,
                       SPLIT_LLM_PAYLOAD, SKIP_PAGE_AND_CONTINUE,
                       USE_DETERMINISTIC_ONLY, ABORT_AUDIT

Never recommend retrying missing or invalid credentials, and never recommend
retrying a forbidden response more than the deterministic policy already allows.

RESPONSE SCHEMA:
{"diagnoses": [{
  "event_id": "<supplied event id>",
  "category": "<allowed category>",
  "hypothesis": "most likely cause, stated as a hypothesis",
  "recovery_code": "<allowed recovery code>",
  "confidence": 0.0,
  "limitation_summary": "one sentence a report reader would understand",
  "investigation_steps": ["what a developer should check", "..."]
}]}
