# Off-site discoverability — detection rules

Every rule here answers one question: **if a retrieval system went looking for
this brand across the web, what would it have to work with?** None of them can
be settled by reading one origin, which is why all of them produce
recommendations rather than defects.

## 1. External identity anchoring

**Mechanism.** A model reconciling "which Nova Technical is this?" joins records
by shared identifiers. `sameAs` is the only machine-readable way a site can
declare "these external records are me".

| observation | output |
|---|---|
| no `sameAs` **and** no outbound link to a known identity host | recommend publishing verifiable identity signals |
| outbound profile links exist but `sameAs` is absent | recommend mirroring them into the Organization JSON-LD (the cheaper fix) |
| `sameAs` present | no output |

Identity hosts treated as anchors: wikipedia.org, wikidata.org, linkedin.com,
crunchbase.com, github.com, x.com / twitter.com, facebook.com, instagram.com,
youtube.com, bloomberg.com, opencorporates.com.

**Why not a defect.** `site-010` in the gold set publishes valid Organization
JSON-LD with no `sameAs` and is a healthy site. Flagging it would be a false
positive, and precision is the metric this marketplace protects hardest.

## 2. Corroboration surface

**Mechanism.** Appendix D: a claim repeated by independent sources is treated as
more trustworthy than one that lives in a single place. A site cannot manufacture
independent coverage, but it can publish dated, concrete announcements that give
outlets something to cite.

Matched vocabulary: press, newsroom, news, media kit, media, award(s),
recognition, in the news, coverage, featured in, case stud*, testimonial,
customer stor*.

Absent across all crawled pages → recommend adding press releases, awards or news.

## 3. Direct-answer content shape

**Mechanism.** Appendix B: assistants answer questions, and prose already shaped
as *question → short self-contained answer* can be quoted verbatim. Anything
else has to be summarised first, which is where facts get dropped.

No Q&A vocabulary and no `FAQPage` markup → recommend question-shaped content.

## 4. Answer-engine schema coverage

Compared against `FAQPage`, `HowTo`, `QAPage`, `Speakable`, `BreadcrumbList`,
`Article`, `NewsArticle`.

Fires only when **five or more are absent and an Organization block already
exists**. The Organization precondition matters: a site with no structured data
at all is `content-semantics-audit`'s finding, and reporting it here too would
split one root cause across two skills.

## 5. Fact quotability

**Mechanism.** Appendix C: the more explicitly a fact is stated, the more
reliably it is extracted. A page asserting nothing measurable gives a model
nothing to quote, so it is rarely chosen as a source even when it ranks.

A page counts as quotable if its readable text contains a currency figure, a
percentage, a four-digit year, or a counted quantity (users, customers, clients,
employees, countries, years, projects, offices).

Fewer than **half** the crawled pages quotable → recommend concrete facts. The
threshold is a site-level ratio, not a per-page test, so a prose About page does
not trigger it on its own.

## 6. Contact reachability

**Mechanism.** Appendix D, mistaken identity: contact details are a standard
signal for telling same-named organisations apart. Locked in an image or built
by script, they cannot serve that purpose.

Neither an email nor a phone number in readable text → recommend publishing them
as text.

## Ranking

Every recommendation from this skill carries `rank_hint: 1`, marking it as advice
that would apply to many sites. The orchestrator ranks by `(rank_hint, effort)`
and truncates, so general advice can never evict a recommendation grounded in
something measured on *this* site.
