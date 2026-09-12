# AI Orchestration and Reasoning Rules

You (the Host Agent) must use the evidence gathered by the Python scripts to generate a final report. Do NOT hallucinate findings. Every finding MUST be backed by evidence in the JSON payload.

## False-Positive Filtering Rules
1. **Missing JSON-LD is not always critical:** If JSON-LD is missing, but the HTML text is extremely clear and well-structured, downgrade this to a "medium" or "low" severity. Only mark as high if BOTH JSON-LD is missing AND the HTML is a mess.
2. **JS-Heavy Sites:** If a site uses JS to render, but the initial HTML contains the core text (e.g., Next.js SSR), it is NOT a defect. Only flag if the difference between raw and rendered HTML hides core factual content.

## Mechanism-Based Recommendations
- Your suggested actions must address the specific mechanism. 
- **BAD:** "Improve your SEO."
- **GOOD:** "Implement schema.org/Organization JSON-LD on the homepage to ensure AI assistants can explicitly extract your brand facts without relying on raw text parsing."

## Severity Assignment
Consult `../../config/severity_rules.json` to map your findings to the correct priority bucket.
