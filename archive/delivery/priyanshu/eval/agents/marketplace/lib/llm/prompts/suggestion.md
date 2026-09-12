TASK: Improve the remediation guidance for website audit findings that have
already been detected and verified by deterministic code.

You are given findings that are CONFIRMED. Do not re-litigate them, do not
change their severity or priority, and do not add or remove findings. Your only
job is to make the fix concrete for the team that has to implement it.

For each finding, return an object with:
  finding_id            the id exactly as supplied
  root_cause            one sentence, grounded in the supplied evidence
  recommendation        what should change, specific to this page or template
  implementation_steps  2-6 imperative steps, each independently checkable
  where                 the page URL or template the change applies to; use only
                        supplied URLs
  example               OPTIONAL short copy or markup sketch (<= 400 chars)
  expected_impact       what improves for AI retrieval or for the visitor
  effort                one of: low, medium, high
  owner                 one of: content, engineering, design, marketing, seo
  acceptance_test       an observable check that proves the fix landed
  evidence_refs         evidence ids you relied on, from the supplied list only

Reject your own draft and omit the finding entirely if you cannot name the
affected page or template, a concrete change, and an acceptance test. A generic
recommendation is worse than none: the deterministic recommendation is kept
whenever you omit a finding.

RESPONSE SCHEMA:
{"suggestions": [ { ...one object per finding you improved... } ]}
