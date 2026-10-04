"""The entry/exit setups and the indicators they read.

Each setup is checked against its rule restated here in the test's own words: every entry satisfies
it, every exit satisfies its exit rule, and no bar between the two did (an exit is the FIRST bar its
rule allows). The fixture is a seeded random walk long enough for each setup to trade; each test
also asserts it traded, or it would prove nothing.
"""

from __future__ import annotations

import math
import random

from cherrypick.technicals import chart, indicators, setups, store


def _walk(n=3000, seed=6, sign=1):
    """Short swings riding a slow cycle and a small upward drift: seed 6 trades every setup at
    least three times (breakouts and pullbacks are rare in plain noise, as in real prices)."""
    rnd = random.Random(seed)
    closes, highs, lows, vols = [], [], [], []
    c = 100.0
    for i in range(n):
        drift = sign * (0.8 * math.sin(i / 15) + 0.4 * math.sin(i / 97) + 0.1)
        c = max(5.0, c * (1 + (drift + rnd.gauss(0, 0.7)) / 100))
        span = c * rnd.uniform(0.004, 0.02)
        closes.append(c)
        highs.append(c + span * rnd.random())
        lows.append(c - span * rnd.random())
        vols.append(1e6 * (3.0 if rnd.random() < 0.06 else rnd.uniform(0.7, 1.3)))
    return highs, lows, closes, vols


def _readings(**kw):
    return setups.readings(*_walk(**kw))


def _safe(rule):
    """A rule read where an input is still undefined is simply not met."""

    def ok(*a):
        try:
            return bool(rule(*a))
        except (TypeError, IndexError, ValueError):
            return False

    return ok


def _check(trades, entry_ok, exit_ok, size):
    """Every entry meets the rule and every exit is the first bar its rule allows -- and, the other
    way, every bar with no position open that meets the entry rule IS an entry, so a rule made
    stricter than stated fails as surely as one made looser."""
    entry_ok, exit_ok = _safe(entry_ok), _safe(exit_ok)
    assert trades, "the fixture should trade this setup, or the test proves nothing"
    held = set()
    for t in trades:
        held.update(range(t.entry, size if t.exit is None else t.exit + 1))
    entries = {t.entry for t in trades}
    missed = [i for i in range(1, size) if i not in held and entry_ok(i)]
    assert not missed, f"bars meeting the entry rule with no position open: {missed[:5]}"
    assert entries <= held
    last = -1
    for k, t in enumerate(trades):
        assert t.entry > last, "positions overlap"
        assert entry_ok(t.entry), t
        if t.exit is None:
            assert k == len(trades) - 1, "only the last position can still be open"
            continue
        assert t.exit > t.entry
        assert exit_ok(t.exit, t), t
        assert not any(exit_ok(j, t) for j in range(t.entry + 1, t.exit)), f"an earlier bar exits {t}"
        last = t.exit


# ------------------------------------------------------------------------------------- indicators


def test_atr_is_the_range_on_bars_that_never_gap():
    highs, lows, closes = [11.0] * 30, [9.0] * 30, [10.0] * 30
    a = indicators.atr(highs, lows, closes, 14)
    assert a[13] is None and a[14] == 2.0 and a[-1] == 2.0


def test_adx_is_high_in_a_steady_trend_and_low_in_a_chop():
    up = [100.0 + i for i in range(80)]
    assert indicators.adx([c + 0.5 for c in up], [c - 0.5 for c in up], up, 14)[-1] > 90
    chop = [100.0 + (i % 2) for i in range(80)]
    assert indicators.adx([c + 0.5 for c in chop], [c - 0.5 for c in chop], chop, 14)[-1] < 10
    first = indicators.adx([c + 0.5 for c in up], [c - 0.5 for c in up], up, 14)
    assert first[26] is None and first[27] is not None  # 2n - 1


def test_an_outside_bar_counts_only_its_larger_move():
    """Highs creep up 0.1 while lows fall 1.0: every bar's down move is the larger, so +DM is zero,
    +DI is zero and ADX is 100. Counting the up move too would pull it under."""
    highs = [100.0 + 0.1 * i for i in range(60)]
    lows = [90.0 - 1.0 * i for i in range(60)]
    closes = [(h + lo) / 2 for h, lo in zip(highs, lows, strict=True)]
    assert math.isclose(indicators.adx(highs, lows, closes, 14)[-1], 100.0)


