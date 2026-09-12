TASK: Aspect-based semantic analysis of website copy for on-site engagement.

Read the supplied evidence sections and identify places where the WRITING
itself would cost the site a visitor or an AI answer engine: meaning that is
ambiguous, contradictory, buried, manipulative, or missing a next step.

Do NOT produce one overall sentiment for the site. Work aspect by aspect.

allowed aspect:        value_proposition, product_clarity, pricing_transparency,
                       product_quality, shipping, returns, support,
                       trust_credibility, cta_clarity, forms_validation,
                       error_messages, navigation, reviews_testimonials,
                       tone_consistency
allowed sentiment:     positive, neutral, negative, mixed, unknown
allowed emotion:       trust, reassurance, clarity, confusion, frustration,
                       pressure, anxiety, urgency, uncertainty, blame, confidence
allowed severity:      critical, high, medium, low
allowed source_kind:   brand_copy, customer_voice, system_message
allowed analysis_type: content_tone, predicted_visitor_friction, customer_sentiment

Look for, among others: a value proposition a reader cannot restate; vague
pricing or shipping language; policies that contradict each other across pages;
unsupported superlative claims; manufactured urgency; a call to action whose
outcome is unclear; error or validation messages that blame the visitor or
offer no recovery; a tone that changes between sales and support pages; and
facts that are present but hard to locate or quote.

Rules that decide whether a result is usable:
  - Every result MUST cite at least one supplied evidence_id in evidence_refs.
  - A result whose severity is high or critical MUST cite at least two evidence
    ids from independent sections.
  - `analysis_type` may be `customer_sentiment` ONLY when every cited section is
    `customer_voice`. Otherwise use `content_tone` or
    `predicted_visitor_friction`.
  - `confidence` is your calibrated probability (0.0-1.0) that a careful human
    reviewer looking at the same sections would agree. Below 0.65, omit the
    result entirely rather than reporting it weakly.
  - `quote` must be a verbatim substring of one cited section, at most 200
    characters. It is checked against the source; a paraphrase is rejected.
  - Absence of evidence is never neutral sentiment. If an aspect is not covered
    by the supplied sections, say nothing about it.

RESPONSE SCHEMA:
{"observations": [{
  "title": "short, specific, no site name required",
  "aspect": "<allowed aspect>",
  "sentiment": "<allowed sentiment>",
  "emotion": "<allowed emotion>",
  "severity": "<allowed severity>",
  "source_kind": "<allowed source_kind>",
  "analysis_type": "<allowed analysis_type>",
  "confidence": 0.0,
  "quote": "verbatim substring of a cited section",
  "evidence": "what the cited sections show, in one or two sentences",
  "evidence_refs": ["<evidence_id>", "..."],
  "suggested_action": {"summary": "what to change", "validation": "how to check it landed"}
}]}

Return {"observations": []} when the copy gives you nothing solid. That is a
correct and expected answer.
