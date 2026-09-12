# Real-web benchmark: 100+ hard-to-cite sites

The synthetic suite proves an agent behaves correctly on faults we planted. It cannot
prove the agent survives the real web: broken markup, cookie walls, redirect chains,
mis-declared encodings, 3MB of tag soup, bot protection. This benchmark does that, on a
curated corpus of sites that AI answer engines rarely cite as primary sources.

Everything runs from your own machine, because it needs ordinary outbound internet access.

## Run it

```bash
pip install requests beautifulsoup4 trafilatura        # trafilatura is only for the baseline agent

python eval/runners/capture_corpus.py                  # 1. capture the corpus  (~10-20 min)
python eval/runners/run_real_suite.py                  # 2. score every agent   (~2-5 min)
python eval/runners/validate_adjudicator.py            # 3. how trustworthy is the scoring
```

Useful flags: `--limit 20` (trial run), `--pages 6` (deeper crawl), `--agent marketplace`
(one agent), `--urls my_urls.txt` (your own site list, one URL per line),
`--out somewhere/else` (corpus location, roughly 0.5-1 MB per site).

## The corpus

`eval/sites/real/sites.json` lists 104 hand-picked domains across twelve verticals chosen
because AI assistants rarely cite the primary source in them:

| vertical | n | why citation is weak there |
|---|---|---|
| gov / civic portals | 10 | legacy templates, PDF-first content, image-only notices |
| universities & research institutes | 10 | hand-written HTML, no entity markup |
| NGOs | 8 | story-led pages, statistics inside infographics |
| D2C / retail | 10 | Shopify-style storefronts, facts inside product images |
| food & hospitality | 7 | menus published as images |
| healthcare providers | 7 | large templated sites, JS-driven listings |
| local-services marketplaces | 7 | client-rendered listings |
| Indian fintech / SaaS | 8 | SPA marketing sites, JS pricing tables |
| global SPA product sites | 18 | component-rendered content, empty shells |
| industry bodies | 6 | report-led, PDF-first |
| B2B manufacturing | 8 | spec sheets as PDFs or images |
| regional media | 5 | paywalls and heavy JS |

Domains are over-provisioned on purpose: some will be dead, renamed or bot-protected. The
capture report lists every exclusion with its reason and the manifest records exactly what
was audited, so the corpus is honest about its own coverage.

## Why the corpus is captured, not crawled live

`capture_corpus.py` fetches each site **once** and stores the bytes.

* **Politeness** - one fetch per page for the whole benchmark rather than one per agent.
  robots.txt is obeyed (a disallowed entry URL is skipped and recorded), requests to a host
  are serialised with a delay, only HTML is downloaded, and there is a 3MB per-page cap.
* **Fairness** - all six agents replay byte-identical input. A site that changes or rate
  limits between runs would otherwise score agents on different content.
* **Reproducibility** - the corpus is a directory you can re-run, diff and ship. Real-web
  numbers that cannot be re-run are anecdotes.

Storage mirrors each URL's path, so root-relative links, `robots.txt` and the original
`Content-Type` (including a missing charset, which is a real-world trap) behave in replay
exactly as they did live. Only the *host* part of same-host absolute links is rewritten,
which is what stops a replayed crawl from leaking back onto the live site. Each site is
then served from its own local replay server rooted at that site.

## How agents are scored without gold labels

`eval/scoring/adjudicate.py` re-derives each of the eight categories from the stored bytes
using a deliberately independent implementation - stdlib `html.parser` instead of
BeautifulSoup, byte-level regex instead of DOM walks, Jaccard token overlap instead of
`SequenceMatcher`. For every site and category it answers:

| label | meaning | effect on scoring |
|---|---|---|
| `yes` | concrete evidence of the defect | agent that misses it takes a false negative |
| `no` | concrete evidence against it | agent that flags it takes a false positive |
| `abstain` | genuinely ambiguous | excluded; agent findings here are counted as *unverified* |

Abstention is the point. A scorer that has to guess on every site produces confident
nonsense; this one declines, and reports how much it declined. On the 16 labelled synthetic
sites it abstains on 26 of 128 category decisions.

Alongside detection the runner reports evidence quality, suggested-action quality, output
schema validity, findings per site, recommendations per site, crash rate and runtime -
operational behaviour that separates agents on the real web far more sharply than any
synthetic site does.

## How trustworthy is the adjudicator

`validate_adjudicator.py` pushes the 16 **labelled** sites through the identical pipeline
and compares its labels against hand-written gold:

```
agreement : 0.990  (101 agree, 1 disagree)
abstained : 26 category decisions the adjudicator declined to call
disagreement: site-007-poor-navigation / structured_data  (adjudicator=yes, gold=no)
```

Quote that figure with any real-web result. The single disagreement is the documented
judgement call in `content-semantics-audit`: on a site with no metadata layer at all, the
marketplace folds missing JSON-LD into the primary finding plus a recommendation rather
than reporting it as a second defect, while the adjudicator reports it literally.