def test_supertrend_ratchets_up_in_a_rise_and_turns_down_on_a_collapse():
    closes = [100.0 + i for i in range(40)] + [100.0] * 5
    highs, lows = [c + 1 for c in closes], [c - 1 for c in closes]
    line, up = indicators.supertrend(highs, lows, closes, 10, 3.0)
    rising = [v for v, u in zip(line[10:40], up[10:40], strict=True) if u]
    assert all(u for u in up[10:40]) and rising == sorted(rising), "the lower band only ratchets up"
    assert up[40] is False and line[40] > closes[40]


def test_bollinger_is_the_trend_scores_band():
    closes = [float(10 + (i % 5)) for i in range(40)]
    upper, mid, lower = indicators.bollinger(closes)
    sd = indicators.stdev(closes, 20)[-1]
    assert math.isclose(upper[-1] - mid[-1], 2 * sd) and math.isclose(mid[-1] - lower[-1], 2 * sd)


# ---------------------------------------------------------------------------------------- setups


def test_trend_following_enters_on_the_cross_and_leaves_on_the_first_close_under_the_21():
    r = _readings()
    _check(
        setups.trend(r),
        lambda i: (
            r.ema9[i - 1] <= r.ema21[i - 1]
            and r.ema9[i] > r.ema21[i]
            and r.closes[i] > r.ema50[i]
            and r.adx14[i] > 20
        ),
        lambda j, t: r.closes[j] < r.ema21[j],
        len(r.closes),
    )


def test_a_pullback_holds_the_21_and_leaves_at_the_prior_high_or_the_chandelier_stop():
    r = _readings()

    def stop(j, t):
        return r.closes[j] < max(r.highs[t.entry : j + 1]) - 3 * r.atr22[j]

    def leaves(j, t):
        return stop(j, t) or (t.target is not None and r.highs[j] >= t.target)

    trades = setups.pullback(r)
    _check(
        trades,
        lambda i: (
            r.ema9[i] > r.ema21[i] > r.ema50[i]
            and r.lows[i] <= r.ema21[i] < r.closes[i]
            and 40 <= min(r.rsi14[i - 4 : i + 1]) <= 50
        ),
        leaves,
        len(r.closes),
    )
    for t in trades:
        prior = max(r.highs[t.entry - 20 : t.entry])
        assert t.target == (prior if prior > r.closes[t.entry] else None)
        if t.exit is not None:
            assert t.reason == ("stop" if stop(t.exit, t) else "target")


def _hand(n, **series):
    """Readings written by hand: flat at 100 unless a series is given."""
    flat = [100.0] * n
    base = dict(
        highs=flat,
        lows=flat,
        closes=flat,
        volumes=[None] * n,
        ema9=flat,
        ema21=flat,
        ema50=flat,
        adx14=[0.0] * n,
        rsi14=[50.0] * n,
        atr14=[1.0] * n,
        atr22=[1.0] * n,
        bb_upper=flat,
        bb_mid=flat,
        bb_lower=flat,
        squeeze=[False] * n,
        supertrend=flat,
        supertrend_up=[True] * n,
    )
    return setups.Readings(**{**base, **series})


def test_a_pullback_bar_that_hits_both_target_and_stop_is_a_stop():
    """Entry on bar 25 (stacked, a dip to the 21 that holds, RSI 45); the 20 highs before it peak at
    110, the target. Bar 27 trades up to 111 but closes at 100, under 111 - 3 x ATR 1."""
    n = 30
    highs, lows, closes = [101.0] * n, [99.5] * n, [100.0] * n
    highs[10] = 110.0
    lows[25], closes[25] = 98.0, 100.5
    highs[27], closes[27] = 111.0, 100.0
    r = _hand(
        n,
        highs=highs,
        lows=lows,
        closes=closes,
        ema9=[102.0] * n,
        ema21=[99.0] * n,
        ema50=[95.0] * n,
        rsi14=[60.0] * 24 + [45.0] + [60.0] * 5,
    )
    assert setups.pullback(r) == [setups.Trade(25, 27, "stop", 110.0)]
    closes[27] = 109.0  # the same bar closing strong is the target
    assert setups.pullback(_hand(n, **{**vars(r), "closes": closes})) == [
        setups.Trade(25, 27, "target", 110.0)
    ]


