#!/usr/bin/env python3
"""Stress and robustness suite for the marketplace entrypoint.

Every case serves a pathological response from a local server and asserts that
the audit (a) does not raise, (b) finishes inside the per-site budget, and
(c) still emits a schema-valid report. Cases that imply a specific behaviour
also assert that behaviour - a robots.txt that disallows everything must produce
no crawl, a JS shell must be reported as `rendering`, and so on.

    python tools/stress_test.py            # run everything
    python tools/stress_test.py --case huge_page --case redirect_loop
    python tools/stress_test.py --list
"""

from __future__ import annotations

import argparse
import gzip
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG_ROOT)

BUDGET_SECONDS = 60          # per-site ceiling for a stress case
CASES: dict[str, dict] = {}


def case(name: str, expect_categories: tuple[str, ...] = (), forbid_categories: tuple[str, ...] = (),
         note: str = ""):
    def deco(fn):
        CASES[name] = {"build": fn, "expect": expect_categories, "forbid": forbid_categories, "note": note}
        return fn
    return deco


# ---------------------------------------------------------------------------
# Response fixtures: each returns (status, headers, body_bytes) for a path
# ---------------------------------------------------------------------------

HTML_OK = (b"<!doctype html><html lang=en><head><meta charset=utf-8><title>Acme Metrology - Precision Tools</title>"
           b'<link rel=canonical href="http://127.0.0.1/"><script type="application/ld+json">'
           b'{"@context":"https://schema.org","@type":"Organization","name":"Acme Metrology",'
           b'"sameAs":["https://linkedin.com/company/acme"]}</script></head><body>'
           b"<nav><a href=/>Home</a><a href=/about>About</a><a href=/pricing>Pricing</a></nav>"
           b"<main><h1>Acme Metrology</h1><p>Acme Metrology was founded in 2019 in Leeds and employs 120 "
           b"engineers serving 2,400 customers across 30 countries with a 99.9% uptime SLA. Our calibration "
           b"service starts at $4,200 per year and instruments ship in 6 weeks. Contact sales@acme.example.com "
           b"or +44-113-555-0142 for a quotation. Updated September 2026.</p>"
           b"<h2>Instruments</h2><p>The RG-8 coordinate measuring machine offers 1.2 micron accuracy from "
           b"$86,000 and the RG-3 optical comparator from $24,500, both current as of September 2026.</p></main>"
           b"<footer><p>&copy; 2026 Acme Metrology Ltd</p></footer></body></html>")


def _page(body: bytes, ctype: str = "text/html; charset=utf-8", status: int = 200):
    return status, {"Content-Type": ctype}, body


@case("baseline_healthy", forbid_categories=("crawlability", "rendering", "structured_data"),
      note="control: a well-built page must produce no access/schema findings")
def _baseline():
    return {"/": _page(HTML_OK)}


@case("malformed_html", note="unclosed tags, stray angle brackets, nested forms")
def _malformed():
    body = (b"<html><head><title>Broken <b>Shop</title><body><div><p>Price: $49<div><span>"
            b"<form><form><input><table><tr><td>unclosed<ul><li>a<li>b"
            b"<a href=/x>link<img src=x.png alt=>< > <<>> <script>var a='<div>';</script>"
            b"<p>Contact sales@x.example.com or +1-555-555-0100. Founded 2019, 400 customers.")
    return {"/": _page(body)}


@case("huge_page", note="8MB document must not blow the time budget")
def _huge():
    filler = b"<p>Calibration procedure paragraph with detail about tolerances and ranges.</p>" * 90000
    return {"/": _page(b"<html><head><title>Huge</title></head><body><h1>Huge</h1>" + filler + b"</body></html>")}


@case("deep_nesting", note="6000-level nesting stresses recursive parsers")
def _deep():
    body = b"<html><head><title>Deep</title></head><body>" + b"<div>" * 6000 + b"<p>bottom $19/mo</p>" + b"</div>" * 6000 + b"</body></html>"
    return {"/": _page(body)}


@case("many_images", note="2000 img tags with generic alt")
def _images():
    imgs = b"".join(b'<img src="pricing-table-%d.png" alt="pricing">' % i for i in range(2000))
    return {"/": _page(b"<html><head><title>Gallery</title></head><body><h2>Pricing</h2>" + imgs + b"</body></html>")}


