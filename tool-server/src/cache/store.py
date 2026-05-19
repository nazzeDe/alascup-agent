from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _CacheEntry:
    result: Any
    timestamp: float


@dataclass
class ToolCache:
    """In-memory request_id-keyed result cache with TTL."""

    ttl: float = 600.0
    _store: dict[str, _CacheEntry] = field(default_factory=dict)

    def get(self, request_id: str) -> Any | None:
        entry = self._store.get(request_id)
        if entry is None:
            return None
        if time.monotonic() - entry.timestamp > self.ttl:
            del self._store[request_id]
            return None
        return entry.result

    def put(self, request_id: str, result: Any) -> None:
        self._store[request_id] = _CacheEntry(result=result, timestamp=time.monotonic())

    def evict(self, request_id: str) -> None:
        self._store.pop(request_id, None)

    def clear(self) -> None:
        self._store.clear()

    def expire_stale(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        now = time.monotonic()
        stale = [rid for rid, e in self._store.items() if now - e.timestamp > self.ttl]
        for rid in stale:
            del self._store[rid]
        return len(stale)


def create_cache(ttl: float) -> ToolCache:
    return ToolCache(ttl=ttl)
