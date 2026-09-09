#!/usr/bin/env python3
"""
Shared utilities for Brand AI-Readiness Audit skills.

Provides: HTTP fetcher with retry, comprehensive HTML parser,
finding builder, and common regex patterns.

All skills import from here to avoid code duplication.
"""
import json, sys, gzip, re, time, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser


# ===========================================================================
# HTTP fetch with retry
# ===========================================================================

class _RedirectTracker(urllib.request.HTTPRedirectHandler):
    """Records every hop in a redirect chain."""
    def __init__(self):
        super().__init__()
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"url": req.full_url, "status": code})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=12, retries=1):
    """Fetch *url* with retry and return a dict with body bytes and metadata."""
    last_err = None
    for attempt in range(retries + 1):
        tracker = _RedirectTracker()
        opener = urllib.request.build_opener(tracker)
        req = urllib.request.Request(
            url, headers={"User-Agent": "BrandAIReadinessAudit/1.0"}
        )
        try:
            with opener.open(req, timeout=timeout) as r:
                body = r.read(2_000_000)
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                if hdrs.get("content-encoding") == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except Exception:
                        pass
                return {
                    "url": r.geturl(),
                    "status": r.status,
                    "content_type": hdrs.get("content-type", ""),
                    "body": body,
                    "redirect_chain": tracker.chain,
                    "last_modified": hdrs.get("last-modified"),
                    "headers": hdrs,
                }
        except urllib.error.HTTPError as e:
            return {
                "url": url,
                "status": e.code,
                "content_type": "",
                "body": b"",
                "redirect_chain": tracker.chain,
                "last_modified": None,
                "headers": {},
                "error": str(e),
            }
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5)
                continue
    return {
        "url": url,
        "status": 0,
        "content_type": "",
        "body": b"",
        "redirect_chain": [],
        "last_modified": None,
        "headers": {},
        "error": str(last_err),
    }


# ===========================================================================
# Comprehensive HTML parser — all fields needed by every skill
# ===========================================================================

