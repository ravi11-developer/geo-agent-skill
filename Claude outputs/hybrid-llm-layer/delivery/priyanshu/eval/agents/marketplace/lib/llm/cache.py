#!/usr/bin/env python3
"""Content-addressed cache for LLM responses.

The key is the tuple the spec requires - snapshot hash, prompt version, schema
version, taxonomy version, provider, model, feature mode - plus the task name
and a digest of the rendered request, so two different prompts for the same site
never collide.

Two layers: an in-process dictionary (so a single audit never repeats a call)
and an optional on-disk JSON store (so a benchmark can be resumed without
re-billing).  The disk layer is best-effort: a corrupt or unwritable cache
degrades to a miss and never fails an audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any

from .config import LLMConfig
from .provider import LLMRequest, LLMResponse

CACHE_VERSION = "1"


def digest(*parts: Any) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(part).encode("utf-8", errors="replace"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def snapshot_hash(pages: list[tuple[str, int | None, str]]) -> str:
    """Stable identity for a crawl: (url, status, body-digest) for every page.

    Derived from content rather than time, so re-running an audit over the same
    captured fixture produces the same key and therefore a cache hit.
    """
    material = sorted(f"{url}|{status}|{body}" for url, status, body in pages)
    return digest(CACHE_VERSION, *material)


def cache_key(config: LLMConfig, snapshot_id: str, request: LLMRequest) -> str:
    return digest(
        CACHE_VERSION,
        snapshot_id,
        request.task,
        *config.cache_identity(),
        digest(request.system, request.user),
    )


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.lookups, 4) if self.lookups else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits, "misses": self.misses, "writes": self.writes,
            "errors": self.errors, "hit_rate": self.hit_rate,
        }


class ResponseCache:
    """Memory-first cache with an optional durable directory behind it."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.enabled = config.cache_enabled and not config.is_off
        self.stats = CacheStats()
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._dir = config.cache_dir if (self.enabled and config.cache_dir) else None
        if self._dir:
            try:
                os.makedirs(self._dir, exist_ok=True)
            except OSError:
                self.stats.errors += 1
                self._dir = None

    # -- internals -------------------------------------------------------
    def _path(self, key: str) -> str:
        assert self._dir is not None
        return os.path.join(self._dir, f"{key[:2]}", f"{key}.json")

    def _read_disk(self, key: str) -> dict[str, Any] | None:
        if not self._dir:
            return None
        try:
            with open(self._path(key), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _write_disk(self, key: str, payload: dict[str, Any]) -> None:
        if not self._dir:
            return
        path = self._path(key)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError:
            self.stats.errors += 1

    # -- API -------------------------------------------------------------
    def get(self, key: str, task: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        with self._lock:
            payload = self._memory.get(key)
        if payload is None:
            payload = self._read_disk(key)
            if payload is not None:
                with self._lock:
                    self._memory[key] = payload
        if payload is None:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return LLMResponse(
            task=task,
            data=payload.get("data"),
            raw_text=payload.get("raw_text", ""),
            provider=payload.get("provider", ""),
            model=payload.get("model", ""),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            latency_ms=0,
            cached=True,
            attempts=0,
        )

    def put(self, key: str, response: LLMResponse) -> None:
        if not self.enabled:
            return
        payload = {
            "data": response.data,
            "raw_text": response.raw_text,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        try:
            json.dumps(payload)
        except (TypeError, ValueError):
            self.stats.errors += 1
            return
        with self._lock:
            self._memory[key] = payload
        self.stats.writes += 1
        self._write_disk(key, payload)
