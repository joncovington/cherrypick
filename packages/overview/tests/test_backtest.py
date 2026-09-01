"""The zone overlay: the no-look-ahead join, the warmup, and what the result refuses to claim.

The load-bearing test here is `test_a_zone_is_never_credited_with_its_own_days_move`. Everything
else in this module could be right and the backtest would still be worthless if a session's zone
were scored on the move it is being credited with -- so that off-by-one is pinned directly, with
data built so a look-ahead would be visibly, unmistakably profitable.
"""

from datetime import date, timedelta

from cherrypick.overview import backtest, score, symbols

SECTORS = tuple(symbols.SECTOR_ETFS)


def _days(n, start="2022-01-03"):
    """n weekday sessions from `start`."""
    out, day = [], date.fromisoformat(start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _series(days, values):
    return [{"session": d, "close": float(v)} for d, v in zip(days, values, strict=False)]


def _history(n=400, vix=None, spx=None):
    """A history rich enough for every signal, over n sessions."""
    days = _days(n)
    history = {
        "VIX": _series(days, vix if vix else [18.0 + (i % 7) * 0.5 for i in range(n)]),
        "VIX3M": _series(days, [21.0 + (i % 5) * 0.3 for i in range(n)]),
        "HYG": _series(days, [78.0 + (i % 11) * 0.15 for i in range(n)]),
        "TLT": _series(days, [92.0 - (i % 13) * 0.1 for i in range(n)]),
        "SPX": _series(days, spx if spx else [4000.0 + i for i in range(n)]),
    }
    for index, etf in enumerate(SECTORS):
        base = 100.0 + index
        history[etf] = _series(days, [base + i * 0.05 for i in range(n)])
    return days, history


# --------------------------------------------------------------------------- the join

def test_a_zone_is_never_credited_with_its_own_days_move():
    """Built so a look-ahead would be obvious: SPX rips exactly on the sessions where VIX spikes.

    A correct join credits the calm-scored day with the NEXT day's move. A look-ahead join would
    credit the spike day's own big move to the low score it produced, and the defensive bucket
    would show a large positive mean instead of the following day's ordinary move.
    """
    n = 400
    days = _days(n)
    vix = [18.0] * n
    spx = [4000.0] * n
    for i in range(300, n, 10):          # every tenth late session: VIX spikes, SPX rips same day
        vix[i] = 60.0
        spx[i] = spx[i - 1] * 1.10
        for j in range(i + 1, n):        # carry the level forward so the next move is ordinary
            spx[j] = spx[i]
    _, history = _history(n=n, vix=vix, spx=spx)
    history["SPX"] = _series(days, spx)

    result = backtest.run(history, SECTORS)
    rows = {row["session"]: row for row in result["series"]}

    # On a spike session the score is depressed; the return credited to it is the move AFTER it,
    # which by construction is 0 -- not the +10% that happened ON it.
    spike_sessions = [days[i] for i in range(300, n - 1, 10) if days[i] in rows]
    assert spike_sessions, "fixture produced no scored spike sessions"
    for session in spike_sessions:
        assert rows[session]["spx_forward_return_pct"] == 0.0

    # And the day BEFORE a spike is the one credited with the +10%.
    for i in range(300, n - 1, 10):
        prior = days[i - 1]
        if prior in rows:
            assert round(rows[prior]["spx_forward_return_pct"], 2) == 10.0


def test_forward_returns_key_off_the_session_they_follow():
    days = _days(5)
    history = {"SPX": _series(days, [100.0, 110.0, 121.0, 121.0, 121.0])}
    returns = backtest._spx_returns(history)
    assert round(returns[days[0]], 4) == 10.0   # the move from day0's close to day1's
    assert round(returns[days[1]], 4) == 10.0
    assert returns[days[3]] == 0.0
    assert days[4] not in returns               # nothing follows the last session


# --------------------------------------------------------------------------- warmup and honesty

def test_the_warmup_produces_no_scored_days():
    _, history = _history(n=backtest.WARMUP_SESSIONS - 10)
    result = backtest.run(history, SECTORS)
    assert result["sessions_scored"] == 0
    assert result["sessions_joined"] == 0
    assert str(backtest.WARMUP_SESSIONS) in result["unscored_reason"]


def test_scored_days_start_only_after_a_full_year_of_history():
    n = 400
    days, history = _history(n=n)
    scored = backtest.score_series(history, SECTORS)
    assert scored, "expected some scored sessions"
    # The first scoreable session is the one with WARMUP_SESSIONS closes at or before it.
    assert scored[0]["session"] == days[backtest.WARMUP_SESSIONS - 1]
    assert len(scored) == n - backtest.WARMUP_SESSIONS + 1


def test_as_of_slicing_never_reveals_a_later_session():
    _, history = _history(n=300)
    cut = history["VIX"][100]["session"]
    sliced = backtest._as_of(history, cut)
    for series in sliced.values():
        assert all(row["session"] <= cut for row in series)


def test_readings_come_from_that_days_close():
    days, history = _history(n=300)
    target = days[150]
    readings = backtest._readings_on(history, target)
    expected = next(r["close"] for r in history["VIX"] if r["session"] == target)
    assert readings["vix"]["value"] == expected


def test_result_states_it_is_a_benchmark_and_not_pnl():
    _, history = _history(n=300)
    result = backtest.run(history, SECTORS)
    assert "not suite P&L" in result["not_pnl"]
    assert "SPX" in result["benchmark"]
    assert "previous session" in result["no_look_ahead"]


def test_zone_shares_and_distribution_are_reported():
    _, history = _history(n=400)
    result = backtest.run(history, SECTORS)
    assert set(result["zones"]) == set(backtest.ZONES)
    counted = sum(result["zones"][z]["sessions"] for z in backtest.ZONES)
    assert counted == result["sessions_joined"]
    # The distribution is the diagnostic that says whether the zones separated anything at all.
    dist = result["score_distribution"]
    assert dist["min"] is not None and dist["max"] is not None
    assert dist["min"] <= dist["median"] <= dist["max"]


def test_a_zone_nobody_landed_in_reports_none_not_zero():
    # A calm, trending fixture should never reach DEFENSIVE; that bucket must read as empty
    # rather than as a measured 0.00% return.
    _, history = _history(n=400)
    result = backtest.run(history, SECTORS)
    empty = [z for z in backtest.ZONES if result["zones"][z]["sessions"] == 0]
    assert empty, "fixture was expected to leave at least one zone unvisited"
    for zone in empty:
        assert result["zones"][zone]["spx_mean_forward_return_pct"] is None


def test_the_backtest_scores_through_the_same_function_the_pack_uses():
    # Not a style check: a second implementation of the blend would let the thing under test drift
    # from the thing in production. Scoring one day by hand must match the series entry.
    days, history = _history(n=300)
    target = days[280]
    entry = next(e for e in backtest.score_series(history, SECTORS) if e["session"] == target)
    direct = score.evaluate(backtest._readings_on(history, target),
                            backtest._as_of(history, target), SECTORS)
    assert entry["score"] == direct["score"]
    assert entry["zone"] == direct["zone"]
