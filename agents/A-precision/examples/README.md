# Example audit reports

Four `audit.json` outputs across deliberately different stacks, produced by the
entrypoint exactly as shipped. Each is schema-valid against `../schema/audit.schema.json`.

| file | stack | what the audit found |
|---|---|---|
| `audit-react-spa-tldraw.json` | React SPA (tldraw.com) | `rendering` + `content_extraction`: 13KB shell, all content injected client-side |
| `audit-wordpress-smile-foundation.json` | WordPress (smilefoundationindia.org) | `non_text_facts`: campaign statistics published only as images |
| `audit-minimal-html-cmi.json` | hand-written HTML (cmi.ac.in) | `structured_data` + `corroboration`: no schema, no verifiable identity signals |
| `audit-ecommerce-mokobara.json` | Shopify storefront (mokobara.com) | see file - product facts and schema coverage |

## Why the `site` field says 127.0.0.1

These were produced by replaying byte-exact captures of those sites from a local
server, so the run is reproducible and does not re-crawl anyone. The audit records the
URL it actually fetched, which is the replay URL; the origin of each capture is named
in the table above. Point `run.py` at a live URL to produce a report whose `site` is
that URL.
