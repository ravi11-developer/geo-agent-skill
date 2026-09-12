# Detection rules: engagement & orientation

## Counting

- **Nav landmarks**: `<nav>` elements plus any element with `role="navigation"`.
- **Internal links**: unique absolute URLs on the entry host, excluding `#`, `mailto:`,
  `tel:`, `javascript:` and `data:`.
- **Breadcrumbs**: an `aria-label` or class matching `breadcrumb`, or a BreadcrumbList
  entry in structured data.
- **Call to action**: body text matching contact / get in touch / book a / request a /
  start free / sign up / talk to / demo / quote / pricing.

## The finding

`engagement` (high) requires **all** measurable pages to have zero nav landmarks and
fewer than two internal links, and the entry page must be among them. That is a site of
dead ends, not a site with thin navigation.

Evidence quotes the landmark count, both link counts, how many of the three orientation
landmarks (header, footer, breadcrumbs) exist, and how many of the crawled pages share
the pattern.

## The shell exemption

A page is unmeasurable when its DOM is built entirely by JavaScript: fewer than 60 words
of served text plus an injection marker (empty framework root, JS-filled empty element,
or a `<noscript>` gate). Those pages are removed from the population before the rule is
applied. If nothing measurable remains, the skill emits:

> "Re-check navigation once the initial HTML is server-rendered"

as a recommendation. Reporting missing navigation on an unrendered shell would be a
second finding for a cause `rendering` already owns.

## Advice, not defects

| observation | why it is advice |
|---|---|
| generic anchor text | the link works; the wording is a quality matter |
| no breadcrumbs, but nav exists | orientation is present, just shallower |
| no `<footer>` landmark | site-wide context is missing, nobody is stranded |
| no call to action | conversion issue, not a discoverability defect |
| multiple H1s, stuffed titles, underscore URLs | classic SEO lint; AI extraction is unaffected |
