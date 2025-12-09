from __future__ import annotations

import time

from app.cache import NL2SQLCache


def test_cache_hit_and_miss() -> None:
    cache = NL2SQLCache(ttl=60.0)
    key = "k1"
    payload = {"x": 1}

    # First access → miss
    assert cache.get(key) is None

    # After set → hit
    cache.set(key, payload)
    assert cache.get(key) == payload


def test_cache_respects_ttl() -> None:
    cache = NL2SQLCache(ttl=0.01)
    key = "k2"
    payload = {"y": 2}

    cache.set(key, payload)
    # Immediately should be hit
    assert cache.get(key) == payload

    # After TTL has passed → miss
    time.sleep(0.02)
    assert cache.get(key) is None
