"""The idea search (#1294): a planted losing condition is found and survives, noise is not, the held-out
part's P&L is never read by the search, and features use only what was known before entry."""

import random
from datetime import date, timedelta

import pytest

from cherrypick.backtest import ideasearch as ids

DRAWS = 300


def _days(n, start=date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _world(n=480, seed=5, planted="smooth"):
    """`n` sessions of bars, VIX and one trade a session, every session earning noise around +20 but
    the planted ones. "smooth": sessions whose prior VIX close is 25-30 lose 100 and 30 or over lose
    160. "cliff": only 30 or over lose, 150. None: noise only."""
    rng = random.Random(seed)
    days = _days(n + 1)
    bars, px = [], 100.0
    for d in days:
        o = px * (1 + rng.gauss(0, 0.004))
        px = o * (1 + rng.gauss(0, 0.008))
        bars.append({"session": d, "open": round(o, 4), "close": round(px, 4)})
    vix = {d: rng.choice([13, 17, 22, 27, 33]) + rng.random() for d in days}
    trades = []
    for k, d in enumerate(days[1:], start=1):
        net, v = rng.gauss(20, 60), vix[days[k - 1]]
        if planted == "smooth" and v >= 25:
            net = rng.gauss(-160 if v >= 30 else -100, 60)
        elif planted == "cliff" and v >= 30:
            net = rng.gauss(-150, 60)
        trades.append(
            {
                "arm": "a",
                "session": d,
                "net": {"verdict": round(net, 2)},
                "fees": 4.0,
                "slippage": {"verdict": 6.0},
            }
        )
    return days[1:], bars, vix, trades


def _search(planted="smooth", **kw):
    sessions, bars, vix, trades = _world(planted=planted)
    feats = ids.features(sessions, bars, vix)
    split = ids.split_date(sessions[0], sessions[-1])
    return ids.search(trades, feats, split, draws=DRAWS, **kw), trades, feats, split


def test_an_idea_reads_as_its_conditions_holding_at_once():
    assert ids.idea_words([("vix", "above", 3)]) == "skip when VIX at the prior close is 25 and over"
    assert ids.idea_words([("event:FOMC", "bin", 1)]) == "skip on FOMC days"
    pair = [("vix", "bin", 4), ("weekday", "bin", 0)]
    assert ids.idea_words(pair) == "skip on Mondays when VIX at the prior close is 30 and over"
    assert ids.idea_words([("weekday", "bin", 0), ("event:OPEX", "bin", 1)]) == (
        "skip on Mondays that are also OPEX days"
    )
    assert ids.idea_words([("trend", "bin", 0), ("rsi", "below", 2)]) == (
        "skip when the prior close is below its 200-day average and RSI(14) at the prior close is under 50"
    )


def test_split_date_is_two_thirds_of_the_span():
    assert ids.split_date("2024-01-01", "2024-01-31") == "2024-01-21"


def test_features_read_only_what_was_known_before_entry():
    sessions, bars, vix, _ = _world(n=260)
    s = sessions[230]
    before = ids.features([s], bars, vix)[s]
    i = next(k for k, b in enumerate(bars) if b["session"] == s)
    moved = [dict(b) for b in bars]
    moved[i]["close"] *= 1.5  # the session's own close is not known at its entry
    vix2 = {**vix, s: 99.0}  # nor is its own VIX close
    assert ids.features([s], moved, vix2)[s] == before
    moved[i]["open"] *= 1.05  # its open is: the gap moves
    assert ids.features([s], moved, vix)[s]["gap"] != before["gap"]
    assert before["vix"] == ids._band(vix[bars[i - 1]["session"]], ids.DIMS["vix"][1])


def test_search_finds_the_planted_condition_and_it_survives():
    out, *_ = _search()
    assert out["tried"] > 50
    top = out["rows"][0]
    assert top["survives"], top["gates"]
    assert top["id"] == "vix:above:3"  # VIX 25 and over: the whole losing band
    assert top["skipped_ci"][1] < 0
    assert 0 < top["uplift_shrunk"] < top["uplift"]
    assert top["held_out"]["testable"]


def test_a_lone_peak_is_rejected_and_a_tail_must_earn_its_widest_bin():
    out, *_ = _search(planted="cliff")
    # Only the top VIX bin lost; its neighbour made money, so the bin is a lone peak, not a plateau.
    row = next(r for r in out["rows"] if r["id"] == "vix:bin:4")
    assert row["gates"]["beats_menu"] and row["gates"]["loses"]
    assert row["plateau"]["verdict"] == "lone_peak" and not row["survives"]
    # VIX 25 and over still loses on the whole, but the 25-30 bin it adds made money.
    wider = next(r for r in out["rows"] if r["id"] == "vix:above:3")
    assert wider["gates"]["loses"] and wider["gates"]["beats_menu"]
    assert wider["adds"]["adds"] is False and not wider["survives"]


def test_search_over_noise_hands_back_nothing():
    out, *_ = _search(planted=None)
    assert out["survivors"] == 0
    assert out["tried"] > 50


def test_search_never_reads_the_held_out_pnl():
    # Guard: the held-out part is spent once, by `holdout`. Moving its P&L must not move the search.
    out, trades, feats, split = _search()
    flipped = [
        {**t, "net": {"verdict": -t["net"]["verdict"] * 7}} if t["session"] >= split else t for t in trades
    ]
    again = ids.search(flipped, feats, split, draws=DRAWS)
    assert again == out


def test_holdout_tests_one_idea_on_the_later_sessions():
    out, trades, feats, split = _search()
    read = ids.holdout(trades, feats, split, out["rows"][0]["id"])
    assert read["verdict"] == "passed"
    assert read["skipped"] >= ids.HELD_MIN
    thin = ids.holdout(trades, feats, split, "event:FOMC:bin:1")
    assert thin["verdict"] == "no_verdict"


def test_a_pair_must_add_to_its_first_condition():
    _, trades, feats, split = _search()
    sessions = sorted(feats)
    nets = ids.session_nets(trades)
    design = [s for s in sessions if s < split]
    read = ids.adds([("vix", "bin", 4), ("weekday", "bin", 0)], feats, design, nets)
    # Skipping only high-VIX Mondays leaves the other high-VIX days, which lost: no addition.
    assert read["adds"] is False


def test_declaration_drafts_a_register_row_with_the_split_fixed():
    out, *_ = _search()
    result = {**out, "automation": "iron-fly-1000", "sessions": ["2023-01-03", "2024-11-01"]}
    row = ids.declaration(result, out["rows"][0]["id"], result_path="{home}/backtest/automations/x.json")
    assert row["status"] == "declared" and row["id"] is None and row["mechanism"] is None
    assert out["split"] in row["sample"] and out["split"] in row["rule"]
    assert row["idea_search"]["split"] == out["split"]
    with pytest.raises(ValueError):
        ids.declaration(result, "vix:bin:9", result_path="x")
