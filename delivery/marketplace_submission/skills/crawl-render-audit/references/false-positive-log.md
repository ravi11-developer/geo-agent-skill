# False-positive log

Each entry is a real site that broke an earlier version of this skill, and the rule
change that fixed it. Kept as a regression checklist: any future loosening of these
gates must be re-tested against these cases.

| site | what fired | why it was wrong | fix |
|---|---|---|---|
| bluetokaicoffee.com | `rendering` | inline JS fills a loyalty modal with "Loading offers..." on a page carrying 672 words of served copy | injection must be load-bearing: thin page, or prices only in the payload |
| smilefoundationindia.org | `rendering` | captcha error text ("Incorrect sum. Try again.") injected into an empty span, 2,531 words served | same rule |
| qdrant.tech | `rendering` | newsletter form replaced by a tracker-blocked notice, 925 words served | same rule |
| manipalhospitals.com | `rendering` | Brightcove video player and banner carousel injected, 285 words served | thin-page bar lowered to the 60-word shell threshold |
| sulekha.com, val.town | `crawlability` | `noindex` on `/search` and `/auth/signin` | transactional paths excluded from the noindex rule |
| harborline (multi-page fixture) | mojibaked titles | requests fell back to ISO-8859-1 for `text/html` with no charset parameter | charset resolution order documented in `detection-rules.md` |

## Regression fixtures

`tools/stress_test.py` reproduces the shapes above (JS widget on a content-rich page,
SPA shell, noindex on a cart URL, charset-lying page) so the guards stay honest.
