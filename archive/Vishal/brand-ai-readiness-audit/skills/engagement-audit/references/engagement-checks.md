# Engagement Reference

## User Journey Model
AI answer → landing page → orientation → relevant content → next logical action.

## High-Value Checks

### Orientation (Can the visitor tell what the page is about?)
- Look for `<h1>` — the primary heading should summarise the page purpose
- Check heading hierarchy (`h1 > h2 > h3`) for structure
- Count `<p>` elements — fewer than 2 suggests insufficient context

### Navigation Health
- Count internal `<a href>` links — 0 = dead-end page (severity: high)
- Fewer than 3 internal links = weak navigation (severity: medium)
- Probe a sample of internal links (up to 5) for HTTP 4xx/5xx responses

### Breadcrumbs & Context
- Look for `<nav>` elements with class or aria-label containing "breadcrumb"
- Look for `<ol class="breadcrumb">` or similar patterns
- Flag deep pages (URL path depth ≥ 2) without breadcrumbs

### Calls to Action
- Count `<button>` elements
- Count `<a>` links with class containing "btn", "button", or "cta"
- 0 CTAs is a recommendation, not necessarily a defect

### Mobile Readiness
- Check for `<meta name="viewport">`
- Missing viewport = page may not render on mobile (severity: medium)

### Dead-End Detection
- A page with 0 internal links is a dead end
- A page with only external links and no way back to the site is also a dead end

## False-Positive Rules
- Minimal design ≠ poor engagement
- Don't recommend intrusive popups
- Don't infer conversion rates without analytics