class FullParser(HTMLParser):
    """Single-pass HTML parser that extracts every signal needed by all audit skills."""

    def __init__(self, base_url=""):
        super().__init__()
        self.base_url = base_url
        self.base_domain = urllib.parse.urlsplit(base_url).netloc if base_url else ""

        # --- Core meta ---
        self._in_title = False
        self.title = ""
        self.meta_description = ""
        self.meta_dates = []
        self.viewport_meta = False
        self.og_tags = {}
        self.canonical = ""

        # --- Robots meta ---
        self.robots_meta = ""
        self.googlebot_meta = ""

        # --- Headings ---
        self.headings = []
        self._in_heading = False
        self._heading_tag = ""
        self._heading_buf = ""

        # --- Links ---
        self.links = []
        self._in_a = False
        self._a_href = ""
        self._a_text = ""

        # --- Navigation ---
        self.has_nav = False
        self.has_breadcrumb = False
        self._in_nav = False

        # --- CTAs ---
        self.buttons = 0
        self._in_button = False
        self._button_text = ""

        # --- Images ---
        self.images = []

        # --- JSON-LD ---
        self._in_jsonld = False
        self._jsonld_buf = ""
        self.jsonld_blocks = []

        # --- Visible text ---
        self._text_parts = []
        self._skip_tags = {"script", "style", "noscript"}
        self._skip_depth = 0
        self.paragraph_count = 0
        
        # --- Microdata ---
        self.has_microdata = False

        # --- Hidden content ---
        self.hidden_elements = 0
        self.display_none_elements = 0
        self.aria_hidden_elements = 0
        self.hidden_text = ""
        self._element_stack = []

        # --- Iframes / lazy loading ---
        self.iframes = 0
        self.lazy_images = 0
        self.has_noscript = False

        # --- Authorship ---
        self.meta_author = ""
        self.og_author = ""

        # --- Hreflang ---
        self.hreflang_links = []

        # --- Twitter Cards ---
        self.twitter_cards = {}

        # --- Language ---
        self.lang_attr = ""

        # --- Forms / Accessibility ---
        self.forms_count = 0
        self.input_count = 0
        self.label_count = 0
        self.has_email_input = False

        # --- Resources ---
        self.stylesheet_count = 0
        self.external_script_count = 0

    def _abs(self, href):
        return urllib.parse.urljoin(self.base_url, href) if href else ""

    def _is_internal(self, abs_url):
        return urllib.parse.urlsplit(abs_url).netloc == self.base_domain

    def _in_hidden_context(self):
        return any(item["hidden"] for item in self._element_stack)

    # noinspection PyMethodOverriding
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)

        # Skip depth
        if tag in self._skip_tags:
            self._skip_depth += 1
            if tag == "noscript":
                self.has_noscript = True

        # Hidden content signals
        is_hidden = False
        if tag != "input" and "hidden" in d:
            self.hidden_elements += 1
            is_hidden = True
        style = (d.get("style") or "").lower().replace(" ", "")
        if "display:none" in style:
            self.display_none_elements += 1
            is_hidden = True
        if d.get("aria-hidden", "").lower() == "true":
            self.aria_hidden_elements += 1
            is_hidden = True
        self._element_stack.append({"tag": tag, "hidden": is_hidden})
        
        if "itemtype" in d or "typeof" in d:
            self.has_microdata = True

        if tag == "html":
            self.lang_attr = d.get("lang", "")

        elif tag == "title":
            self._in_title = True

        elif tag == "meta":
            name = d.get("name", "").lower()
            prop = d.get("property", "").lower()
            content = d.get("content", "")

            if name == "description":
                self.meta_description = content
            if name == "viewport":
                self.viewport_meta = True
            if name == "author" and content:
                self.meta_author = content
            if name == "robots":
                self.robots_meta = content.lower()
            if name == "googlebot":
                self.googlebot_meta = content.lower()
            if prop.startswith("og:"):
                self.og_tags[prop] = content
            if prop in ("article:author", "og:article:author") and content:
                self.og_author = content
            if name.startswith("twitter:"):
                self.twitter_cards[name] = content
            if any(k in prop or k in name for k in ("date", "time", "modified", "published")):
                if content:
                    self.meta_dates.append(content)

        elif tag == "link":
            rel = d.get("rel", "").lower()
            if rel == "canonical":
                self.canonical = d.get("href", "")
            elif rel == "alternate" and "hreflang" in d:
                self.hreflang_links.append({
                    "lang": d.get("hreflang", ""),
                    "href": d.get("href", ""),
                })
            elif rel == "stylesheet":
                self.stylesheet_count += 1

        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_tag = tag
            self._heading_buf = ""

        elif tag == "a" and "href" in d:
            self._in_a = True
            self._a_href = d["href"]
            self._a_text = ""
            cls = d.get("class", "").lower()
            if any(k in cls for k in ("btn", "button", "cta")):
                self.buttons += 1

        elif tag == "nav":
            self.has_nav = True
            self._in_nav = True
            cls = d.get("class", "").lower()
            aria = d.get("aria-label", "").lower()
            if "breadcrumb" in cls or "breadcrumb" in aria:
                self.has_breadcrumb = True

        elif tag in ("ol", "ul"):
            cls = d.get("class", "").lower()
            if "breadcrumb" in cls:
                self.has_breadcrumb = True

        elif tag == "button":
            self.buttons += 1
            self._in_button = True
            self._button_text = ""

        elif tag == "img":
            self.images.append({
                "src": self._abs(d.get("src", "")),
                "alt": d.get("alt", ""),
            })
            if d.get("loading", "").lower() == "lazy":
                self.lazy_images += 1

        elif tag == "iframe":
            self.iframes += 1

        elif tag == "script":
            if d.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = ""
            if "src" in d:
                self.external_script_count += 1

        elif tag == "p":
            self.paragraph_count += 1

        elif tag == "form":
            self.forms_count += 1

        elif tag == "input":
            input_type = d.get("type", "text").lower()
            input_name = d.get("name", "").lower()
            if input_type != "hidden":
                self.input_count += 1
            if input_type == "email" or "email" in input_name or "newsletter" in input_name:
                self.has_email_input = True

        elif tag == "label":
            self.label_count += 1

    def handle_data(self, data):
        if self._in_hidden_context():
            self.hidden_text += data
        if self._in_title:
            self.title += data
        if self._in_heading:
            self._heading_buf += data
        if self._in_a:
            self._a_text += data
        if self._in_button:
            self._button_text += data
        if self._in_jsonld:
            self._jsonld_buf += data
        if self._skip_depth == 0:
            self._text_parts.append(data)

    def handle_endtag(self, tag):
        for i in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[i]["tag"] == tag:
                self._element_stack = self._element_stack[:i]
                break

        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._in_heading:
            self.headings.append((self._heading_tag, self._heading_buf.strip()))
            self._in_heading = False
        elif tag == "a" and self._in_a:
            abs_url = self._abs(self._a_href)
            self.links.append({
                "href": abs_url,
                "text": self._a_text.strip(),
                "is_internal": self._is_internal(abs_url),
            })
            self._in_a = False
        elif tag == "nav":
            self._in_nav = False
        elif tag == "button" and self._in_button:
            self._in_button = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                self.jsonld_blocks.append(json.loads(self._jsonld_buf))
            except json.JSONDecodeError:
                self.jsonld_blocks.append({"_parse_error": True, "_raw": self._jsonld_buf[:500]})

    @property
    def visible_text(self):
        return " ".join(self._text_parts)

    @property
    def word_count(self):
        return len(self.visible_text.split())

    @property
    def internal_links(self):
        return [l for l in self.links if l["is_internal"]]

    @property
    def external_links(self):
        return [l for l in self.links if not l["is_internal"]]


def parse_page(html, base_url=""):
    """Parse *html* and return a populated FullParser instance."""
    p = FullParser(base_url)
    p.feed(html)
    return p


# ===========================================================================
# Finding builder
# ===========================================================================

def make_finding(fid, title, severity, evidence, action_summary,
                 priority=None, affected_urls=None, confidence="high",
                 finding_type="defect"):
    """Return a standards-compliant finding dict."""
    return {
        "id": fid,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "type": finding_type,
        "evidence": evidence,
        "affected_urls": affected_urls or [],
        "suggested_action": {
            "summary": action_summary,
            "priority": priority or severity,
        },
    }


# ===========================================================================
# Common regex patterns
# ===========================================================================

PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b')
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
COPYRIGHT_RE = re.compile(r'(?:copyright|©)\s*(?:(\d{4})\s*[-–]\s*)?(\d{4})', re.I)
ADDRESS_RE = re.compile(
    r'\d{1,5}\s+[\w\s]{2,30}'
    r'(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way|place|pl)\b',
    re.I,
)
BRAND_TOKEN_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
QUESTION_RE = re.compile(r'[A-Z][^.?!]*\?')