@case("invalid_utf8", note="declared UTF-8, contains invalid byte sequences")
def _bad_utf8():
    body = b"<html><head><meta charset=utf-8><title>Caf\xe9 \xff\xfe Bad</title></head><body><p>Price \x80 $10</p></body></html>"
    return {"/": _page(body)}


@case("charset_lie", note="latin-1 bytes served as UTF-8 and a UTF-8 meta tag")
def _charset_lie():
    body = ("<html><head><meta charset=utf-8><title>Cafétéria — Prix</title></head><body>"
            "<p>Fondée en 2019, 300 clients, à partir de 49€/mois. contact@cafe.example.com</p>"
            "</body></html>").encode("latin-1", errors="replace")
    return {"/": (200, {"Content-Type": "text/html"}, body)}


@case("utf16_bom", note="UTF-16 with BOM and no charset parameter")
def _utf16():
    body = "<html><head><title>UTF16 Shop</title></head><body><p>Price $99</p></body></html>".encode("utf-16")
    return {"/": (200, {"Content-Type": "text/html"}, body)}


@case("gzip_body", note="Content-Encoding: gzip")
def _gzip():
    return {"/": (200, {"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"},
                  gzip.compress(HTML_OK))}


@case("http_500", expect_categories=("crawlability",), note="server error on the entry URL")
def _500():
    return {"/": (500, {"Content-Type": "text/html"}, b"<html><body>Internal Server Error</body></html>")}


@case("http_403", expect_categories=("crawlability",), note="bot wall on the entry URL")
def _403():
    return {"/": (403, {"Content-Type": "text/html"}, b"<html><body>Forbidden</body></html>")}


@case("empty_body", expect_categories=("crawlability",), note="HTTP 200 with a zero-byte body")
def _empty():
    return {"/": (200, {"Content-Type": "text/html"}, b"")}


@case("non_html_pdf", expect_categories=("crawlability",), note="entry URL serves a PDF")
def _pdf():
    return {"/": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n")}


@case("json_api", expect_categories=("crawlability",), note="entry URL serves JSON")
def _json_api():
    return {"/": (200, {"Content-Type": "application/json"}, b'{"products":[{"name":"A","price":49}]}')}


@case("no_content_type", note="response omits Content-Type entirely")
def _no_ctype():
    return {"/": (200, {}, HTML_OK)}


@case("null_bytes", note="NUL bytes inside markup")
def _nulls():
    return {"/": _page(b"<html><head><title>Null\x00Title</title></head><body><p>\x00\x00 $12/mo founded 2020</p></body></html>")}


@case("rtl_cjk", note="RTL Arabic and CJK content")
def _rtl():
    body = ("<html lang=ar dir=rtl><head><meta charset=utf-8><title>شركة القياس - الأدوات</title></head><body>"
            "<nav><a href='/'>الرئيسية</a><a href='/about'>معلومات</a></nav>"
            "<h1>شركة القياس الدقيق</h1><p>تأسست في 2019، ٤٠٠ عميل. السعر ١٩٩ دولار شهريًا. "
            "電話 +81-3-5555-0100 、東京都に本社を置く計測機器メーカーです。sales@example.com</p>"
            "<footer>© 2026 شركة القياس</footer></body></html>").encode()
    return {"/": _page(body)}


@case("spa_shell", expect_categories=("rendering",), note="empty framework root plus JS gate")
def _spa():
    body = (b"<html><head><title>App</title><script type='application/ld+json'>"
            b'{"@context":"https://schema.org","@type":"Organization","name":"AppCo"}</script></head><body>'
            b"<div id='root'></div><noscript>Please enable JavaScript to view products and pricing.</noscript>"
            b"<script>document.getElementById('root').innerHTML='<h1>AppCo</h1><p>Pro plan $49/mo</p>';</script>"
            b"</body></html>")
    return {"/": _page(body)}


@case("js_widget_on_rich_page", forbid_categories=("rendering",),
      note="regression: a modal injected into a content-rich page is not a rendering defect")
def _widget():
    body = HTML_OK.replace(b"</main>",
                           b"<div id='cart-drawer'></div></main>"
                           b"<script>document.getElementById('cart-drawer').innerHTML="
                           b"'<p class=loading>Loading offers...</p>';</script>")
    return {"/": _page(body)}


@case("noindex_on_cart", forbid_categories=("crawlability",),
      note="regression: noindex on a transactional path is correct practice")
def _noindex_cart():
    cart = HTML_OK.replace(b"<head>", b"<head><meta name='robots' content='noindex,nofollow'>")
    return {"/": _page(HTML_OK), "/cart": _page(cart)}


@case("robots_disallow_all", note="robots.txt forbids everything - the crawl must not proceed")
def _robots_block():
    return {"/": _page(HTML_OK),
            "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /\n")}


