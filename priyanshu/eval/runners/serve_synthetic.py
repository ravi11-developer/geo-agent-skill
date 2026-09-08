#!/usr/bin/env python3
"""Serve synthetic test sites on localhost for benchmarking.

Each site-XXX directory is served at http://localhost:PORT/site-XXX/
"""

import http.server
import os
import sys
import threading


SITES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sites", "synthetic")
DEFAULT_PORT = 9500


class SyntheticSiteHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from the synthetic sites directory."""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory or SITES_DIR, **kwargs)

    def log_message(self, format, *args):
        # Suppress noisy logging during benchmark runs
        pass

    def end_headers(self):
        # Add permissive headers for local testing
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html><html><body><h1>Synthetic Server</h1></body></html>")
            return
        super().do_GET()


def start_server(port: int = DEFAULT_PORT, background: bool = False):
    """Start the synthetic site server."""
    sites_dir = os.path.abspath(SITES_DIR)
    if not os.path.isdir(sites_dir):
        print(f"Error: Sites directory not found: {sites_dir}", file=sys.stderr)
        sys.exit(1)

    handler = lambda *args, **kwargs: SyntheticSiteHandler(*args, directory=sites_dir, **kwargs)
    server = http.server.HTTPServer(("127.0.0.1", port), handler)

    # List available sites
    sites = sorted(d for d in os.listdir(sites_dir) if os.path.isdir(os.path.join(sites_dir, d)))
    print(f"Serving {len(sites)} synthetic sites on http://localhost:{port}/")
    for site in sites:
        print(f"  http://localhost:{port}/{site}/")

    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
            server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    start_server(port)
