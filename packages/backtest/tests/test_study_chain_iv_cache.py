"""The study kit's Chain solves each quote's implied vol once and returns what an uncached solve would."""

from __future__ import annotations

from cherrypick.backtest.study import chains as chains_mod
from cherrypick.backtest.study.chains import Chain
from cherrypick.core import bs

STRIKES = [float(k) for k in range(90, 111)]


def _rows():
    rows = []
    for k in STRIKES:
        call = max(100.0 - k, 0.0) + 2.0 + 0.05 * abs(k - 100)
        put = max(k - 100.0, 0.0) + 2.0 + 0.05 * abs(k - 100)
        rows.append((k, "CALL", round(call - 0.05, 2), round(call + 0.05, 2)))
        rows.append((k, "PUT", round(put - 0.05, 2), round(put + 0.05, 2)))
    return rows


def _uncached(chain: Chain, q: dict):
    """The solve as it was before the cache."""
    if not chain.forward or not chains_mod.legbars.two_sided(q):
        return None
    iv = bs.implied_vol(chain.forward, q["strike"], chain.years, chains_mod._mid(q), q["right"], 0.0)
    return iv if iv and iv > chains_mod.MIN_IV else None


def test_cached_iv_and_delta_match_an_uncached_solve():
    chain = Chain(_rows(), "2026-01-02", "2026-02-20")
    assert chain.forward
    for right in ("C", "P"):
        for q in chain.by[right].values():
            want = _uncached(chain, q)
            assert chain.iv(q) == want
            assert chain.iv(q) == want  # the second ask comes from the cache
            if want is not None:
                assert chain.delta(q) == bs.delta(chain.forward, q["strike"], chain.years, want, right, 0.0)


def test_nearest_solves_each_strike_once(monkeypatch):
    chain = Chain(_rows(), "2026-01-02", "2026-02-20")
    calls = []
    real = bs.implied_vol

    def counting(*a, **k):
        calls.append(a[1:3])
        return real(*a, **k)

    monkeypatch.setattr(chains_mod.bs, "implied_vol", counting)
    first = [chain.nearest("P", 0.2), chain.nearest("C", 0.2)]
    solved = len(calls)
    second = [chain.nearest("P", 0.2), chain.nearest("C", 0.2), chain.nearest("P", 0.1)]
    assert second[:2] == first
    assert len(calls) == solved  # no further solves: every strike was already held
    assert solved <= len(STRIKES) * 2


def test_a_changed_quote_never_reads_a_stale_value():
    chain = Chain(_rows(), "2026-01-02", "2026-02-20")
    q = chain.by["P"][95.0]
    before = chain.iv(q)
    moved = dict(q, bid=q["bid"] + 0.5, ask=q["ask"] + 0.5)
    assert chain.iv(moved) == _uncached(chain, moved)
    assert chain.iv(moved) != before
    assert chain.iv(q) == before
