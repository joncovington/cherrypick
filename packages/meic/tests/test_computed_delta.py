"""MEIC's strike selection by a delta computed from the chain's own mids (2026-10-27).

The data feed's delta lags its quotes by about a minute, so "nearest 0.16 delta" on it picks a stale
strike and no history source can reproduce the pick. `delta_source: "computed"` picks by
`core.chaindelta` instead; "stream" (the default) is the behaviour before this date.
"""

from __future__ import annotations

import pytest
from cherrypick.core import bs

from cherrypick.meic import paper_loop

SPOT, VOL = 6000.0, 0.15
YEARS = 3 * 3600 / (365.0 * 86400.0)


def chain(stale_spot: float | None = None):
    """Rows priced at SPOT; the feed's `delta` computed at `stale_spot` -- the market a minute ago."""
    rows = []
    for k in range(5850, 6155, 5):
        for right in ("C", "P"):
            px = bs.price(SPOT, float(k), YEARS, VOL, right, 0.0)
            feed = bs.delta(stale_spot or SPOT, float(k), YEARS, VOL, right, 0.0)
            rows.append(
                {
                    "strike_price": float(k),
                    "option_type": right,
                    "streamer_symbol": f".SPXW{right}{k}",
                    "bid": round(max(0.05, px - 0.05), 2),
                    "ask": round(px + 0.05, 2),
                    "delta": feed,
                }
            )
    return rows


def shorts(cands):
    c = cands[0]
    return c["short_put"]["strike"], c["short_call"]["strike"]


def test_stream_stays_the_default_and_picks_by_the_feeds_delta():
    rows = chain(stale_spot=SPOT - 15)  # the feed still prices the market 15 points lower
    default = paper_loop.candidates_from_chain(rows, SPOT, [10], [0.16], 0.16)
    stream = paper_loop.candidates_from_chain(
        rows, SPOT, [10], [0.16], 0.16, delta_source="stream", years=YEARS
    )
    assert shorts(default[0]) == shorts(stream[0])


def test_computed_picks_the_strike_the_current_quotes_give_not_the_stale_feeds():
    rows = chain(stale_spot=SPOT - 15)
    stale = paper_loop.candidates_from_chain(rows, SPOT, [10], [0.16], 0.16)
    fresh = paper_loop.candidates_from_chain(
        rows, SPOT, [10], [0.16], 0.16, delta_source="computed", years=YEARS
    )
    truth = paper_loop.candidates_from_chain(
        chain(), SPOT, [10], [0.16], 0.16
    )  # feed current: the right answer
    assert shorts(fresh[0]) == shorts(truth[0])
    assert shorts(stale[0]) != shorts(truth[0])  # the stale feed would have picked another strike
    leg = fresh[0][0]["short_call"]
    assert leg["delta_stream"] is not None and leg["delta"] != leg["delta_stream"]


def test_a_chain_that_cannot_be_computed_refuses_entries_but_still_marks():
    cands, quotes, err = paper_loop.candidates_from_chain(
        [r for r in chain() if r["option_type"] == "C"],
        SPOT,
        [10],
        [0.16],
        0.16,
        delta_source="computed",
        years=YEARS,
    )
    assert cands == [] and err == "no call/put quotes" and quotes  # no puts at all: the plain refusal
    cands, quotes, err = paper_loop.candidates_from_chain(
        chain(), SPOT, [10], [0.16], 0.16, delta_source="computed", years=None
    )
    assert cands == [] and "time to the close" in err and quotes


def test_the_source_in_force_is_the_streams_before_the_declared_boundary():
    cfg = {"delta_source": "computed", "delta_source_since": "2026-10-27"}
    assert paper_loop.delta_source_for(cfg, "2026-10-26") == "stream"
    assert paper_loop.delta_source_for(cfg, "2026-10-27") == "computed"
    assert paper_loop.delta_source_for({"delta_source": "computed"}, "2026-01-02") == "computed"
    assert paper_loop.delta_source_for({}, "2026-10-27") == "stream"


def test_a_validation_replays_a_session_on_the_delta_it_ran_with(monkeypatch):
    """A history run evaluates today's rule; the check reproduces the session, so before the boundary it
    must pick on the feed's delta or it would be checking a rule paper never ran."""
    from cherrypick.meic import replay, replay_check

    seen = {}

    class Stop(Exception):
        pass

    def fake_day(day, cfg, profiles, *, history_db=None, delta_source=None):
        seen["source"] = delta_source
        raise Stop

    monkeypatch.setattr(
        replay,
        "replay_profiles",
        lambda arms: ({"delta_source": "computed", "delta_source_since": "2026-10-27"}, {}),
    )
    monkeypatch.setattr(replay, "ReplayDay", fake_day)
    for day, expected in (("2026-10-26", "stream"), ("2026-10-28", "computed")):
        with pytest.raises(Stop):
            replay_check.decision_check(day, ["control"])
        assert seen["source"] == expected, day
    # A chosen source replaces the session's own: backtest's validation runs both of its sources on
    # "computed", because the feed's lag is in the recording and not in a vendor's history.
    with pytest.raises(Stop):
        replay_check.decision_check("2026-10-26", ["control"], delta_source="computed")
    assert seen["source"] == "computed"
    with pytest.raises(ValueError, match="delta_source"):
        replay_check.decision_check("2026-10-26", ["control"], delta_source="vendor")


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError, match="delta_source"):
        paper_loop.candidates_from_chain(chain(), SPOT, [10], [0.16], 0.16, delta_source="vendor")
