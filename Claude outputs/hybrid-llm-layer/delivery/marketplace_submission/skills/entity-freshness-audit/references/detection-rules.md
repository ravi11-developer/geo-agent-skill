# Detection rules: entity, freshness, corroboration

## entity_identity (high)

1. Collect candidates from the identity slots (see SKILL.md).
2. Normalise: lowercase, punctuation stripped, trailing legal suffixes removed on up to
   three joined tokens - `B.V.` becomes `b v` after punctuation stripping and must still
   match `bv`; likewise `L.L.C.` -> `llc`, `Pty Ltd` -> `ptyltd`.
3. Pick the **canonical**: the medoid (the spelling most similar to all the others)
   among names that behave like site identity - present in a strong slot (schema name,
   `og:site_name`, footer copyright) or recurring across slots or pages.
4. Count as a **variant** any other name that is at most 2.2x the canonical's length and
   either >= 0.50 character-similar to it or shares its first four squashed characters.
5. Fire when the family (canonical + variants) reaches **3**.

Two spellings are a recommendation, not a defect.

## Title parsing

Titles split on `:: — – | - · • › »`. When the leading segment is a generic page word
(about, products, pricing, contact, news, blog, docs, home, services, support, careers,
faq, team, press, resources, solutions, company, overview) the **trailing** segment is
the brand instead - sub-pages are titled "Page - Brand" while home pages are titled
"Brand - tagline". Without this rule every sub-page contributes a phantom entity.

## freshness

- Years are read from visible text including header and footer (copyright lines live
  there), never from inline scripts - version numbers and timestamps in JS made
  text-light sites look years stale.
- Years in founding contexts (`founded|since|established|est.|inception|incorporated`)
  are excluded.
- Copyright ranges take the **latest** year: `© 2020-26` is 2026, not 2020.
- A page is stale when it carries **no current-year date** and either its copyright is
  <= current_year - 2, or it holds >= 3 stale dated claims.
- The site fires if any page is stale. If the site publishes current-year content
  anywhere, an old copyright line is demoted to a recommendation.
- Severity: >= 8 stale claims across the site is `high`, otherwise `medium`.

Claims are classified for the evidence string as pricing, award, statistic, update
stamp, press or team, so the report says *what* is stale.

## corroboration (low)

Signals counted across all crawled pages:

- schema keys: `sameAs`, `citation`, `review`, `aggregateRating`, `award`, `founder`,
  `parentOrganization`, `memberOf`, `publisher`;
- outbound links to identity platforms (LinkedIn, Crunchbase, Wikipedia, Wikidata,
  GitHub, X, Meta properties, YouTube, G2, Capterra, Trustpilot, Glassdoor, Bloomberg,
  SEC, Companies House, MCA/Zauba, DOI, ORCID, Scholar, ResearchGate, Yelp,
  TripAdvisor, app stores, Product Hunt, Medium) - these are *platforms*, so the check
  stays domain-agnostic;
- citation markup: `<cite>`, `<blockquote>`, `rel=author|publisher|external|cite`.

Fires only when the total is **zero**, the site has a resolvable canonical name, and the
entry page carries >= 120 words - i.e. a site that makes claims and offers no way to
check any of them. A site with signals but no `sameAs` array gets a recommendation.
