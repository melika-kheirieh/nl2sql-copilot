import time

from app.cache import NL2SQLCache


def test_cache_hit_and_miss():
    """
    Basic cache behavior:
    - First access should be a miss
    - After setting a value, it should be a hit
    """
    cache = NL2SQLCache(ttl=60.0)

    key = "k1"
    value = {"x": 1}

    # Cache miss before setting the value
    assert cache.get(key) is None

    # Cache hit after setting the value
    cache.set(key, value)
    assert cache.get(key) == value


def test_cache_respects_ttl(monkeypatch):
    """
    Cache entries should expire after TTL.
    This test controls time explicitly to avoid using sleep()
    and to keep the test deterministic.
    """
    fake_now = 1000.0

    def fake_time():
        return fake_now

    # Monkeypatch time.time() so we fully control "current time"
    monkeypatch.setattr(time, "time", fake_time)

    cache = NL2SQLCache(ttl=10.0)
    key = "k2"
    value = {"y": 2}

    cache.set(key, value)

    # Immediately after set -> cache hit
    assert cache.get(key) == value

    # Advance time beyond TTL without sleeping
    fake_now += 11.0

    # After TTL has passed -> cache miss
    assert cache.get(key) is None
