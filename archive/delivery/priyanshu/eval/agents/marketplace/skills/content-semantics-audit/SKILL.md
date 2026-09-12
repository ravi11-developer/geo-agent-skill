---
name: content-semantics-audit
description: Measures whether the facts a buyer would ask about are present as machine-readable text - extractable body content, JSON-LD/microdata structured data, and facts locked inside images with no text equivalent.
version: 1.0.0
license: MIT
kind: audit
provides: [content_extraction, structured_data, non_text_facts]
consumes: [snapshot]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot, performs no network calls
---

# Content & Semantics Audit

## Responsibility

Given the crawl snapshot, answer three questions:

1. **Can a text-only crawler lift the facts out of the HTML?** -> `content_extraction`
2. **Are those facts also stated in schema?** -> `structured_data`
3. **Are any of them trapped in pixels?** -> `non_text_facts`

## Fact coverage (the unit of measurement)

Rather than counting markup, the skill measures which of four *fact families* can be
extracted from the raw HTML text of each page:

| family | probe |
|---|---|
| `pricing` | currency amounts, `per month`, `/mo`, `/year` |
| `contact` | email addresses, phone numbers |
| `company_facts` | founded / headquarters / employees / customers / revenue / certifications, thousands separators, percentages |
| `product_detail` | two or more headed sections carrying >= 15 words of prose, or >= 120 words overall |

Reporting on fact coverage - rather than on cosmetic markup - is what keeps this skill
silent on pages that are unusual but healthy.

## Detection rules

**`content_extraction`** (severity **high**) requires *three independent conditions* to
agree, which is why it does not fire on merely thin or partially dynamic pages:

- at most **one** of the four fact families is present in the extractable text, **and**
- the page carries fewer than **60 words** of content text, **and**
- an identifiable cause exists on that same page: client-side rendering, or three or
  more fact-bearing images.

It must also affect the entry page, not only a sub-page.

**`structured_data`**:

- JSON-LD present but unparseable -> **high** (the parse error is quoted);
- zero JSON-LD, microdata and RDFa across every crawled page -> **medium**;
- present but incomplete (no Organization node, no Product/Offer, no FAQ) -> *recommendation only*.

Precondition on the "zero structured data" finding: the site must already publish a
metadata layer (canonical link, OpenGraph tags, or a meta description of >= 50
characters). A site with no metadata whatsoever has one root cause, not two, and
reporting both would double-count it; there the gap is raised as a recommendation.

**`non_text_facts`** counts images that (a) are outside header/footer/nav, (b) are not
logos or icons, (c) have a fact-bearing filename or sit under a fact heading (pricing,
comparison, matrix, infographic, results, contact, ...), (d) have missing, generic or
under-4-word alt text and no `<figcaption>`, and (e) sit in a section with fewer than
30 words of prose that could restate them. Two or more such images -> **medium**;
four or more -> **high**.

## Precision guards

- A single weak-alt image is a recommendation, not a finding.
- Multiple JSON-LD blocks, verbose CSS, long titles and multiple H1s are explicitly
  *not* defects: none of them stops extraction.
- Facts that are merely *also* available via JavaScript are reported by
  `crawl-render-audit` as `rendering`; `content_extraction` is reserved for pages
  where a text-only crawler ends up with essentially nothing to cite.
