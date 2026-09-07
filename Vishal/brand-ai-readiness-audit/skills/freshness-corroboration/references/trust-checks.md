# Freshness/Corroboration Reference

## Good Evidence Describes
- The exact claim (e.g., "phone number is +1-555-1234")
- The pages/sources compared (with URLs)
- Observed dates when available
- The conflicting or corroborating values

## Specific Checks

### Copyright Year
- Regex: `(?:copyright|©)\s*(?:(\d{4})\s*[-–]\s*)?(\d{4})`
- Flag if the latest year is more than 1 year behind current year
- Severity: medium (signals maintenance neglect)

### Last-Modified HTTP Header
- Read the `Last-Modified` response header
- Parse with `email.utils.parsedate_to_datetime`
- Flag if content is older than 365 days

### Meta Date Tags
- Look for `<meta property="article:published_time">`
- Look for `<meta property="article:modified_time">`
- Look for any meta tag with "date" or "time" in property/name
- Missing dates = recommendation (severity: low)

### Internal Fact Consistency
- Extract phone numbers: `[\+]?[\d][\d\-\.\s\(\)]{7,15}\d`
- Extract email addresses: standard email regex
- Compare across all sampled pages
- Different values for the same fact type = high severity

### Date Range Analysis
- Extract all 4-digit years (20xx) from visible text
- If the range (max - min) > 5 years and oldest < current - 3, flag as recommendation
- May indicate mixed-age content where old facts are presented alongside new

## Phrasing Rules
- Avoid: "AI will not trust this site"
- Prefer: "This conflict makes the fact harder to resolve consistently"
- Phrase conflicts as: "source A says X; source B says Y"
