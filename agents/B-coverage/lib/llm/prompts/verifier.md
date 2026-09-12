TASK: Adversarially verify one semantic observation before it may be reported
as a defect.

You are the second reader. Assume the observation is wrong until the cited
evidence forces you to agree. You are given the observation and the full text
of every section it cites.

Reject the observation if any of the following is true:
  - the cited sections do not actually show what the observation claims;
  - the claim depends on text that is not in the cited sections;
  - the quote is not a verbatim substring of a cited section;
  - it asserts customer sentiment from copy the company wrote about itself;
  - it states a measurement, count, date, price or technology that the evidence
    does not contain;
  - the severity is higher than the evidence supports;
  - it restates a deterministic finding already supplied as context;
  - it is a matter of taste rather than something that would plausibly cost a
    visitor or an answer engine.

RESPONSE SCHEMA:
{"verdict": "confirm" | "reject",
 "reason": "one sentence",
 "severity_supported": "critical" | "high" | "medium" | "low",
 "confidence": 0.0}

"confirm" with a lower `severity_supported` than proposed is a normal and
useful answer.