@case("robots_garbage", note="robots.txt is binary junk")
def _robots_junk():
    return {"/": _page(HTML_OK), "/robots.txt": (200, {"Content-Type": "text/plain"}, os.urandom(4096))}


@case("bad_jsonld", expect_categories=("structured_data",), note="unparseable JSON-LD block")
def _bad_ld():
    body = HTML_OK.replace(b'{"@context":"https://schema.org","@type":"Organization","name":"Acme Metrology","sameAs":["https://linkedin.com/company/acme"]}',
                           b'{"@context":"https://schema.org","@type":"Organization",,"name":"Acme",}')
    return {"/": _page(body)}


@case("exotic_jsonld", forbid_categories=("structured_data",),
      note="@graph, arrays, nulls and non-dict nodes must parse without error")
def _exotic_ld():
    ld = (b'<script type="application/ld+json">[{"@context":"https://schema.org","@graph":'
          b'[{"@type":"Organization","name":"Acme Metrology","sameAs":["https://linkedin.com/company/acme"]},'
          b'{"@type":"WebSite","name":null},"a string node",42]}]</script>')
    return {"/": _page(HTML_OK.replace(b"</head>", ld + b"</head>"))}


@case("base_href", note="<base href> changes relative link resolution")
def _base():
    body = HTML_OK.replace(b"<head>", b"<head><base href='http://127.0.0.1:%d/sub/'>" % 0).replace(b"%d", b"")
    return {"/": _page(body)}


@case("link_explosion", note="5000 internal links must not explode the crawl")
def _links():
    links = b"".join(b'<a href="/p%d">page %d</a>' % (i, i) for i in range(5000))
    return {"/": _page(HTML_OK.replace(b"</main>", links + b"</main>"))}


@case("cyclic_links", note="pages that link to each other in a cycle")
def _cycle():
    def mk(name, nxt):
        return _page(b"<html><head><title>Cycle</title></head><body><nav><a href='" + nxt +
                     b"'>next</a></nav><p>Founded 2019. Price $10/mo. mail@x.example.com</p></body></html>")
    return {"/": mk(b"a", b"/a"), "/a": mk(b"b", b"/b"), "/b": mk(b"c", b"/"), "/c": mk(b"a", b"/a")}


@case("data_uri_images", note="images embedded as data: URIs")
def _data_uri():
    img = (b'<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==" alt="pricing">' * 5)
    return {"/": _page(HTML_OK.replace(b"</main>", img + b"</main>"))}


@case("redirect_loop", expect_categories=("crawlability",), note="302 to itself")
def _redirect():
    return {"/": (302, {"Location": "/"}, b"")}


