# Sentiment taxonomy

The closed vocabularies this skill is allowed to use. They are enforced twice:
once in `lib/contracts.py::make_observation` and again in
`lib/llm/validation.py::validate_observations`. Anything outside them is
rejected rather than coerced, so a drifting model produces fewer results rather
than wrong ones.

## Source kind — what was actually read

| value | means | may support |
|---|---|---|
| `brand_copy` | marketing, product, policy or support copy the site wrote about itself | `content_tone`, `predicted_visitor_friction` |
| `customer_voice` | reviews, testimonials, survey answers, support ticket text | all three, including `customer_sentiment` |
| `system_message` | validation errors, empty states, transactional notices | `content_tone`, `predicted_visitor_friction` |
| `unknown` | could not be classified | nothing is promoted from it |

## Analysis type — what is being claimed

| value | claim |
|---|---|
| `content_tone` | how the site's own writing reads |
| `predicted_visitor_friction` | what a reader would plausibly struggle with |
| `customer_sentiment` | how real customers actually feel |

`customer_sentiment` over `brand_copy` is the single most tempting error in this
whole area, and it is the reason the source kind is carried on every section
rather than inferred later. A company describing its own returns policy warmly
is evidence about the copy, not about the customers.

## Aspects

`value_proposition` · `product_clarity` · `pricing_transparency` ·
`product_quality` · `shipping` · `returns` · `support` · `trust_credibility` ·
`cta_clarity` · `forms_validation` · `error_messages` · `navigation` ·
`reviews_testimonials` · `tone_consistency`

An aspect is only reportable when the crawled pages actually cover it. A site
with no shipping page produces no `shipping` observation — not a neutral one.

## Sentiment

`positive` · `neutral` · `negative` · `mixed` · `unknown`

`unknown` is the correct answer when the section is factual boilerplate.
`mixed` means the same aspect is treated differently in different places, which
is usually the more interesting finding of the two.

## Emotional indicators

`trust` · `reassurance` · `clarity` · `confidence` — what well-written copy
produces.

`confusion` · `uncertainty` · `frustration` · `anxiety` · `pressure` ·
`urgency` · `blame` — what costs a visitor.

`pressure` and `urgency` are separated deliberately: a genuine deadline is
`urgency` and is legitimate; a manufactured countdown is `pressure` and is a
defect in the copy.

## Confidence bands

| band | meaning | outcome |
|---|---|---|
| `>= 0.80` | a careful reviewer would very likely agree | eligible for promotion once evidence validates |
| `0.65 – 0.79` | plausible, needs support | promoted only with two independent sections on different pages, or deterministic corroboration |
| `< 0.65` | not supported | abstain — the result is dropped |

Self-reported confidence is never the only signal. It is combined with the
number of cited sections, whether those sections come from different pages,
whether a deterministic check agrees, and — for `high` and `critical` — an
adversarial verifier pass.

## Severity

`critical` · `high` · `medium` · `low`, with the same meaning as the
deterministic categories. A semantic observation is capped by the promotion
policy and may be lowered by the verifier; it is never raised.
