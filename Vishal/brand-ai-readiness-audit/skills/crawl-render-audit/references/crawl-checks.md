# Crawl/Render Reference

## Evidence Hierarchy
1. Direct fetch/render observations (HTTP status, headers, raw HTML).
2. URL-level comparisons across raw and rendered content.
3. Site-wide patterns supported by a meaningful sample.

## Useful Sample
- Homepage
- 2–5 high-value category/listing pages
- 3–10 product/service pages
- 1–3 informational/about/contact pages

Always record sample size when making a site-wide statement.

## Specific Checks

### robots.txt
- Fetch `{base_url}/robots.txt`
- Parse with `urllib.robotparser`
- Check `can_fetch("*", target_url)` — if blocked, severity = critical
- Extract `Sitemap:` directives for sitemap discovery

### Sitemaps
- Try sitemap URLs from robots.txt first, then fallback to `/sitemap.xml`
- Validate that the response is XML (check content-type or first bytes)
- A missing sitemap is not critical if pages are otherwise discoverable via links

### Redirect Chains
- Track every 3xx hop during fetch
- Flag chains with more than 2 hops (slowness + potential truncation)
- Record the full chain in evidence

### Canonical Tags
- Look for `<link rel="canonical" href="...">`
- Resolve to absolute URL and compare with the fetched URL
- Mismatched canonical = crawlers may ignore this page

### Content Behind JavaScript
- Count internal `<a href>` links in raw HTML
- If page has > 2KB of HTML but < 3 internal links, JS rendering is likely hiding navigation
- Compare visible text length vs. total HTML size

### Content Locked in Images
- Count `<img>` tags without `alt` attributes
- If > 3 images lack alt text, important info may be invisible to crawlers
- Decorative images with `alt=""` are acceptable
