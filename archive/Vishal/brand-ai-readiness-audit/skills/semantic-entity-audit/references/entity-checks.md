# Semantic/Entity Reference

## Prioritize Facts Users Would Ask About
- Who the organization is
- What it sells/provides
- Where it operates
- What a product/service does
- Material attributes: price, specifications, availability

## Specific Checks

### Page Title
- Look for `<title>` tag
- Empty or missing title = severity: high
- Should name the entity and describe the page purpose

### Meta Description
- Look for `<meta name="description" content="...">`
- Missing = severity: medium
- Should summarise the explicit facts and offerings

### JSON-LD Structured Data
- Look for `<script type="application/ld+json">`
- Parse with `json.loads()` — syntax errors = severity: high
- Extract `name` field (handles `@graph` arrays)
- Compare `name` against visible page text — if not found, data may be stale
- Common types to look for: Organization, Product, Service, Article, LocalBusiness

### OpenGraph Tags
- Look for `<meta property="og:title">`, `og:description`, `og:type`, `og:image`
- No OG tags + no JSON-LD = severity: high (no machine-readable data)

### Page-Intent Detection
- Can a machine tell if this is a product page vs. article vs. contact page?
- Signals: JSON-LD `@type`, `og:type`, heading content, meta description
- If none of these exist, intent is ambiguous = severity: medium (recommendation)

### Identity Consistency (Cross-Page)
- Compare `Organization.name` in JSON-LD across all sampled pages
- Compare brand suffix in `<title>` tags (e.g., "Page Name | BrandX")
- Different names = severity: high (AI may treat them as different entities)
- Different title suffixes = severity: medium (recommendation)

## For Structured Data Validation
- Accuracy and consistency matter more than schema volume
- Don't flag optional schema types just because they're absent
- Marketing language ≠ factual claim unless explicitly presented as one
