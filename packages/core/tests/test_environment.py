"""B8: one declared environment label, from the closes before the session."""

from cherrypick.core import environment as env


def _series(n, f):
    return [f(i) for i in range(n)]


def test_a_rising_market_is_an_uptrend():
    assert env.classify(_series(300, lambda i: 100 + i)) == "uptrend"


def test_twenty_percent_off_the_peak_is_a_downtrend_and_wins_over_the_rest():
    closes = _series(200, lambda i: 100 + i) + _series(60, lambda i: 299 - 3 * i)
    assert closes[-1] <= max(closes) * 0.8
    assert env.classify(closes) == "downtrend"


def test_a_flat_market_is_sideways():
    # Flat with a falling last month: not an uptrend (below a falling average), within 10% of six months ago.
    closes = [100.0] * 200 + _series(25, lambda i: 100 - 0.2 * i)
    assert env.classify(closes) == "sideways"


def test_what_no_rule_covers_is_mixed():
    # 15% below six months ago (not sideways), 15% off the peak (not a downtrend), falling (not up).
    closes = [100.0] * 200 + _series(30, lambda i: 100 - 0.5 * i)
    assert env.classify(closes) == "mixed"


def test_too_little_history_is_none():
    assert env.classify([100.0] * 50) is None


def test_a_session_is_labelled_from_the_closes_before_it():
    bars = [(f"d{i:04d}", 100.0 + i) for i in range(300)]
    labs = env.labels(bars)
    assert labs["d0299"] == env.classify([c for _, c in bars[:299]])
    assert env.label_on(bars, "d0299") == labs["d0299"]
    assert labs["d0010"] is None


def test_counts_show_sessions_and_trades_per_label():
    labs = {"a": "uptrend", "b": "uptrend", "c": "downtrend", "d": None}
    out = env.counts(labs, ["a", "a", "c", "d"])
    assert out["uptrend"] == {"sessions": 2, "trades": 2}
    assert out["downtrend"] == {"sessions": 1, "trades": 1}
    assert out["unlabelled"] == {"sessions": 1, "trades": 1}
    assert out["sideways"] == {"sessions": 0, "trades": 0}