def test_mean_reversion_buys_the_lower_band_and_leaves_at_the_middle_or_a_2_atr_stop():
    r = _readings()

    def stop(j, t):
        return r.closes[j] < r.closes[t.entry] - 2 * r.atr14[t.entry]

    trades = setups.reversion(r)
    _check(
        trades,
        lambda i: r.lows[i] <= r.bb_lower[i] and r.rsi14[i] < 30,
        lambda j, t: stop(j, t) or r.closes[j] >= r.bb_mid[j],
        len(r.closes),
    )
    assert {t.reason for t in trades if t.exit is not None} >= {"target"}


def test_a_breakout_needs_a_recent_squeeze_and_volume_and_leaves_when_supertrend_turns_down():
    r = _readings()

    def squeezed(i):
        # a squeeze on day j, one of the last 5: its width is the least of the 120 ending at j
        def width(x):
            return (r.bb_upper[x] - r.bb_lower[x]) / r.bb_mid[x]

        for j in range(i - 4, i + 1):
            window = range(j - 119, j + 1)
            if j - 119 >= 0 and all(r.bb_mid[x] is not None for x in window):
                if width(j) <= min(width(x) for x in window):
                    return True
        return False

    def volume(i):
        return r.volumes[i] > 1.5 * sum(r.volumes[i - 50 : i]) / 50

    _check(
        setups.breakout(r),
        lambda i: r.closes[i] > r.bb_upper[i] and squeezed(i) and volume(i),
        lambda j, t: r.supertrend_up[j] is False,
        len(r.closes),
    )


# ------------------------------------------------------------------------------------ the shorts
# Each mirrors its long: the same tests, every comparison reversed, on a walk that drifts down
# (seed 1 of the falling walk trades every short setup at least four times).


def _falling():
    return _readings(seed=1, sign=-1)


def test_trend_following_short_enters_on_the_cross_down_and_covers_on_a_close_over_the_21():
    r = _falling()
    _check(
        setups.trend_short(r),
        lambda i: (
            r.ema9[i - 1] >= r.ema21[i - 1]
            and r.ema9[i] < r.ema21[i]
            and r.closes[i] < r.ema50[i]
            and r.adx14[i] > 20
        ),
        lambda j, t: r.closes[j] > r.ema21[j],
        len(r.closes),
    )


def test_a_short_pullback_is_rejected_at_the_21_and_covers_at_the_prior_low_or_its_stop():
    r = _falling()

    def stop(j, t):
        return r.closes[j] > min(r.lows[t.entry : j + 1]) + 3 * r.atr22[j]

    trades = setups.pullback_short(r)
    _check(
        trades,
        lambda i: (
            r.ema9[i] < r.ema21[i] < r.ema50[i]
            and r.highs[i] >= r.ema21[i] > r.closes[i]
            and 50 <= max(r.rsi14[i - 4 : i + 1]) <= 60
        ),
        lambda j, t: stop(j, t) or (t.target is not None and r.lows[j] <= t.target),
        len(r.closes),
    )
    for t in trades:
        prior = min(r.lows[t.entry - 20 : t.entry])
        assert t.target == (prior if prior < r.closes[t.entry] else None)
        if t.exit is not None:
            assert t.reason == ("stop" if stop(t.exit, t) else "target")


def test_mean_reversion_short_sells_the_upper_band_and_covers_at_the_middle_or_a_2_atr_stop():
    r = _falling()

    def stop(j, t):
        return r.closes[j] > r.closes[t.entry] + 2 * r.atr14[t.entry]

    trades = setups.reversion_short(r)
    _check(
        trades,
        lambda i: r.highs[i] >= r.bb_upper[i] and r.rsi14[i] > 70,
        lambda j, t: stop(j, t) or r.closes[j] <= r.bb_mid[j],
        len(r.closes),
    )
    assert {t.reason for t in trades if t.exit is not None} >= {"target"}


