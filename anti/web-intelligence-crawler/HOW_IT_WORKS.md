# How the SEO Audit Crawler Works

This document explains the internal architecture and data flow of the SEO Audit Crawler, including references to the core files that power the system.

## The Three-Phase Architecture

The crawler operates in a strict, sequential three-phase pipeline: **Crawl**, **Analyze**, and **Report**.

### Phase 1: Crawling (Data Collection)
**File reference:** [`scripts/crawler.py`](./scripts/crawler.py)

Before analyzing anything, the system must collect raw data. The crawler uses a Breadth-First Search (BFS) approach:
1. **Validation & Safety:** Validates the URL to prevent SSRF (Server-Side Request Forgery) by blocking local IPs and internal networks.
2. **Site-wide Discovery:** Fetches and parses `robots.txt` (to know what is blocked) and `sitemap.xml` (to discover all intended pages).
3. **Queue Processing:** Visits allowed URLs one by one. For each page, it collects:
   - Raw HTML (`html_raw`)
   - HTTP Status Code and Response Headers
   - Redirect chains
   - Internal and external links
   - Clean body text (via `trafilatura`)
4. **Output:** Returns a tuple containing a list of `page` dictionaries and a `site_data` dictionary.

### Phase 2: Analysis (The SEO Checks)
**File reference:** [`scripts/analyzers.py`](./scripts/analyzers.py)

Once crawling is complete, the raw data is passed through 50 specialized check functions divided into 10 categories (Technical SEO, On-Page SEO, Structured Data, Content Quality, Performance, Mobile, Security, Accessibility, Link Health, and International SEO).

- **Per-Page Checks (e.g., `check_title_missing`):** Inspect a single page's HTML or headers and return a list of findings for that specific URL.
- **Site-Wide Checks (e.g., `check_orphan_pages`):** Look at the entire crawl dataset simultaneously (e.g., cross-referencing sitemap URLs against found internal links).

Each check returns a standardized `Finding` dictionary if it detects an issue.

### Phase 3: Orchestration & Reporting
**File reference:** [`scripts/seo_audit.py`](./scripts/seo_audit.py)

This is the main entry point that ties everything together.
1. It calls the `crawler.crawl()` function.
2. It passes the resulting data to the checks in `analyzers.py`.
3. **Deduplication:** If multiple pages trigger the exact same issue (e.g., "Missing XML Sitemap" or duplicate titles), it groups them into a single finding and aggregates the `affected_urls`.
4. **Prioritization:** Assigns unique IDs (e.g., `F-001`) and sorts the findings from `critical` down to `low` severity.
5. **Output:** Generates the final, structured JSON report.

---

## Agent Integration (AgentSkills.io Standard)

Because this tool is designed to be used by an AI Agent, it includes metadata and templates to bridge the gap between Python and natural language.

- **The Skill Manifest:** [`SKILL.md`](./SKILL.md)
  Acts as the "prompt instructions" for the AI. It tells the agent when to trigger the tool, how to run the CLI command, and how to interpret the JSON output.
- **The Report Template:** [`assets/report-template.md`](./assets/report-template.md)
  The markdown structure the AI must use when presenting the final findings back to the user, ensuring the output is always consistently formatted.
- **Dependencies:** [`scripts/requirements.txt`](./scripts/requirements.txt)
  Standard Python packages (`requests`, `beautifulsoup4`, `trafilatura`, `lxml`) required to run the scripts.

---

## Data Flow Diagram

```text
[ Start URL ] 
      │
      ▼
( crawler.py ) ───▶ [ list of Page Dicts, Site Data Dict ]
      │
      ▼
( analyzers.py ) ─▶ [ list of Finding Dicts (Raw) ]
      │
      ▼
( seo_audit.py ) ─▶ Deduplicate -> Assign IDs -> Sort -> Count
      │
      ▼
[ final_audit.json ]
```