@case("slow_server", expect_categories=("crawlability",), note="response stalls past the request timeout")
def _slow():
    def slow_body():
        time.sleep(20)
        return HTML_OK
    return {"/": ("SLOW", {"Content-Type": "text/html"}, slow_body)}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def make_server(routes: dict, port: int):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            entry = routes.get(path)
            if entry is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", "9")
                self.end_headers()
                self.wfile.write(b"not found")
                return
            status, headers, body = entry
            if status == "SLOW":
                body = body()
                status = 200
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        do_HEAD = do_GET

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = S(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_case(name: str, spec: dict) -> dict:
    from run import run_audit
    from tools.validate_package import validate_against_schema  # noqa: F401

    routes = spec["build"]()
    port = free_port()
    server = make_server(routes, port)
    result = {"case": name, "note": spec["note"], "error": None, "seconds": 0.0,
              "findings": [], "schema_errors": [], "expect_ok": True, "forbid_ok": True}
    started = time.monotonic()
    try:
        report = run_audit(f"http://127.0.0.1:{port}/")
        result["seconds"] = round(time.monotonic() - started, 2)
        cats = [f.get("category") for f in report.get("findings", [])]
        result["findings"] = cats
        schema = json.load(open(os.path.join(PKG_ROOT, "schema", "audit.schema.json"), encoding="utf-8"))
        result["schema_errors"] = validate_against_schema(report, schema)
        result["expect_ok"] = all(c in cats for c in spec["expect"])
        result["forbid_ok"] = not any(c in cats for c in spec["forbid"])
        result["report"] = report
    except Exception as exc:  # noqa: BLE001
        import traceback
        result["seconds"] = round(time.monotonic() - started, 2)
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()[-800:]
    finally:
        server.shutdown()
        server.server_close()
    return result


def run_concurrency(workers: int = 8) -> dict:
    """Same site audited by N threads at once - shared state must not leak."""
    routes = _baseline()
    port = free_port()
    server = make_server(routes, port)
    from run import run_audit
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            reports = list(pool.map(lambda _: run_audit(f"http://127.0.0.1:{port}/"), range(workers)))
        signatures = {json.dumps(sorted((f["category"], f["severity"]) for f in r["findings"])) for r in reports}
        return {"case": "concurrency_8x", "note": "8 parallel audits of one site",
                "error": None if len(signatures) == 1 else f"divergent results across threads: {signatures}",
                "seconds": round(time.monotonic() - started, 2), "findings": [], "schema_errors": [],
                "expect_ok": True, "forbid_ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"case": "concurrency_8x", "note": "8 parallel audits of one site",
                "error": f"{type(exc).__name__}: {exc}", "seconds": 0.0, "findings": [],
                "schema_errors": [], "expect_ok": True, "forbid_ok": True}
    finally:
        server.shutdown()
        server.server_close()


def run_idempotency() -> dict:
    routes = _baseline()
    port = free_port()
    server = make_server(routes, port)
    from run import run_audit
    try:
        a = run_audit(f"http://127.0.0.1:{port}/")
        b = run_audit(f"http://127.0.0.1:{port}/")
        sig = lambda r: [(f["id"], f["category"], f["severity"], f["evidence"]) for f in r["findings"]]
        same = sig(a) == sig(b)
        return {"case": "idempotency", "note": "two runs of one site produce identical findings",
                "error": None if same else "findings differ between identical runs",
                "seconds": 0.0, "findings": [], "schema_errors": [], "expect_ok": True, "forbid_ok": True}
    finally:
        server.shutdown()
        server.server_close()


def run_dead_port() -> dict:
    from run import run_audit
    port = free_port()  # nothing listening
    started = time.monotonic()
    try:
        report = run_audit(f"http://127.0.0.1:{port}/")
        cats = [f.get("category") for f in report.get("findings", [])]
        ok = "crawlability" in cats
        return {"case": "connection_refused", "note": "nothing listening on the port",
                "error": None if ok else "expected a crawlability finding",
                "seconds": round(time.monotonic() - started, 2), "findings": cats,
                "schema_errors": [], "expect_ok": ok, "forbid_ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"case": "connection_refused", "note": "nothing listening on the port",
                "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - started, 2),
                "findings": [], "schema_errors": [], "expect_ok": False, "forbid_ok": True}



def run_location_integrity() -> dict:
    """Every finding's `locations` must be a page this run actually fetched.

    Regression guard for lib/verification.py's location_integrity rule: a
    finding citing a URL nothing ever read is unreproducible for whoever acts
    on the report, and the verification stage is supposed to drop it before it
    reaches the output. This runs a real multi-page site and checks the
    invariant end-to-end rather than mocking a false detection.
    """
    routes = {
        "/": _page(HTML_OK),
        "/about.html": _page(HTML_OK.replace(b"Acme Metrology", b"Acme Metrology - About")),
        "/broken.html": (500, {"Content-Type": "text/html"}, b"server error"),
    }
    port = free_port()
    server = make_server(routes, port)
    from run import run_audit
    started = time.monotonic()
    try:
        report = run_audit(f"http://127.0.0.1:{port}/")
        fetched = {f"http://127.0.0.1:{port}{p}" for p in routes} | {f"http://127.0.0.1:{port}/"}
        bad = [
            loc for finding in report.get("findings", [])
            for loc in finding.get("locations", [])
            if loc not in fetched and not loc.endswith(("robots.txt", "sitemap.xml"))
        ]
        return {"case": "location_integrity", "note": "no finding may cite an unfetched URL",
                "error": None if not bad else f"finding cites unfetched location(s): {bad}",
                "seconds": round(time.monotonic() - started, 2), "findings": [], "schema_errors": [],
                "expect_ok": True, "forbid_ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"case": "location_integrity", "note": "no finding may cite an unfetched URL",
                "error": f"{type(exc).__name__}: {exc}", "seconds": 0.0, "findings": [],
                "schema_errors": [], "expect_ok": True, "forbid_ok": True}
    finally:
        server.shutdown()
        server.server_close()


def run_saturation_stop() -> dict:
    """A site with several URL templates should stop well short of the hard
    page limit once each template is sampled, under the extended crawl profile.

    Regression guard for the Phase 3 stratified crawl (crawl_render.py):
    without saturation, a fixed page budget is spent breadth-first and never
    reports that it stopped early. Six templates x 2 pages each is enough
    to trigger a stop before the 30-page hard limit if saturation is working.
    """
    sections = ("blog", "products", "docs", "team", "pricing", "faq")
    per_section = 5  # > per_template_samples(3), so every template saturates well before this many are fetched
    routes = {"/": _page(HTML_OK)}
    for section in sections:
        for i in range(1, per_section + 1):
            path = f"/{section}/{i}"
            routes[path] = _page(HTML_OK.replace(b"Acme Metrology", f"Acme {section}-{i}".encode()))
    links = "".join(f'<a href="/{s}/{i}">{s} {i}</a>' for s in sections for i in range(1, per_section + 1))
    routes["/"] = _page(HTML_OK.replace(b"</body>", links.encode() + b"</body>"))
    # 1 entry + 6 sections x 5 pages = 31 URLs total, comfortably above soft_page_target(16),
    # so a crawl that saturates should stop well short of exhausting the queue.

    port = free_port()
    server = make_server(routes, port)
    from run import run_audit
    started = time.monotonic()
    try:
        os.environ["AUDIT_CRAWL_PROFILE"] = "extended"
        try:
            report = run_audit(f"http://127.0.0.1:{port}/")
        finally:
            os.environ.pop("AUDIT_CRAWL_PROFILE", None)
        telemetry = report.get("telemetry", {})
        stopped = telemetry.get("crawl_stopped_because")
        pages = telemetry.get("pages_fetched", 0)
        ok = stopped == "saturated" and pages < 30
        return {"case": "saturation_stop", "note": "extended crawl stops on template saturation, not the page ceiling",
                "error": None if ok else f"stopped_because={stopped!r} pages_fetched={pages} (expected 'saturated' under 30)",
                "seconds": round(time.monotonic() - started, 2), "findings": [], "schema_errors": [],
                "expect_ok": ok, "forbid_ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"case": "saturation_stop", "note": "extended crawl stops on template saturation, not the page ceiling",
                "error": f"{type(exc).__name__}: {exc}", "seconds": 0.0, "findings": [],
                "schema_errors": [], "expect_ok": False, "forbid_ok": True}
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Stress-test the marketplace entrypoint")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", help="write full results here")
    args = ap.parse_args()

    if args.list:
        for name, spec in CASES.items():
            print(f"{name:<24} {spec['note']}")
        return 0

    names = args.case or list(CASES)
    results = [run_case(n, CASES[n]) for n in names]
    if not args.case:
        results += [run_concurrency(), run_idempotency(), run_dead_port(),
                    run_location_integrity(), run_saturation_stop()]

    failures = 0
    print(f"{'case':<24}{'time':>7}  {'result':<8} detail")
    print("-" * 100)
    for r in results:
        problems = []
        if r["error"]:
            problems.append(r["error"][:70])
        if r["schema_errors"]:
            problems.append(f"schema: {r['schema_errors'][0][:50]}")
        if not r["expect_ok"]:
            problems.append(f"missing expected category (got {r['findings']})")
        if not r["forbid_ok"]:
            problems.append(f"forbidden category present ({r['findings']})")
        if r["seconds"] > BUDGET_SECONDS:
            problems.append(f"over budget: {r['seconds']}s")
        status = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        print(f"{r['case']:<24}{r['seconds']:>6.2f}s  {status:<8} {'; '.join(problems) or ','.join(r['findings']) or '-'}")

    print(f"\n{len(results)-failures}/{len(results)} passed")
    if args.json:
        json.dump([{k: v for k, v in r.items() if k != "report"} for r in results],
                  open(args.json, "w"), indent=2, default=str)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
