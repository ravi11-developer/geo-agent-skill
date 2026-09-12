# Detection rules: content & semantics

## content_extraction (high)

Three independent conditions must agree, which is why it does not fire on merely thin
or partially dynamic pages:

1. at most **one** of the four fact families present in extractable text, **and**
2. fewer than **60 words** of content text (nav/header/footer excluded), **and**
3. an identifiable cause on that same page: client-side rendering (inline injection
   or a `<noscript>` gate) or >= 3 fact-bearing images.

It must also affect the entry page - a single thin sub-page is not a site-level defect.

Evidence quotes: extractable characters and words, which fact families are missing,
the headings that promise facts the text never delivers, the measured cause, and -
when a JS payload exists - the words a simulated render recovers.

## structured_data

| state | outcome |
|---|---|
| a JSON-LD block fails `json.loads` | finding, `high`, parse error and first 60 chars quoted |
| no JSON-LD, no `itemtype`, no `typeof` anywhere | finding, `medium` |
| present but no Organization node | recommendation |
| present but no Product/Offer node | recommendation |
| present and complete | recommendation to add FAQPage, and to validate schema in CI |

Sub-pages without their own Organization block are **not** flagged: schema is assessed
at site level, because that is how consumers read it.

Note: an earlier version suppressed the "no structured data" finding on sites that also
lacked canonical/OpenGraph/description, to match one synthetic gold label. Real-web
testing showed that suppression hiding genuine missing-schema defects on eight
government and university sites, so it was removed. The metadata gap is now reported as
a recommendation alongside the finding.

## non_text_facts

An image counts as fact-bearing without a text equivalent when **all** hold:

- it is outside `nav`/`header`/`footer`;
- its filename does not start with `logo` and contains no decorative token
  (`icon`, `favicon`, `avatar`, `sprite`, `spacer`, `pixel`, `arrow`, `chevron`,
  `close`, `hamburger`, `menu-toggle`, `screenreader`, `font-`, `social`, `share`,
  `thumb`, `placeholder`, `loader`, `spinner`, `bullet`, `divider`, `badge-`);
- a **whole token** of its filename or nearest preceding heading is a fact word
  (pricing, price, tariff, plan, tier, table, chart, comparison, matrix, infographic,
  spec, datasheet, feature, menu, catalogue, brochure, results, benchmark, stats,
  timetable, schedule, fees, packages, partner, customer, client, award, contact,
  roadmap);
- alt text is missing, empty, generic, or under four words, and there is no `<figcaption>`;
- the hosting section carries < 30 words of prose that could restate the facts.

Severity: >= 4 images `high`, 2-3 `medium`, exactly 1 becomes a recommendation.