def test_a_breakdown_needs_a_recent_squeeze_and_volume_and_covers_when_supertrend_turns_up():
    r = _falling()

    def squeezed(i):
        # a squeeze on day j, one of the last 5: its width is the least of the 120 ending at j
        def width(x):
            return (r.bb_upper[x] - r.bb_lower[x]) / r.bb_mid[x]

        for j in range(i - 4, i + 1):
            window = range(j - 119, j + 1)
            if j - 119 >= 0 and all(r.bb_mid[x] is not None for x in window):
                if width(j) <= min(width(x) for x in window):
                    return True
        return False

    _check(
        setups.breakout_short(r),
        lambda i: (
            r.closes[i] < r.bb_lower[i]
            and squeezed(i)
            and r.volumes[i] > 1.5 * sum(r.volumes[i - 50 : i]) / 50
        ),
        lambda j, t: r.supertrend_up[j] is True,
        len(r.closes),
    )


def test_missing_volume_never_confirms_a_breakout():
    vols = [100.0] * 60
    vols[-1] = 1000.0
    assert setups.volume_confirms(vols, 59)
    assert not setups.volume_confirms([*vols[:-1], None], 59)
    assert not setups.volume_confirms([*vols[:30], None, *vols[31:]], 59)
    assert setups.volume_confirms([None, *vols[1:]], 59), "the 51st session back is outside the average"


# ---------------------------------------------------------------------------------- chart file


def _land(conn, symbol, closes, highs, lows, vols):
    days = [f"{2023 + i // 336}-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(len(closes))]
    store.upsert_bars(
        conn,
        [(symbol, d, c, h, lo, c, v) for d, c, h, lo, v in zip(days, closes, highs, lows, vols, strict=True)],
    )
    conn.commit()
    return days


def test_spx_reads_spys_volume_and_says_so():
    """SPX lands with no volume; its breakout test reads SPY's, by date, and the file names SPY.
    Without the stand-in SPX could never trade a breakout."""
    highs, lows, closes, vols = _walk()
    conn = store.connect()
    _land(conn, "SPX", closes, highs, lows, [0.0] * len(closes))
    _land(conn, "SPY", [c / 10 for c in closes], [h / 10 for h in highs], [lo / 10 for lo in lows], vols)
    doc = chart.build(conn, "SPX")
    assert doc["volume_source"] == "SPY" and doc["bars"]["volume"][-1] is None
    bars = store.adjusted_bars(conn, "SPX")
    volumes, source = chart._volumes(conn, "SPX", bars)
    assert source == "SPY" and volumes == vols
    on_spy = setups.breakout(setups.readings(highs, lows, closes, volumes))
    assert on_spy and on_spy == setups.breakout(setups.readings(highs, lows, closes, vols))

    blind = setups.breakout(setups.readings(highs, lows, closes, [None] * len(closes)))
    assert blind == [], "with no volume at all, a breakout never confirms"
    assert chart.build(conn, "SPY")["volume_source"] is None


def test_the_chart_carries_every_setup_with_its_rule_lines_and_trades_in_the_window():
    highs, lows, closes, vols = _walk()
    conn = store.connect()
    days = _land(conn, "ABC", closes, highs, lows, vols)
    doc = chart.build(conn, "ABC")
    assert doc["chart_version"] == chart.CHART_VERSION
    assert [s["id"] for s in doc["setups"]] == [
        "trend",
        "pullback",
        "reversion",
        "breakout",
        "trend-short",
        "pullback-short",
        "reversion-short",
        "breakout-short",
    ]
    assert {(s["family"], s["side"]) for s in doc["setups"]} == {
        (f, side) for f in ("trend", "pullback", "reversion", "breakout") for side in ("long", "short")
    }
    shown = set(doc["bars"]["date"])
    for s in doc["setups"]:
        assert s["rule"] and set(s["lines"]) <= set(doc["setup_lines"])
        for t in s["trades"]:
            # every trade has an arrow in the window: its entry, or its exit, or it is still open
            assert t["entry_date"] in shown or t["exit_date"] in shown or t["exit_date"] is None
    for v in doc["setup_lines"].values():
        assert len(v) == len(doc["bars"]["date"])
    assert days[-1] == doc["session"]
