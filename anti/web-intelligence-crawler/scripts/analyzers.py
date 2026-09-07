import json
import re
import typing
from typing import Any, Dict, List
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

Finding = Dict[str, Any]

def make_finding(category: str, title: str, severity: str, affected_urls: List[str], evidence: str, action_summary: str) -> Finding:
    return {
        "category": category,
        "title": title,
        "severity": severity,
        "affected_urls": affected_urls,
        "evidence": evidence,
        "suggested_action": {
            "summary": action_summary,
            "priority": severity,
        }
    }

# Category 1: Technical SEO

def check_http_status(page: dict) -> List[Finding]:
    status = page.get("status_code")
    if status and status >= 400:
        return [make_finding(
            "technical-seo",
            f"HTTP {status} Error",
            "critical",
            [page["url"]],
            f"Page returned status code {status}",
            "Fix the broken link or restore the page."
        )]
    return []

def check_redirect_chains(page: dict) -> List[Finding]:
    chain = page.get("redirect_chain", [])
    if len(chain) > 2:
        return [make_finding(
            "technical-seo",
            "Long Redirect Chain",
            "high",
            [page["url"]],
            f"Chain length: {len(chain)} ({' -> '.join(chain)})",
            "Update links to point directly to the final destination."
        )]
    return []

def check_canonical_tag(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    canonicals = soup.find_all("link", rel="canonical")
    if not canonicals:
        return [make_finding(
            "technical-seo",
            "Missing Canonical Tag",
            "high",
            [page["url"]],
            "No <link rel=\"canonical\"> tag found.",
            "Add a self-referencing canonical tag to prevent duplicate content issues."
        )]
    elif len(canonicals) > 1:
        return [make_finding(
            "technical-seo",
            "Multiple Canonical Tags",
            "high",
            [page["url"]],
            f"Found {len(canonicals)} canonical tags.",
            "Ensure only one canonical tag is present on the page."
        )]
    return []

def check_canonical_target(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        href = canonical.get("href")
        if href != page["url"]:
            return [make_finding(
                "technical-seo",
                "Canonical Points to Different URL",
                "medium",
                [page["url"]],
                f"Canonical href ({href}) differs from page URL ({page['url']}).",
                "Verify that the canonical URL is the intended master version."
            )]
    return []

def check_url_structure(page: dict) -> List[Finding]:
    url = page.get("url", "")
    issues = []
    if len(url) > 115:
        issues.append(f"Length {len(url)} > 115 chars")
    if "_" in urlparse(url).path:
        issues.append("Contains underscores")
    if any(c.isupper() for c in urlparse(url).path):
        issues.append("Contains uppercase letters")
    
    if issues:
        return [make_finding(
            "technical-seo",
            "Suboptimal URL Structure",
            "low",
            [url],
            ", ".join(issues),
            "Use short, lowercase URLs with hyphens instead of underscores."
        )]
    return []

def check_sitemap_missing(pages: List[dict], site_data: dict) -> List[Finding]:
    if not site_data.get("sitemap_urls"):
        return [make_finding(
            "technical-seo",
            "Missing XML Sitemap",
            "high",
            [site_data.get("base_url", "")],
            "No sitemap URLs found in site data.",
            "Provide an XML sitemap and reference it in robots.txt."
        )]
    return []

def check_trailing_slash(page: dict) -> List[Finding]:
    url = page.get("url", "")
    path = urlparse(url).path
    if len(path) > 1 and path.endswith("/"):
        return [make_finding(
            "technical-seo",
            "Trailing Slash on URL",
            "low",
            [url],
            f"URL path ends with a slash: {path}",
            "Ensure consistent trailing slash usage (either enforce or remove)."
        )]
    return []

# Category 2: On-Page SEO

def check_title_missing(page: dict) -> List[Finding]:
    title = page.get("title")
    if not title or not title.strip():
        return [make_finding(
            "on-page-seo",
            "Missing or Empty Title Tag",
            "critical",
            [page["url"]],
            "The <title> tag is missing or empty.",
            "Add a unique, descriptive title tag to the page."
        )]
    return []

def check_title_length(page: dict) -> List[Finding]:
    title = page.get("title")
    if title:
        length = len(title.strip())
        if length < 30 or length > 60:
            return [make_finding(
                "on-page-seo",
                "Suboptimal Title Length",
                "medium",
                [page["url"]],
                f"Title length is {length} characters.",
                "Keep title tags between 30 and 60 characters."
            )]
    return []

def check_meta_description_missing(page: dict) -> List[Finding]:
    desc = page.get("meta_description")
    if not desc or not desc.strip():
        return [make_finding(
            "on-page-seo",
            "Missing Meta Description",
            "high",
            [page["url"]],
            "No meta description found.",
            "Add a unique and compelling meta description."
        )]
    return []

def check_meta_description_length(page: dict) -> List[Finding]:
    desc = page.get("meta_description")
    if desc:
        length = len(desc.strip())
        if length < 70 or length > 155:
            return [make_finding(
                "on-page-seo",
                "Suboptimal Meta Description Length",
                "medium",
                [page["url"]],
                f"Description length is {length} characters.",
                "Keep meta descriptions between 70 and 155 characters."
            )]
    return []

def check_h1_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    if not soup.find("h1"):
        return [make_finding(
            "on-page-seo",
            "Missing H1 Tag",
            "high",
            [page["url"]],
            "No <h1> tag found in the HTML.",
            "Include exactly one <h1> tag summarizing the page topic."
        )]
    return []

def check_h1_multiple(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    h1s = soup.find_all("h1")
    if len(h1s) > 1:
        return [make_finding(
            "on-page-seo",
            "Multiple H1 Tags",
            "low",
            [page["url"]],
            f"Found {len(h1s)} <h1> tags.",
            "Use only one <h1> tag per page for best SEO practices."
        )]
    return []

def check_heading_hierarchy(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    headings = soup.find_all(re.compile('^h[1-6]$'))
    
    last_level = 0
    for h in headings:
        level = int(h.name[1])
        if last_level > 0 and level - last_level > 1:
            return [make_finding(
                "on-page-seo",
                "Skipped Heading Levels",
                "medium",
                [page["url"]],
                f"Heading skipped from H{last_level} to H{level}.",
                "Ensure heading levels are nested properly without skipping levels."
            )]
        last_level = level
    return []

def check_noindex(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    robots_meta = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "robots"})
    if robots_meta and "noindex" in (robots_meta.get("content") or "").lower():
        return [make_finding(
            "on-page-seo",
            "Noindex Tag Present",
            "critical",
            [page["url"]],
            "Found 'noindex' in meta robots tag.",
            "Remove 'noindex' if this page should be indexed by search engines."
        )]
    return []

# Category 3: Structured Data

def check_jsonld_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    scripts = soup.find_all("script", type="application/ld+json")
    if not scripts:
        return [make_finding(
            "structured-data",
            "Missing JSON-LD",
            "medium",
            [page["url"]],
            "No JSON-LD script tags found.",
            "Implement JSON-LD structured data to enhance rich snippets."
        )]
    return []

def check_jsonld_parse_errors(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            content = script.string
            if content:
                json.loads(content)
        except Exception:
            return [make_finding(
                "structured-data",
                "JSON-LD Parse Error",
                "high",
                [page["url"]],
                "JSON-LD content failed to parse as valid JSON.",
                "Fix syntax errors in the JSON-LD structured data."
            )]
    return []

def check_og_tags_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    og_title = soup.find("meta", property="og:title")
    og_image = soup.find("meta", property="og:image")
    missing = []
    if not og_title: missing.append("og:title")
    if not og_image: missing.append("og:image")
    
    if missing:
        return [make_finding(
            "structured-data",
            "Missing Open Graph Tags",
            "medium",
            [page["url"]],
            f"Missing tags: {', '.join(missing)}",
            "Add missing Open Graph meta tags for better social sharing."
        )]
    return []

def check_twitter_card_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    tw_card = soup.find("meta", attrs={"name": "twitter:card"})
    if not tw_card:
        return [make_finding(
            "structured-data",
            "Missing Twitter Card",
            "low",
            [page["url"]],
            "No twitter:card meta tag found.",
            "Add a twitter:card meta tag to optimize sharing on Twitter."
        )]
    return []

# Category 4: Content Quality

def check_thin_content(page: dict) -> List[Finding]:
    text = page.get("text", "")
    if text:
        words = len(text.split())
        if words < 300:
            return [make_finding(
                "content-quality",
                "Thin Content",
                "high",
                [page["url"]],
                f"Word count is {words}.",
                "Expand the page content to provide more value (aim for >300 words)."
            )]
    return []

def check_text_html_ratio(page: dict) -> List[Finding]:
    text = page.get("text", "")
    html = page.get("html_raw", "")
    if text and html:
        ratio = len(text) / len(html)
        if ratio < 0.1:
            return [make_finding(
                "content-quality",
                "Low Text-to-HTML Ratio",
                "low",
                [page["url"]],
                f"Ratio is {ratio:.2%} (<10%).",
                "Increase readable text or reduce HTML bloat."
            )]
    return []

def check_duplicate_titles(pages: List[dict], site_data: dict) -> List[Finding]:
    title_map = {}
    for p in pages:
        t = p.get("title", "").strip()
        if t:
            title_map.setdefault(t, []).append(p["url"])
    
    findings = []
    for t, urls in title_map.items():
        if len(urls) > 1:
            findings.append(make_finding(
                "content-quality",
                "Duplicate Title Tags",
                "high",
                urls,
                f"Title '{t}' is shared across {len(urls)} pages.",
                "Ensure every page has a unique title tag."
            ))
    return findings

def check_placeholder_text(page: dict) -> List[Finding]:
    text = page.get("text", "").lower()
    if "lorem ipsum" in text:
        return [make_finding(
            "content-quality",
            "Placeholder Text Found",
            "medium",
            [page["url"]],
            "Found 'lorem ipsum' in page text.",
            "Remove placeholder text and replace it with real content."
        )]
    return []

# Category 5: Performance

def check_page_size(page: dict) -> List[Finding]:
    size = page.get("content_length", 0)
    if size > 2_000_000:
        return [make_finding(
            "performance",
            "Large Page Size",
            "medium",
            [page["url"]],
            f"Page size is {size} bytes (> 2MB).",
            "Optimize page assets and reduce HTML size to improve load times."
        )]
    return []

def check_render_blocking_css(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    head = soup.find("head")
    if not head: return []
    
    blocking_css = 0
    for link in head.find_all("link", rel="stylesheet"):
        media = link.get("media", "").lower()
        if media not in ["print", "not all"]:
            blocking_css += 1
            
    if blocking_css > 3:
        return [make_finding(
            "performance",
            "Render-Blocking CSS",
            "high",
            [page["url"]],
            f"Found {blocking_css} blocking stylesheets in <head>.",
            "Inline critical CSS and defer non-critical stylesheets."
        )]
    return []

def check_render_blocking_scripts(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    head = soup.find("head")
    if not head: return []
    
    blocking_scripts = 0
    for script in head.find_all("script", src=True):
        if not script.has_attr("async") and not script.has_attr("defer"):
            blocking_scripts += 1
            
    if blocking_scripts > 2:
        return [make_finding(
            "performance",
            "Render-Blocking Scripts",
            "high",
            [page["url"]],
            f"Found {blocking_scripts} blocking scripts in <head>.",
            "Use async or defer attributes on script tags."
        )]
    return []

def check_dom_size(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    elements = len(soup.find_all())
    if elements > 1500:
        return [make_finding(
            "performance",
            "Excessive DOM Size",
            "medium",
            [page["url"]],
            f"Found {elements} HTML elements.",
            "Reduce DOM complexity to improve rendering performance."
        )]
    return []

def check_image_lazy_loading(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    images = soup.find_all("img")
    missing_lazy = 0
    for img in images:
        if img.get("loading") != "lazy":
            missing_lazy += 1
            
    if missing_lazy > 5:
        return [make_finding(
            "performance",
            "Images Missing Lazy Loading",
            "medium",
            [page["url"]],
            f"{missing_lazy} images lack loading=\"lazy\".",
            "Add loading=\"lazy\" to images below the fold."
        )]
    return []

# Category 6: Mobile

def check_viewport_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        return [make_finding(
            "mobile",
            "Missing Viewport Meta Tag",
            "critical",
            [page["url"]],
            "No <meta name=\"viewport\"> found.",
            "Add a viewport meta tag for mobile responsiveness."
        )]
    return []

# Category 7: Security

def check_https(page: dict) -> List[Finding]:
    if not page.get("is_https", False):
        return [make_finding(
            "security",
            "Page Not Using HTTPS",
            "critical",
            [page["url"]],
            "URL uses http:// instead of https://.",
            "Migrate the page to HTTPS."
        )]
    return []

def check_mixed_content(page: dict) -> List[Finding]:
    if not page.get("is_https", False):
        return []
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    
    mixed_count = 0
    for tag, attr in [("img", "src"), ("script", "src"), ("link", "href"), ("iframe", "src")]:
        for el in soup.find_all(tag):
            val = el.get(attr, "")
            if val.startswith("http://"):
                mixed_count += 1
                
    if mixed_count > 0:
        return [make_finding(
            "security",
            "Mixed Content",
            "high",
            [page["url"]],
            f"Found {mixed_count} HTTP resources on an HTTPS page.",
            "Update resource links to use HTTPS."
        )]
    return []

def check_security_headers(page: dict) -> List[Finding]:
    headers = {k.lower(): v for k, v in page.get("response_headers", {}).items()}
    expected = ["strict-transport-security", "content-security-policy", "x-frame-options", "x-content-type-options"]
    missing = [h for h in expected if h not in headers]
    
    if missing:
        return [make_finding(
            "security",
            "Missing Security Headers",
            "medium",
            [page["url"]],
            f"Missing headers: {', '.join(missing)}",
            "Implement standard security headers to protect users."
        )]
    return []

def check_unsafe_target_blank(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    
    unsafe = 0
    for a in soup.find_all("a", target="_blank"):
        rel = a.get("rel", [])
        if isinstance(rel, str):
            rel = [rel]
        if "noopener" not in rel and "noreferrer" not in rel:
            unsafe += 1
            
    if unsafe > 0:
        return [make_finding(
            "security",
            "Unsafe target=\"_blank\" Links",
            "low",
            [page["url"]],
            f"Found {unsafe} links missing rel=\"noopener\" or rel=\"noreferrer\".",
            "Add rel=\"noopener\" to external links that open in a new tab."
        )]
    return []

# Category 8: Accessibility

def check_img_alt_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    images = soup.find_all("img")
    missing_alt = 0
    for img in images:
        if not img.has_attr("alt"):
            missing_alt += 1
            
    if missing_alt > 0:
        return [make_finding(
            "accessibility",
            "Missing Image Alt Text",
            "high",
            [page["url"]],
            f"{missing_alt} out of {len(images)} images lack an alt attribute.",
            "Add descriptive alt text to all images for screen readers."
        )]
    return []

def check_html_lang_missing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    html_tag = soup.find("html")
    if html_tag and not html_tag.has_attr("lang"):
        return [make_finding(
            "accessibility",
            "Missing HTML Lang Attribute",
            "medium",
            [page["url"]],
            "The <html> tag has no 'lang' attribute.",
            "Specify the primary language of the page in the <html> tag."
        )]
    return []

def check_form_labels(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    
    unlabeled = 0
    inputs = soup.find_all(["input", "textarea", "select"])
    for inp in inputs:
        typ = inp.get("type", "").lower()
        if typ in ["hidden", "submit", "button", "image", "reset"]:
            continue
            
        inp_id = inp.get("id")
        has_label = False
        
        if inp_id:
            label = soup.find("label", attrs={"for": inp_id})
            if label:
                has_label = True
        
        if not has_label:
            parent_label = inp.find_parent("label")
            if parent_label:
                has_label = True
                
        if not has_label:
            unlabeled += 1
            
    if unlabeled > 0:
        return [make_finding(
            "accessibility",
            "Missing Form Labels",
            "low",
            [page["url"]],
            f"Found {unlabeled} form inputs without associated labels.",
            "Ensure all form controls have an associated <label>."
        )]
    return []

def check_aria_landmarks(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    
    landmarks = ["main", "nav", "header", "footer", "aside"]
    has_semantic = any(soup.find(t) for t in landmarks)
    has_role = soup.find(attrs={"role": lambda x: x in ["banner", "navigation", "main", "complementary", "contentinfo", "search", "form"]})
    
    if not has_semantic and not has_role:
        return [make_finding(
            "accessibility",
            "Missing ARIA Landmarks",
            "low",
            [page["url"]],
            "No semantic HTML5 landmarks or ARIA landmark roles found.",
            "Use semantic HTML5 elements or ARIA roles to define page regions."
        )]
    return []

# Category 9: Link Health

def check_broken_internal_links(pages: List[dict], site_data: dict) -> List[Finding]:
    url_to_status = {p["url"]: p.get("status_code", 0) for p in pages}
    findings = []
    
    for p in pages:
        broken = []
        for link in p.get("links_internal", []):
            status = url_to_status.get(link)
            if status and status >= 400:
                broken.append(f"{link} ({status})")
                
        if broken:
            findings.append(make_finding(
                "link-health",
                "Broken Internal Links",
                "critical",
                [p["url"]],
                f"Links to broken pages: {', '.join(broken)}",
                "Fix or remove broken internal links."
            ))
    return findings

def check_orphan_pages(pages: List[dict], site_data: dict) -> List[Finding]:
    sitemap_urls = set(site_data.get("sitemap_urls", []))
    if not sitemap_urls:
        return []
        
    linked_urls = set()
    for p in pages:
        linked_urls.update(p.get("links_internal", []))
        
    crawled_urls = {p["url"] for p in pages}
    
    orphans = []
    for url in sitemap_urls:
        if url in crawled_urls and url not in linked_urls:
            orphans.append(url)
            
    if orphans:
        return [make_finding(
            "link-health",
            "Orphan Pages",
            "high",
            orphans,
            f"Found {len(orphans)} sitemap pages with no internal links pointing to them.",
            "Ensure important pages are linked from elsewhere on the site."
        )]
    return []

def check_excessive_links(page: dict) -> List[Finding]:
    internal = len(page.get("links_internal", []))
    external = len(page.get("links_external", []))
    total = internal + external
    if total > 3000:
        return [make_finding(
            "link-health",
            "Excessive Links on Page",
            "low",
            [page["url"]],
            f"Found {total} total links (internal + external).",
            "Reduce the number of links to keep the page focused."
        )]
    return []

def check_internal_nofollow(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    base_domain = urlparse(page["url"]).netloc
    
    nofollow_internal = 0
    for a in soup.find_all("a", rel="nofollow"):
        href = a.get("href")
        if href:
            target_domain = urlparse(urljoin(page["url"], href)).netloc
            if target_domain == base_domain:
                nofollow_internal += 1
                
    if nofollow_internal > 0:
        return [make_finding(
            "link-health",
            "Internal Nofollow Links",
            "medium",
            [page["url"]],
            f"Found {nofollow_internal} internal links with rel=\"nofollow\".",
            "Remove nofollow from internal links to allow PageRank flow."
        )]
    return []

def check_nondescriptive_anchors(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    generic_words = {"click here", "read more", "learn more", "here", "more", "link"}
    
    generic_count = 0
    for a in soup.find_all("a"):
        text = a.get_text(strip=True).lower()
        if text in generic_words:
            generic_count += 1
            
    if generic_count > 0:
        return [make_finding(
            "link-health",
            "Non-Descriptive Anchor Text",
            "low",
            [page["url"]],
            f"Found {generic_count} links with generic anchor text.",
            "Use descriptive keywords in anchor text."
        )]
    return []

# Category 10: International SEO

def check_hreflang_missing(pages: List[dict], site_data: dict) -> List[Finding]:
    has_hreflang_anywhere = False
    pages_with_hreflang = set()
    
    for p in pages:
        html = p.get("html_raw")
        if html:
            soup = BeautifulSoup(html, 'lxml')
            if soup.find("link", rel="alternate", hreflang=True):
                has_hreflang_anywhere = True
                pages_with_hreflang.add(p["url"])
                
    if not has_hreflang_anywhere:
        return []
        
    missing_urls = [p["url"] for p in pages if p["url"] not in pages_with_hreflang]
    if missing_urls:
        return [make_finding(
            "international-seo",
            "Missing Hreflang Tags",
            "high",
            missing_urls,
            f"{len(missing_urls)} pages lack hreflang tags on a multi-language site.",
            "Ensure hreflang tags are implemented consistently across all localized pages."
        )]
    return []

def check_hreflang_invalid_codes(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    invalid = []
    
    pattern = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
    for link in soup.find_all("link", rel="alternate", hreflang=True):
        lang = link.get("hreflang", "")
        if lang != "x-default" and not pattern.match(lang):
            invalid.append(lang)
            
    if invalid:
        return [make_finding(
            "international-seo",
            "Invalid Hreflang Codes",
            "high",
            [page["url"]],
            f"Invalid codes found: {', '.join(invalid)}",
            "Use valid ISO 639-1 language and ISO 3166-1 alpha-2 region codes."
        )]
    return []

def check_hreflang_missing_xdefault(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    links = soup.find_all("link", rel="alternate", hreflang=True)
    if not links: return []
    
    has_xdefault = any(l.get("hreflang") == "x-default" for l in links)
    if not has_xdefault:
        return [make_finding(
            "international-seo",
            "Missing x-default Hreflang",
            "medium",
            [page["url"]],
            "Hreflang tags exist but none is 'x-default'.",
            "Add an x-default hreflang tag for unmatched languages/regions."
        )]
    return []

def check_hreflang_self_referencing(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    links = soup.find_all("link", rel="alternate", hreflang=True)
    if not links: return []
    
    hrefs = [l.get("href") for l in links if l.get("href")]
    if page["url"] not in hrefs:
        return [make_finding(
            "international-seo",
            "Missing Self-Referencing Hreflang",
            "high",
            [page["url"]],
            "The page's own URL is not included in its hreflang set.",
            "Ensure every page includes a self-referencing hreflang tag."
        )]
    return []

def check_lang_hreflang_mismatch(page: dict) -> List[Finding]:
    html = page.get("html_raw")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    html_tag = soup.find("html")
    if not html_tag or not html_tag.has_attr("lang"): return []
    html_lang = html_tag.get("lang", "").lower()
    
    links = soup.find_all("link", rel="alternate", hreflang=True)
    for link in links:
        if link.get("href") == page["url"]:
            hreflang = link.get("hreflang", "").lower()
            hreflang_lang = hreflang.split('-')[0]
            if html_lang.split('-')[0] != hreflang_lang and hreflang != "x-default":
                return [make_finding(
                    "international-seo",
                    "HTML Lang / Hreflang Mismatch",
                    "medium",
                    [page["url"]],
                    f"HTML lang '{html_lang}' mismatches self-referencing hreflang '{hreflang}'.",
                    "Ensure the HTML lang attribute matches the language in the hreflang tag."
                )]
    return []

def check_duplicate_meta_descriptions(pages: List[dict], site_data: dict) -> List[Finding]:
    desc_map = {}
    for p in pages:
        d = p.get("meta_description")
        if d and d.strip():
            desc_map.setdefault(d.strip(), []).append(p["url"])
            
    findings = []
    for d, urls in desc_map.items():
        if len(urls) > 1:
            findings.append(make_finding(
                "content-quality",
                "Duplicate Meta Descriptions",
                "medium",
                urls,
                f"Meta description is shared across {len(urls)} pages.",
                "Ensure every page has a unique meta description."
            ))
    return findings

PER_PAGE_CHECKS = [
    check_http_status,
    check_redirect_chains,
    check_canonical_tag,
    check_canonical_target,
    check_url_structure,
    check_trailing_slash,
    check_title_missing,
    check_title_length,
    check_meta_description_missing,
    check_meta_description_length,
    check_h1_missing,
    check_h1_multiple,
    check_heading_hierarchy,
    check_noindex,
    check_jsonld_missing,
    check_jsonld_parse_errors,
    check_og_tags_missing,
    check_twitter_card_missing,
    check_thin_content,
    check_text_html_ratio,
    check_placeholder_text,
    check_page_size,
    check_render_blocking_css,
    check_render_blocking_scripts,
    check_dom_size,
    check_image_lazy_loading,
    check_viewport_missing,
    check_https,
    check_mixed_content,
    check_security_headers,
    check_unsafe_target_blank,
    check_img_alt_missing,
    check_html_lang_missing,
    check_form_labels,
    check_aria_landmarks,
    check_excessive_links,
    check_internal_nofollow,
    check_nondescriptive_anchors,
    check_hreflang_invalid_codes,
    check_hreflang_missing_xdefault,
    check_hreflang_self_referencing,
    check_lang_hreflang_mismatch,
]

SITE_WIDE_CHECKS = [
    check_sitemap_missing,
    check_duplicate_titles,
    check_broken_internal_links,
    check_orphan_pages,
    check_hreflang_missing,
    check_duplicate_meta_descriptions,
]