**Limitations, stated plainly:**

1. The adjudicator was written by the same author as the marketplace agent. It uses
   different code and abstains where unsure, and its accuracy is measured rather than
   assumed - but it is not an impartial third party.
2. It reads the same stored HTML the agents read, so a fault visible only in a rendered
   DOM (or only to a human) is invisible to both.
3. `run_real_suite.py` writes `realweb_review_sample_*.csv` with an empty `human_verdict`
   column, holding every finding that the adjudicator called `no` or `abstain`. Hand-label
   a few dozen rows and you have a human-validated precision estimate that answers the
   independence objection directly. Do this before quoting the numbers to judges.

## Reading the results

- **Precision** matters more than recall here. On a real site, a false positive is advice
  that wastes an engineering week; the rubric penalises it, and so does a customer.
- **Unverified rate** is not failure. It is the share of an agent's findings that landed in
  the adjudicator's ambiguous zone: candidates for the human review sample, not errors.
- **Findings per site** exposes shotgunning. An agent averaging ten findings per site on
  the open web is describing SEO habits, not AI discoverability defects.
- **Crash rate and runtime** are where real HTML separates agents that were only ever run
  against tidy fixtures.


---

## What the first real-web run actually found (88 sites, 326 pages)

104 domains were attempted; 88 captured. 16 failed and the manifest records why: HTTP 403
bot protection (india.gov.in, msme.gov.in, apollohospitals, maxhealthcare, justdial,
freshworks, meesho, ficci, iso.org), expired or misconfigured TLS (iiitd.ac.in,
isical.ac.in, hri.res.in, cafecoffeeday.com), HTTP 500/503 (mcgm, kirloskar) and a 406
(housing.com). That failure profile is itself a finding: a meaningful slice of the Indian
public-sector and enterprise web is unreachable to a well-behaved crawler, which is the
first reason an assistant cannot cite it.

The run exposed **five real defects in the marketplace agent and four in the adjudicator**.
Every one was arbitrated against the stored HTML, not against the other component:

**Agent fixes**

1. `rendering` fired on any empty element filled by inline JS - which on real sites means
   cart drawers, captcha errors, cookie banners and video players. It now requires either a
   page thin enough to be a shell, or *prices present in the injected markup and absent from
   the served text*. Comparing values rather than the presence of fact-ish words is what
   separates a pricing widget from a "Loading offers..." modal.
2. `non_text_facts` matched fact keywords as substrings, so `global-map.webp` matched "map"
   and `mobilemenu_close.png` matched "menu". Now whole-token matching, with UI chrome
   (arrows, spinners, share icons, font-size controls) excluded.
3. `freshness` read `© 2020-26` as the year 2020, because the range end is two digits. It
   now takes the latest year of a range, and a stale copyright on a site that publishes
   current-year content is demoted to a recommendation rather than reported as stale content.
4. `entity_identity` counted every distinct name string, so sub-page titles ("Online Rent
   Agreement", "Scaling Render Services") looked like competing company names. It now anchors
   on a canonical name - the medoid of the names that behave like site identity - and counts
   only spellings that are variants *of that name*.
5. `crawlability` flagged `noindex` on cart, login, account and search URLs, which is correct
   practice, not a defect. Those paths are now excluded.

**Adjudicator fixes**

1. Years were read from the raw document including inline scripts, so version numbers and
   timestamps made text-light sites look years stale (122 "stale mentions" on resend.com).
   Now visible text only - but including header and footer, because that is where the
   copyright year lives.
2. Same substring bug as the agent, fixed the same way.
3. Same phantom-entity bug, fixed the same way, and "no" is now claimed only when a site
   speaks with exactly one name; two unrelated names is an abstention.
4. `noindex` on transactional paths excluded.

After those fixes, on the same 88 sites:

| | before | after |
|---|---|---|
| marketplace precision | 0.950 | **1.000** |
| marketplace recall | 0.805 | **0.949** |
| marketplace F1 | 0.871 | **0.974** |
| contradicted findings | 7 | **0** |

Not a like-for-like comparison - the adjudicator changed too - so the number to quote is the
one from a fresh full run of every agent under the current scorer.

The four remaining misses are boundary calls, all in the conservative direction: a stale
copyright line on iiit.ac.in and practo.com where the rest of the site is current, and two
entity-variant sets (zerodha, supreme-industries) that the agent declines to call a conflict.

**29% of the agent's findings land in the adjudicator's abstain zone.** That is not an error
rate - it is the share of findings the mechanical scorer cannot verify either way, and it is
exactly the population to hand-label in `realweb_review_sample_*.csv`.

### Cost paid on the synthetic suite

Removing the structured-data suppression cost one false positive on `site-007`, taking the
synthetic score from 99.3 to **98.6**. It bought eight genuine missing-schema detections on
real government and university sites. The suppression existed to match one gold label and
was benchmark-fitting; the real web is the better authority, so it was removed and the gold
was left untouched.
