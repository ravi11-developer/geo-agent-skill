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
| mokobara.com, iiit.ac.in, smilefoundationindia.org | "Publish an XML sitemap" | all three publish a valid sitemap index; the body was discarded because `_fetch` stored a body only for an HTML content type, and sitemaps are `application/xml` | non-HTML text bodies captured into `page.text_body`; the recommendation now requires `sitemap_checked` and a negative `sitemap_present`, not merely an empty URL list |
| any site with a sitemap index | 0 sitemap URLs | every `<loc>` was read as a page URL, so an index yielded a list of child *sitemaps* and no pages | index and urlset distinguished by wrapper element; indexes followed one level, bounded |

## False negatives worth the same discipline

Two failures in this skill were invisible rather than noisy, which is worse: they
removed a check entirely instead of making it loud.

| symptom | why it was missed | fix |
|---|---|---|
| the AI-crawler block check never fired on any real site | robots.txt is `text/plain`, so its body was discarded; `snapshot.robots_txt` was always empty and the check was dead code in production | body captured via `accept_text=True` |
| a shared `User-agent` rule block reported only its last agent | the parser reset the agent list on every `User-agent` line, contrary to RFC 9309 s2.2.1 | groups accumulate consecutive agent lines |

Neither was caught by the benchmark because **no synthetic fixture served a
robots.txt or a sitemap.xml at all** - the checks had nothing to read, so they passed
by vacancy. `site-017-robots-blocks-ai` and `site-018-sitemap-index-healthy` exist to
close that gap; both are served at the root of their own origin, because robots.txt and
sitemap.xml are origin-level documents that a fixture under a path prefix cannot own.

## Regression fixtures

`tools/stress_test.py` reproduces the shapes above (JS widget on a content-rich page,
SPA shell, noindex on a cart URL, charset-lying page) so the guards stay honest.
