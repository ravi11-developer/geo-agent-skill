TASK: Repair a structured response that failed validation.

You are given the schema violations, your previous response, and the exact list
of evidence ids you are permitted to cite. Return a corrected JSON document in
the same schema.

Fix only what the violations name. Do not add new results, do not raise any
severity, and do not introduce any evidence id outside the permitted list. If a
result cannot be corrected without inventing something, delete that result: a
shorter valid response is the goal, and an empty result list is acceptable.

Return only the corrected JSON object.
