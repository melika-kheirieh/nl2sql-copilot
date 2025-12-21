from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from adapters.metrics.prometheus import cache_events_total


class NL2SQLCache:
    """
    Tiny in-memory TTL cache for NL2SQL responses.
    Stores serialized response payloads (dicts) keyed by a hash.
    """

    def __init__(self, ttl: float = 15.0) -> None:
        self.ttl = ttl
        self._store: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def _gc(self, now: float) -> None:
        """Remove expired entries based on the configured TTL."""
        expired_keys = [
            key for key, (ts, _) in self._store.items() if now - ts > self.ttl
        ]
        for key in expired_keys:
            del self._store[key]

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Return cached payload if present and not expired, otherwise None.
        Also updates Prometheus counters for hits/misses.
        """
        now = time.time()
        self._gc(now)

        entry = self._store.get(key)
        if entry is None:
            cache_events_total.labels(hit="false").inc()
            return None

        ts, payload = entry
        if now - ts <= self.ttl:
            cache_events_total.labels(hit="true").inc()
            return payload

        # Entry is expired
        del self._store[key]
        cache_events_total.labels(hit="false").inc()
        return None

    def set(self, key: str, payload: Dict[str, Any]) -> None:
        """Store payload under the given key with current timestamp."""
        self._store[key] = (time.time(), payload)
