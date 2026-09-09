---
name: content-semantics-audit
description: Measures whether the facts a buyer asks about are present as machine-readable text - extractable body content, JSON-LD/microdata structured data, and facts locked inside images with no text equivalent. Use after crawl-render-audit; reads the shared snapshot and makes no network calls.
version: 1.1.0
license: MIT
kind: audit
provides: [content_extraction, structured_data, non_text_facts]
consumes: [snapshot]
produces: [fact_coverage]
allowed-tools: []
safety: read-only; operates entirely on the shared snapshot; no network access
---

# Content & Semantics Audit

## When to use

After the snapshot exists. Answers three questions:

1. Can a text-only crawler lift the facts out of the HTML? -> `content_extraction`
2. Are those facts also stated as schema? -> `structured_data`
3. Are any of them trapped in pixels? -> `non_text_facts`

## Run it

As a skill: `run(context) -> SkillResult`, with `context["artifacts"]["snapshot"]`.

## The unit of measurement: fact coverage

Rather than counting markup, the skill measures which of four **fact families** can be
extracted from the raw HTML text of each page:

| family | probe |
|---|---|
| `pricing` | currency amounts, `per month`, `/mo`, `/year` |
| `contact` | email addresses, phone numbers |
| `company_facts` | founded / HQ / employees / customers / revenue / certifications, thousands separators, percentages |
| `product_detail` | >= 2 headed sections with >= 15 words of prose, or >= 120 words overall |

Reporting on fact coverage rather than on cosmetic markup is what keeps this skill
silent on pages that are unusual but healthy.

## What it emits

| category | severity | fires when |
|---|---|---|
| `content_extraction` | high | <= 1 fact family present **and** < 60 words **and** an identifiable cause (JS shell or >= 3 fact images), on the entry page |
| `structured_data` | high | JSON-LD present but unparseable (the parse error is quoted) |
| `structured_data` | medium | zero JSON-LD, microdata and RDFa across every crawled page |
| `non_text_facts` | high / medium | >= 4 / >= 2 fact-bearing images with no text equivalent |

## Precision guards

- Multiple JSON-LD blocks, verbose CSS, long titles and several H1s are explicitly
  **not** defects - none of them stops extraction.
- Image filenames are matched on whole tokens, never substrings: `global-map.webp`
  is not a "map" fact, `mobilemenu_close.png` is not a "menu" fact.
- Facts merely *also* available via JavaScript are `rendering`'s business;
  `content_extraction` is reserved for pages that leave a text crawler with nothing.

## See also

- `references/detection-rules.md` - full thresholds, image heuristics, schema handling
- `references/fact-families.md` - what counts as a buying fact, and why these four
