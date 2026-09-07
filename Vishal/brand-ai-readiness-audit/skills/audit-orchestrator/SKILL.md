---
name: audit-orchestrator
description: Compose specialist website audits for AI discoverability and on-site engagement into one evidence-backed, prioritized report. Use when auditing an arbitrary public website for why AI systems may fail to find, read, understand, verify, cite, or use its information, and why arriving visitors may fail to engage. Covers both on-site and off-site discoverability.
license: Apache-2.0
tools: [fetch]
---

# Audit Orchestrator

## When to use
Use when the input is a public website URL or domain and a combined AI-readiness audit is requested.

## Inputs
- Target URL/domain.
- Optional crawl budget and page sample size (default: 15 pages).

## Procedure
1. Normalize the target URL and establish a bounded, read-only crawl budget (default 15 pages).
2. Fetch the homepage; discover internal links and classify them into page types (category, product, info, other).
3. Sample a bounded set of representative pages across all types.
4. Respect `robots.txt`; do not access authenticated/private areas.
5. Invoke all five specialist audits on the sampled pages:
   - `crawl-render-audit`: reachability, robots/sitemap, HTTPS, redirects, canonical, noindex/nofollow, JS-rendering, page size, mixed content.
   - `semantic-entity-audit`: title, meta, JSON-LD/OG/Twitter Cards, entity identity, sameAs, FAQ/speakable schema, quotability, E-E-A-T, duplicate content.
   - `freshness-corroboration`: copyright year, Last-Modified, date meta, fact consistency (phone/email/address), email readability (Appendix F).
   - `engagement-audit`: headings (hierarchy, multiple H1), navigation, breadcrumbs, CTAs, broken links, viewport, word count, lang, hreflang, FAQ content, form accessibility.
   - `offsite-discoverability-audit`: sameAs anchoring, brand disambiguation, content quotability, Organisation schema quality, press/media mentions, contact information, AI-optimised schema types, direct-answer content (Appendix B, D, E).
6. Require each specialist to return findings with stable IDs, evidence, severity, confidence, affected URLs/pages, type (defect or recommendation), and a mechanism-based suggested action.
7. Merge findings. De-duplicate only when the root cause and remediation are materially the same; otherwise retain distinct findings.
8. Prefer concrete evidence over generic best-practice claims. Do not report absence as a defect unless the missing element materially impairs discoverability or engagement for the observed site.
9. Assign severity using impact and breadth:
   - critical: prevents discovery/use of major public content or creates a severe site-wide failure.
   - high: materially impairs discovery, interpretation, trust, or engagement on important pages.
   - medium: meaningful but bounded weakness with a practical impact.
   - low: minor or localized improvement.
10. Include proactive improvements only when they are specific to observed site characteristics; label them with `"type": "recommendation"` rather than `"type": "defect"`.
11. Re-assign sequential finding IDs (F-001, F-002, …) after deduplication.
12. Sort findings by severity (critical first) and emit exactly one JSON-compatible report.

## Output
```json
{
  "site": "example.com",
  "audited_at": "ISO-8601 timestamp",
  "pages_sampled": 12,
  "sample_breakdown": {"homepage": 1, "category": 3, "product": 4, "info": 2, "other": 2},
  "summary": {
    "total_findings": 0,
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0
  },
  "findings": [
    {
      "id": "F-001",
      "title": "Short, specific issue title",
      "severity": "high",
      "confidence": "high",
      "type": "defect",
      "evidence": "Concrete observed evidence with scope.",
      "affected_urls": ["https://example.com/page"],
      "suggested_action": {
        "summary": "What to change and how.",
        "priority": "high"
      }
    }
  ]
}
```

Every finding must contain `id`, `title`, `severity`, `confidence`, `type`, `evidence`, `affected_urls`, and `suggested_action`. Keep the report actionable and avoid unsupported claims about how any particular AI provider ranks sources.
