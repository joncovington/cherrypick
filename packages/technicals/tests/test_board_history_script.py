"""The signal board over history (scripts/board_history.py): the fast path reads every session exactly
as the live board does, and the look-ahead guard refuses a build that reads past the session.

Synthetic series only: no store. The guard is shown to FAIL on a planted one-session leak, because a
check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import math
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from cherrypick.technicals import board, symbols
from cherrypick.technicals.adjust import AdjustedBar
from cherrypick.technicals.revisions import Event, by_session

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "board_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("board_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_history"] = module  # its dataclass resolves annotations through it
    spec.loader.exec_module(module)
    return module


bh = _module()
N = 420


def _calendar() -> list[str]:
    d, out = date(2019, 1, 2), []
    while len(out) < N:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _walk(seed: int, drift: float = 0.0, vol: float = 0.02) -> list[float]:
    rng = random.Random(seed)
    x, out = 100.0, []
    for i in range(N):
        x *= math.exp(drift + vol * rng.gauss(0, 1) + 0.03 * math.sin(i / (7 + seed % 5)))
        out.append(x)
    return out


def _bars(cal: list[str], closes: list[float], skip: set[int] = frozenset()) -> list[AdjustedBar]:
    return [
        AdjustedBar(d, c, c * 1.01, c * 0.99, c, 1e6, 1.0)
        for k, (d, c) in enumerate(zip(cal, closes, strict=True))
        if k not in skip
    ]


def _market():
    cal = _calendar()
    funds = {
        f: dict(zip(cal, _walk(10 + k), strict=True))
        for k, f in enumerate(sorted(set(board.SECTOR_FUND.values())))
    }
    funds["SPY"] = dict(zip(cal, _walk(1, 0.0003, 0.01), strict=True))
    funds["AOR"] = dict(zip(cal, _walk(2, 0.0001, 0.006), strict=True))
    mk = bh.Market(cal, [funds["SPY"][d] for d in cal], funds)
    mk.sector_of = {"AAA": "Technology", "BBB": "Energy", "CCC": "Utilities"}
    for d in cal:
        mk.rotation[d] = bh.rotation.states_on(
            d, {f: {x: c for x, c in s.items() if x <= d} for f, s in funds.items()}, symbols.ASSET_ETFS
        )
    mk.snapshots = cal[::5]
    rng = random.Random(7)
    mk.fund_pct = {s: {n: rng.random() for n in ("AAA", "BBB", "CCC")} for s in mk.snapshots}
    mk.cutoffs = {d: sorted(rng.uniform(-0.4, 0.6) for _ in range(9)) for d in cal}
    mk.events = [
        Event(
            sym,
            datetime.fromisoformat(cal[k] + "T14:00:00").replace(tzinfo=UTC),
            "Firm",
            "Hold",
            "Buy",
            act,
            100.0,
            100.0,
        )
        for k, sym, act in [
            (250, "AAA", "up"),
            (300, "BBB", "down"),
            (301, "AAA", "down"),
            (380, "CCC", "init"),
        ]
    ]
    mk.rev = by_session(mk.events)
    bars = {
        "AAA": _bars(cal, _walk(3, 0.001, 0.025)),
        "BBB": _bars(cal, _walk(4, -0.001, 0.03), skip={310, 311, 350}),
        "CCC": _bars(cal, _walk(5, 0.0, 0.015)),
    }
    return mk, bars


def _samples(mk, bars):
    return [(s, b.date) for s, bs in bars.items() for b in bs[::2] if b.date >= mk.calendar[200]]


def test_the_fast_path_reads_every_session_as_the_live_board_does():
    mk, bars = _market()
    got = bh.verify(_samples(mk, bars), bars.get, mk)
    assert got["differ"] == 0 and got["sampled"] > 300
    # And the readings are not trivially empty: several methods fire on both sides.
    seen = set()
    for s, bs in bars.items():
        for bull, bear in bh.name_tags(s, bs, mk, mk.calendar[200], mk.calendar[-1]).values():
            seen |= {(m, 1) for m in bull} | {(m, -1) for m in bear}
    assert {"stage", "rotation", "fundamentals", "fvp", "revision", "scan"} <= {m for m, _ in seen}
    assert any(side < 0 for _, side in seen) and any(side > 0 for _, side in seen)


def test_the_guard_fails_on_a_planted_one_session_leak(monkeypatch):
    mk, bars = _market()
    honest = bh.scan_sides

    def leaky(highs, lows, closes):
        # Reads the next session's scan: the kind of off-by-one a whole-series indicator invites.
        s = honest(highs, lows, closes)
        return s[1:] + [set()]

    monkeypatch.setattr(bh, "scan_sides", leaky)
    with pytest.raises(bh.LookAheadError, match="differ from the as-of board"):
        bh.verify(_samples(mk, bars), bars.get, mk)


def test_the_guard_fails_on_a_snapshot_read_before_its_date(monkeypatch):
    mk, bars = _market()
    # The fast path takes the NEXT snapshot (published after the session): the guard must catch it.
    monkeypatch.setattr(
        bh.Market,
        "snapshot_on",
        lambda self, day: self.snapshots[min(len(self.snapshots) - 1, bh.bisect_right(self.snapshots, day))],
    )
    with pytest.raises(bh.LookAheadError):
        bh.verify(_samples(mk, bars), bars.get, mk)


def test_the_reference_refuses_any_input_dated_after_the_session():
    mk, bars = _market()
    day = mk.calendar[300]
    kw = bh.as_of(day, "AAA", bars["AAA"], mk)
    bh.assert_as_of(day, kw, mk.snapshot_on(day))  # clean
    kw["bars"]["AAA"] = bars["AAA"][:302]  # one bar past the session
    with pytest.raises(bh.LookAheadError, match="dated after the session"):
        bh.assert_as_of(day, kw, mk.snapshot_on(day))
    kw = bh.as_of(day, "AAA", bars["AAA"], mk)
    with pytest.raises(bh.LookAheadError):
        bh.assert_as_of(day, kw, mk.calendar[305])  # a snapshot from the future


def test_rank_score_at_matches_the_live_rank_score():
    closes = _walk(9)
    for i in (0, 125, 126, 127, 300, N - 1):
        assert bh.rank_score_at(closes, i) == bh.levels.rank_score(closes[: i + 1])


def test_selector_b_signals_on_a_fresh_count_of_three_on_a_counted_session():
    spec = importlib.util.spec_from_file_location(
        "credit_spread_plan", SCRIPT.parent / "credit_spread_plan.py"
    )
    plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan)
    days = [f"2021-03-{d:02d}" for d in range(1, 9)]
    nm = SimpleNamespace(dates=days, member=[True] * 8, shadow=[False] * 7 + [True])
    counts = {
        days[1]: (3, 0),  # fresh bullish
        days[2]: (4, 1),  # still bullish: no new signal
        days[3]: (1, 3),  # fresh bearish
        days[5]: (3, 3),  # both fresh after a gap (a session below 3 on each side)
        days[7]: (0, 5),  # in a corporate-action shadow: not counted
    }
    got = plan.board_signals(nm, counts, allow=lambda i: True)
    assert got == [(1, "put"), (3, "call"), (5, "put"), (5, "call")]
    # Under $300M a day that session: no signal.
    assert plan.board_signals(nm, counts, allow=lambda i: i != 1) == [(3, "call"), (5, "put"), (5, "call")]
