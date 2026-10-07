"""completion_escalation: a raised completion limit replayed on the live order path's own quotes."""

import pytest

from cherrypick.flies import completion_escalation as ce
from cherrypick.flies import fly

# A put short vertical sold 7500/7495; it completes by buying the 7505/7500 put spread as spot rises.
POS = {
    "symbol": "SPX",
    "side": fly.PUT,
    "center": 7500.0,
    "wing_width": 5.0,
    "quantity": 1,
    "credit": 2.40,
    "settlement_price": 7503.0,
}
PLACED = "2026-10-06T10:00:00-04:00"


def _obs(minute: int, mid: float, *, spot: float = 7500.0, limit: float = 2.15) -> dict:
    """One quoted path row whose completion mid is `mid` (buy leg mid minus sell leg mid)."""
    return {
        "observed_at": f"2026-10-06T{10 + minute // 60:02d}:{minute % 60:02d}:00-04:00",
        "limit_price": limit,
        "buy_bid": mid + 9.95,
        "buy_ask": mid + 10.05,
        "sell_bid": 9.95,
        "sell_ask": 10.05,
        "spot": spot,
    }


def _policy(name):
    return next(p for p in ce.policies() if p["name"] == name)


BASE = {"name": "baseline", "family": "baseline", "cap": 0.0, "steps": []}


def test_baseline_fills_once_mid_is_within_the_slack_and_pays_the_limit():
    path = [_obs(0, 2.40), _obs(5, 2.21), _obs(10, 2.20), _obs(15, 2.00)]
    out = ce.simulate(POS, path, BASE, placed_at=PLACED, live_filled_at=None)
    assert out == {
        "completed": True,
        "at": path[2]["observed_at"],
        "paid": 2.15,
        "raised": 0.0,
        "via": "rule",
    }


def test_a_time_trigger_raises_only_after_it_fires_and_rescues_a_strand():
    """Mid sits at 2.45 all day: 0.30 over the limit. The baseline strands; a ladder whose +0.25 cap
    fires at 90 minutes fills there and pays 2.40, not before."""
    path = [_obs(m, 2.45) for m in range(0, 180, 15)]
    assert not ce.simulate(POS, path, BASE, placed_at=PLACED, live_filled_at=None)["completed"]

    out = ce.simulate(
        POS, path, _policy("ladder +0.10@45m, +0.25@90m"), placed_at=PLACED, live_filled_at=None
    )
    assert out["completed"] and out["paid"] == 2.40 and out["raised"] == 0.25
    assert out["at"] == "2026-10-06T11:30:00-04:00"


def test_away_is_against_the_completing_direction_and_stays_latched():
    """A put spread completes as spot rises, so AWAY is spot falling. A fall to 1.0 width below the
    centre latches the raise; it holds after spot comes back."""
    pol = _policy("away 1.0w +0.50")
    up = [_obs(0, 2.60, spot=7506.0), _obs(5, 2.60, spot=7506.0)]
    assert not ce.simulate(POS, up, pol, placed_at=PLACED, live_filled_at=None)["completed"]

    path = [_obs(0, 2.80, spot=7495.0), _obs(5, 2.68, spot=7500.0)]
    out = ce.simulate(POS, path, pol, placed_at=PLACED, live_filled_at=None)
    assert out["completed"] and out["paid"] == 2.65 and out["at"] == path[1]["observed_at"]


def test_a_live_fill_the_rule_never_touched_fills_at_the_live_moment():
    path = [_obs(0, 2.40), _obs(5, 2.30)]
    out = ce.simulate(POS, path, BASE, placed_at=PLACED, live_filled_at="2026-10-06T10:06:00-04:00")
    assert out == {
        "completed": True,
        "at": "2026-10-06T10:06:00-04:00",
        "paid": 2.15,
        "raised": 0.0,
        "via": "live_fill",
    }


def test_a_raise_tracks_the_limit_live_had_at_each_observation():
    """Live repriced from 2.15 to 2.10 mid-path: the raise sits on top of whichever was working."""
    path = [_obs(0, 2.45, limit=2.15), _obs(5, 2.40, limit=2.10)]
    out = ce.simulate(POS, path, _policy("flat +0.25"), placed_at=PLACED, live_filled_at=None)
    assert out["paid"] == 2.40 and out["at"] == path[0]["observed_at"]


def test_settle_prices_strand_and_fly_on_the_modelled_fee_stack():
    fee = fly.vertical_open_fee("SPX", 1)
    strand = ce.settle(POS, {"completed": False, "paid": None})
    expected = fly.position_pnl(
        {
            "kind": "short_vertical",
            "side": fly.PUT,
            "center": 7500.0,
            "wing_width": 5.0,
            "net": 2.40,
            "quantity": 1,
            "fees": fee,
        },
        7503.0,
    )
    assert strand["pnl"] == pytest.approx(round(expected, 2))

    # At +0.50 over a 2.15 limit the fly is paid 2.65 for a 2.40 credit: its floor is below zero.
    rescued = ce.settle(POS, {"completed": True, "paid": 2.65})
    assert rescued["floor"] < 0
    assert ce.settle(POS, {"completed": True, "paid": 2.15})["floor"] > rescued["floor"]


