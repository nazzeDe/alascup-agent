from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _CacheEntry:
    result: Any
    timestamp: float


@dataclass
class _InFlightEntry:
    context: dict[str, str]
    future: asyncio.Future


@dataclass
class ToolCache:
    """In-memory request_id-keyed result cache with TTL."""

    ttl: float = 600.0
    _store: dict[str, _CacheEntry] = field(default_factory=dict)
    _inflight: dict[str, _InFlightEntry] = field(default_factory=dict)

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
        self._inflight.clear()

    def expire_stale(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        now = time.monotonic()
        stale = [rid for rid, e in self._store.items() if now - e.timestamp > self.ttl]
        for rid in stale:
            del self._store[rid]
        return len(stale)

    def get_inflight(
        self, request_id: str
    ) -> tuple[dict[str, str], asyncio.Future] | None:
        entry = self._inflight.get(request_id)
        if entry is None:
            return None
        return entry.context, entry.future

    def put_inflight(
        self, request_id: str, context: dict[str, str], future: asyncio.Future
    ) -> None:
        self._inflight[request_id] = _InFlightEntry(context=context, future=future)

    def evict_inflight(self, request_id: str) -> None:
        self._inflight.pop(request_id, None)


def create_cache(ttl: float) -> ToolCache:
    return ToolCache(ttl=ttl)
