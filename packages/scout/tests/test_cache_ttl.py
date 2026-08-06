import pytest

from cherrypick.scout.services import cache as _cache


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


def test_a_cold_cache_calls_fetch_and_stores(conn):
    calls = []

    def fetch():
        calls.append(1)
        return {"v": 1}

    payload, fetched_at, stale = _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=fetch, now=1000.0)
    assert payload == {"v": 1}
    assert fetched_at == 1000.0
    assert stale is False
    assert len(calls) == 1


def test_a_hit_within_ttl_skips_fetch(conn):
    calls = []

    def fetch():
        calls.append(1)
        return {"v": len(calls)}

    _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=fetch, now=1000.0)
    payload, fetched_at, stale = _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=fetch, now=1030.0)
    assert payload == {"v": 1}
    assert fetched_at == 1000.0
    assert stale is False
    assert len(calls) == 1  # second call was a pure cache hit


def test_expiry_past_ttl_refetches(conn):
    calls = []

    def fetch():
        calls.append(1)
        return {"v": len(calls)}

    _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=fetch, now=1000.0)
    payload, fetched_at, stale = _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=fetch, now=1061.0)
    assert payload == {"v": 2}
    assert fetched_at == 1061.0
    assert stale is False
    assert len(calls) == 2


def test_fetch_failure_serves_stale_payload(conn):
    _cache.get_or_fetch(conn, "b", "k", ttl=1, fetch_fn=lambda: {"v": "good"}, now=1000.0)

    def boom():
        raise RuntimeError("rate limited")

    payload, fetched_at, stale = _cache.get_or_fetch(conn, "b", "k", ttl=1, fetch_fn=boom, now=2000.0)
    assert payload == {"v": "good"}
    assert fetched_at == 1000.0
    assert stale is True


def test_fetch_failure_with_no_prior_cache_raises(conn):
    def boom():
        raise RuntimeError("no data yet")

    with pytest.raises(RuntimeError):
        _cache.get_or_fetch(conn, "b", "k", ttl=60, fetch_fn=boom, now=1000.0)


def test_fresh_force_bypasses_ttl_but_is_floored(conn):
    calls = []

    def fetch():
        calls.append(1)
        return {"v": len(calls)}

    _cache.get_or_fetch(conn, "b", "k", ttl=600, fetch_fn=fetch, now=1000.0)
    # force=True inside the refresh floor (60s default) must still be a cache hit.
    payload, fetched_at, _ = _cache.get_or_fetch(
        conn, "b", "k", ttl=600, fetch_fn=fetch, force=True, now=1030.0
    )
    assert payload == {"v": 1}
    assert fetched_at == 1000.0
    assert len(calls) == 1

    # Past the floor, force=True refetches even though the TTL itself has not expired.
    payload, fetched_at, _ = _cache.get_or_fetch(
        conn, "b", "k", ttl=600, fetch_fn=fetch, force=True, now=1061.0
    )
    assert payload == {"v": 2}
    assert fetched_at == 1061.0
    assert len(calls) == 2


def test_buckets_and_keys_are_independent(conn):
    _cache.get_or_fetch(conn, "bucket-a", "k", ttl=60, fetch_fn=lambda: {"v": "a"}, now=1000.0)
    _cache.get_or_fetch(conn, "bucket-b", "k", ttl=60, fetch_fn=lambda: {"v": "b"}, now=1000.0)
    a, _, _ = _cache.get_or_fetch(conn, "bucket-a", "k", ttl=60, fetch_fn=lambda: {"v": "x"}, now=1010.0)
    b, _, _ = _cache.get_or_fetch(conn, "bucket-b", "k", ttl=60, fetch_fn=lambda: {"v": "y"}, now=1010.0)
    assert a == {"v": "a"}
    assert b == {"v": "b"}
