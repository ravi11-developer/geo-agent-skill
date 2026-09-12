# Remediation playbook — semantic engagement

How a finding in this category should be written up. Every entry follows the
same shape: what the reader experiences, the lever that fixes it, and the
acceptance test that proves it landed. The machine-readable version of this
table lives in `lib/llm/playbook.py` and is fed to the suggestion prompt as
verified context.

## Value proposition

*Reader experience:* cannot say what the company does after reading the hero.
*Lever:* say what it is, who it is for, and what changes for them, in the first
two sentences.
*Acceptance:* a reader can restate the offer in one sentence without scrolling.

## Pricing transparency

*Reader experience:* "from ₹X" with no unit, period or exclusions; a quote form
where a number was expected.
*Lever:* state the number, the unit, the billing period and what is excluded.
*Acceptance:* the total cost of one common case can be computed from the page.

## Shipping

*Reader experience:* cannot tell whether the order arrives before the date they
need it.
*Lever:* give destination, window, cost and cut-off explicitly, as separate
statements.
*Acceptance:* a buyer can answer "will it arrive by Friday" from the page alone.

## Returns

*Reader experience:* eligibility, deadline and exclusions run together in one
paragraph, so the reader assumes the worst.
*Lever:* separate eligibility, deadline, exclusions and the request action.
*Acceptance:* each condition and the next action can be extracted independently.

## Support

*Reader experience:* a contact form and nothing about when anyone will reply.
*Lever:* name the channel, the hours and the expected response time.
*Acceptance:* a visitor knows how and when they will get an answer.

## Trust and credibility

*Reader experience:* superlatives with nothing behind them ("the most trusted"),
which reads as weaker than a plain fact.
*Lever:* attach evidence to each claim — a certificate, a date, a named source.
*Acceptance:* no superlative stands without something checkable next to it.

## CTA clarity

*Reader experience:* "Get started" — start what, and what happens after the
click?
*Lever:* name the outcome, not the gesture.
*Acceptance:* the label alone tells the reader what happens next.

## Error and validation messages

*Reader experience:* "Invalid input" in red, no indication of which field or what
would be valid; worse, wording that blames them.
*Lever:* describe what happened and the recovery step, in the site's normal
voice.
*Acceptance:* the message names an action the reader can take right now.

## Tone consistency

*Reader experience:* a warm product page followed by a legalistic returns page,
which reads as two different companies.
*Lever:* keep the voice of support and policy pages continuous with the sales
pages.
*Acceptance:* a reader moving between them notices no change of speaker.

## Cross-page contradiction

*Reader experience:* the FAQ says 30 days, the policy page says 14.
*Lever:* pick the true one, fix the other, and cite both page URLs in the
finding so the owner can see the pair.
*Acceptance:* every page states the same number, and the finding's two evidence
ids resolve to the two pages that disagreed.

## Writing the finding itself

- Quote the copy. A finding a content editor cannot locate is a finding they
  will not act on.
- Name the page or the template, never "the site".
- Say what a reader would conclude, not what the writing "feels like".
- Never claim the copy proves how customers feel — that requires
  `customer_voice` evidence, and this section says so out loud when reviewers
  ask why the wording is so careful.