def test_the_grid_runs_every_trigger_at_both_caps():
    grid = ce.policies()
    assert grid[0]["name"] == "baseline"
    families = {}
    for p in grid[1:]:
        families.setdefault(p["family"], set()).add(p["cap"])
    assert families and all(caps == set(ce.CAPS) for caps in families.values())
    assert len(grid) == 1 + 6 * len(ce.CAPS)


def test_compare_counts_rescues_overpayment_and_the_session_delta():
    base = [
        {
            "trade_date": "d1",
            "outcome": {"completed": True, "paid": 2.15},
            "settled": {"pnl": 20.0, "floor": 3.0},
        },
        {
            "trade_date": "d1",
            "outcome": {"completed": False, "paid": None},
            "settled": {"pnl": -250.0, "floor": -270.0},
        },
    ]
    rows = [
        {
            "trade_date": "d1",
            "outcome": {"completed": True, "paid": 2.40},
            "settled": {"pnl": -5.0, "floor": -22.0},
        },
        {
            "trade_date": "d1",
            "outcome": {"completed": True, "paid": 2.40},
            "settled": {"pnl": 10.0, "floor": -22.0},
        },
    ]
    out = ce.compare(rows, {"name": "p", "family": "f", "cap": 0.25}, base)
    assert (out["rescued"], out["overpaid"], out["overpaid_dollars"]) == (1, 1, 25.0)
    assert out["negative_floors"] == 2
    assert out["delta"] == pytest.approx(235.0) and out["per_session_delta"] == {"d1": 235.0}


# --------------------------------------------------------------------------- freed margin
STRANDED = {**POS, "position_id": "S1", "entry_time": "2026-10-06T10:00:00-04:00"}
STRAND = {"completed": False, "at": None, "paid": None}
RESCUE = {"completed": True, "at": "2026-10-06T10:30:00-04:00", "paid": 2.65}
BLOCK = {
    "first_seen": "2026-10-06T11:00:00-04:00",
    "last_seen": "2026-10-06T11:10:00-04:00",
    "center": 7520.0,
    "would_be": 1050.0,
    "cap": 1000.0,
}
PAPER = {
    "center": 7520.0,
    "entry_time": "2026-10-06T11:02:00-04:00",
    "completed_at": None,
    "credit": 2.40,
    "wing_width": 5.0,
    "pnl": 18.11,
}


def test_exposure_follows_the_live_margin_rule_through_the_day():
    from cherrypick.flies import fill_model

    at = fill_model.parse_ts
    assert ce.exposure_at(STRANDED, STRAND, at("2026-10-06T09:59:00-04:00")) == 0.0
    vertical = ce.exposure_at(STRANDED, STRAND, at("2026-10-06T11:00:00-04:00"))
    assert vertical > 250  # width less credit, plus fees and the settlement reserve
    fly_after = ce.exposure_at(STRANDED, RESCUE, at("2026-10-06T11:00:00-04:00"))
    assert 40 < fly_after < 60  # the +0.50 fly's -$47 floor still counts
    assert ce.exposure_at(STRANDED, RESCUE, at("2026-10-06T10:15:00-04:00")) == vertical


def test_a_rescue_that_frees_enough_margin_lets_the_refused_entry_in_and_paper_scores_it():
    day = [{"position": STRANDED, "base": STRAND, "policy": RESCUE}]
    out = ce.admit_blocked([BLOCK], day, [PAPER], taken_centers=set())
    assert len(out) == 1 and out[0]["at"] == BLOCK["first_seen"] and out[0]["paper_pnl"] == 18.11
    assert out[0]["would_be"] <= 1000.0


def test_the_baseline_frees_nothing_and_admits_nothing():
    day = [{"position": STRANDED, "base": STRAND, "policy": STRAND}]
    assert ce.admit_blocked([BLOCK], day, [PAPER], taken_centers=set()) == []


def test_a_centre_live_traded_is_skipped_and_a_missing_paper_entry_is_unscored():
    day = [{"position": STRANDED, "base": STRAND, "policy": RESCUE}]
    assert ce.admit_blocked([BLOCK], day, [PAPER], taken_centers={7520.0}) == []
    far = {**PAPER, "entry_time": "2026-10-06T12:30:00-04:00"}
    out = ce.admit_blocked([BLOCK], day, [far], taken_centers=set())
    assert len(out) == 1 and out[0]["paper_pnl"] is None


def test_an_admitted_entry_holds_its_own_margin_against_later_refusals():
    day = [{"position": STRANDED, "base": STRAND, "policy": RESCUE}]
    later = {
        **BLOCK,
        "center": 7525.0,
        "first_seen": "2026-10-06T11:20:00-04:00",
        "last_seen": "2026-10-06T11:20:00-04:00",
    }
    out = ce.admit_blocked([BLOCK, later], day, [PAPER], taken_centers=set())
    assert [a["center"] for a in out] == [7520.0]
