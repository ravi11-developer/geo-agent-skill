#!/usr/bin/env python3
"""Finding verification: an adversarial second pass over candidate findings.

The detectors are optimised to *notice* things. This stage is optimised to
disbelieve them, and it runs after every skill has reported, with the audit's
remaining time budget to spend. It exists because the two cheapest ways to lose
precision are not detector bugs at all:

* a finding raised from a transport blip that would not reproduce, and
* a site-wide claim ("nothing here has X") made from a crawl that only ever saw
  a small and non-random slice of the site.

Nothing here invents findings or edits their meaning. A rule may drop a finding
whose own evidence does not survive re-checking, or lower its ``confidence`` and
say why. Every decision is recorded in the returned log so the report can show
its working rather than quietly discarding a detection.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

# Claims of the form "this is absent across the site" - as opposed to "this
# specific page is broken". Only these depend on how much of the site was seen.
SITE_WIDE_ABSENCE_RE = re.compile(
    r"\b(anywhere|any page|any of the|no [a-z-]+ (?:at all|on any)|none of the|"
    r"across (?:all|the site)|site-wide|sitewide|every page)\b", re.I)

# Below this share of the discoverable URL set, a site-wide absence claim is
# an extrapolation rather than a measurement.
THIN_COVERAGE_RATIO = 0.25
# ...unless the site is genuinely small, where a handful of pages *is* the site.
SMALL_SITE_URLS = 8


def _is_site_wide_absence(finding: dict[str, Any]) -> bool:
    if finding.get("proof", {}).get("site_wide") is True:
        return True
    text = f"{finding.get('title', '')} {finding.get('evidence', '')}"
    return bool(SITE_WIDE_ABSENCE_RE.search(text))


def _coverage(snapshot: Any) -> tuple[int, int, float]:
    """(pages read, URLs discovered, share of the discoverable set we read)."""
    notes = getattr(snapshot, "notes", {}) or {}
    read = len(getattr(snapshot, "ok_pages", []) or [])
    discovered = max(int(notes.get("urls_discovered", 0) or 0), read)
    sitemap_urls = len(getattr(snapshot, "sitemap_urls", []) or [])
    discovered = max(discovered, sitemap_urls)
    return read, discovered, (read / discovered if discovered else 1.0)


def verify_findings(
    findings: list[dict[str, Any]],
    snapshot: Any,
    *,
    refetch: Callable[[str], Any] | None = None,
    budget_seconds: float = 25.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-check every candidate finding. Returns ``(kept, log)``.

    ``refetch`` is an optional ``url -> Page`` callable used to confirm that a
    fetch failure reproduces; when it is absent that rule simply does not run,
    so the stage degrades to static checks rather than failing.
    """
    if not findings:
        return [], []

    deadline = time.monotonic() + budget_seconds
    fetched = {page.url for page in getattr(snapshot, "pages", []) or []}
    read, discovered, ratio = _coverage(snapshot)

    kept: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []

    def record(finding: dict[str, Any], rule: str, outcome: str, reason: str) -> None:
        log.append({
            "title": finding.get("title", ""),
            "category": finding.get("category", ""),
            "rule": rule,
            "outcome": outcome,
            "reason": reason,
        })

    for finding in findings:
        proof = finding.get("proof") or {}
        locations = [str(loc) for loc in (finding.get("locations") or [])]

        # -- Rule 1: a fetch failure must reproduce ---------------------------
        # A DNS hiccup, a rate-limit or a proxy reset during the crawl produces a
        # critical finding that says the site is unreachable. Confirming it costs
        # one request and is the difference between reporting a defect and
        # reporting our own network.
        if proof.get("transport_error") and refetch is not None and time.monotonic() < deadline:
            target = locations[0] if locations else getattr(snapshot, "entry_url", "")
            if target:
                retry = refetch(target)
                if getattr(retry, "ok", False):
                    record(finding, "transient_fetch", "dropped",
                           f"re-fetch of {target} returned "
                           f"{getattr(retry, 'http_label', 'HTTP 200')} with a body; "
                           "the original failure did not reproduce")
                    continue
                # "confirmed", not "kept": the finding still has to clear the
                # remaining rules, and a terminal outcome recorded here would be
                # counted a second time when it does.
                record(finding, "transient_fetch", "confirmed",
                       f"re-fetch of {target} failed again "
                       f"({getattr(retry, 'error', None) or getattr(retry, 'http_label', 'no response')})")

        # -- Rule 2: cited locations must be pages we actually read ------------
        # A finding may only point at evidence this run holds. Anything else is
        # unreproducible for whoever acts on the report.
        if locations and fetched:
            unverifiable = [loc for loc in locations
                            if loc not in fetched and not loc.endswith(("robots.txt", "sitemap.xml"))]
            if unverifiable and len(unverifiable) == len(locations):
                record(finding, "location_integrity", "dropped",
                       f"cites {len(unverifiable)} location(s) that were never fetched: "
                       f"{unverifiable[0]}")
                continue

        # -- Rule 3: a site-wide absence claim needs coverage to stand on ------
        # Never a drop: thin coverage makes the claim unproven, not false. The
        # confidence drop and the annotation are what let a reader weigh it.
        if _is_site_wide_absence(finding) and discovered > SMALL_SITE_URLS and ratio < THIN_COVERAGE_RATIO:
            if finding.get("confidence") == "high":
                finding["confidence"] = "medium"
            finding["evidence"] = (
                f"{finding['evidence']} Coverage note: this site-wide claim rests on "
                f"{read} pages read of {discovered} URLs discovered "
                f"({ratio:.0%}); pages outside that sample were not examined."
            )
            record(finding, "coverage_adequacy", "downgraded",
                   f"site-wide claim from {read}/{discovered} URLs ({ratio:.0%} coverage)")
            kept.append(finding)
            continue

        # -- Rule 4: counts in the proof cannot exceed what was measured -------
        affected = proof.get("pages_affected")
        if isinstance(affected, int) and read and affected > read:
            record(finding, "proof_consistency", "dropped",
                   f"claims {affected} pages affected but only {read} pages were read")
            continue

        record(finding, "verified", "kept", "evidence re-checked against the snapshot")
        kept.append(finding)

    return kept, log


def verification_summary(log: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact, report-safe view of what the stage did."""
    # Only terminal outcomes are counted; a finding may also carry non-terminal
    # notes (a confirmed re-fetch, say) and must not be tallied twice.
    terminal = {"kept", "dropped", "downgraded"}
    outcomes: dict[str, int] = {}
    for entry in log:
        if entry["outcome"] in terminal:
            outcomes[entry["outcome"]] = outcomes.get(entry["outcome"], 0) + 1
    return {
        "candidates": sum(outcomes.values()),
        "kept": outcomes.get("kept", 0),
        "dropped": outcomes.get("dropped", 0),
        "downgraded": outcomes.get("downgraded", 0),
        "decisions": [e for e in log if e["outcome"] != "kept"],
    }
