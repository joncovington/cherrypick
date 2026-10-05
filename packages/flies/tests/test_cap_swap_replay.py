"""scripts/flies_cap_swap_replay.py: at a live cap refusal, free the stalest spread and value the swap.

One fixture session: two open live short verticals when the cap refuses an entry, the paper twin that
took that entry, a completion quote and a mark for the stale spread. The expected swap values are
worked by hand below; the fee schedule is read from the module rather than copied, because it is an
input to the arithmetic, not the thing under test.
"""

import importlib.util
from pathlib import Path

import pytest
from cherrypick.core import fees as core_fees

from cherrypick.flies import db as dbmod
from cherrypick.flies import fly

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_cap_swap_replay.py"
DAY = "2026-09-29"
PRINT = 7670.84
STALE = "live-control-7685-stale"
NEWER = "live-control-7700-newer"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("flies_cap_swap_replay", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _position(conn, **row):
    base = {
        "book_id": f"{row['position_id']}-book",
        "trade_date": DAY,
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "wing_width": 5.0,
        "quantity": 1,
        "status": "settled",
        "settlement_price": PRINT,
    }
    row = {**base, **row}
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO fly_positions ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )
    conn.commit()


@pytest.fixture
def ledgers(managed_home):
    root = managed_home / "data" / "flies"
    root.mkdir(parents=True)
    live = dbmod.connect(str(root / "live_trades.db"))
    paper = dbmod.connect(str(root / "paper_trades.db"))

    # The stale spread: a 7685/7680 put credit spread that settled through both strikes.
    # Held: (2.35 - 5) x 100 - 10.94 = -275.94, of which 7.50 was paid at the print.
    _position(
        live,
        position_id=STALE,
        kind="short_vertical",
        side="put",
        center=7685.0,
        net=2.35,
        credit=2.35,
        fees=10.94,
        settlement_fees=7.5,
        slippage_dollars=5.0,
        entry_time=f"{DAY}T10:17:31-04:00",
        pnl=-275.94,
    )
    # A newer spread, also open at the refusal, that expired worthless: +250 - 3.44 = 246.56.
    _position(
        live,
        position_id=NEWER,
        kind="short_vertical",
        side="call",
        center=7700.0,
        net=2.5,
        credit=2.5,
        fees=3.44,
        settlement_fees=0.0,
        slippage_dollars=5.0,
        entry_time=f"{DAY}T10:40:00-04:00",
        pnl=246.56,
    )
    live.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, last_seen, "
        "occurrences, center_first, center_last, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            DAY,
            "control",
            "SPX",
            "entry",
            "max_open_margin_reached",
            0,
            f"{DAY}T11:18:52-04:00",
            f"{DAY}T11:18:52-04:00",
            1,
            7665.0,
            7665.0,
            "open+proposed $1074.08 > cap $1000.00",
        ),
    )
    # The completing 7690/7685 put spread, 52s before the refusal: natural 20.40 - 15.60 = 4.80,
    # mid 20.20 - 15.75 = 4.45, resting limit 2.35.
    live.execute(
        "INSERT INTO fly_order_path (observed_at, trade_date, position_id, order_id, leg, source, limit_price, "
        "buy_bid, buy_ask, sell_bid, sell_ask, spot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"{DAY}T11:18:00-04:00",
            DAY,
            STALE,
            f"{STALE}-completion",
            "completion",
            "live",
            2.35,
            20.0,
            20.4,
            15.6,
            15.9,
            7671.0,
        ),
    )
    live.execute(
        "INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, kind, structure_mid, mark_pnl, spot, "
        "open_margin, resting_limit) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"{DAY}T11:18:10-04:00", DAY, STALE, "short_vertical", -4.6, -228.44, 7671.0, 1000.0, 2.35),
    )
    live.commit()
    # Paper control, with no cap, took the entry 33s before live refused it.
    _position(
        paper,
        position_id="FLY-control-SPX-twin",
        kind="fly",
        side="put",
        center=7665.0,
        net=0.3,
        credit=2.25,
        debit=1.95,
        fees=6.89,
        entry_time=f"{DAY}T11:18:19-04:00",
        pnl=3.11,
    )
    return live, paper


def _run(script, live, paper) -> dict:
    out = script.replay(live, paper, "control", DAY, DAY)
    assert out["refusal_runs"] == 1
    return out


def test_the_stale_spread_is_freed_and_both_options_are_valued_by_hand(script, ledgers):
    live, paper = ledgers
    out = _run(script, live, paper)
    run = out["runs"][0]
    assert run["status"] == "valued"
    assert run["position_id"] == STALE

    open_fee = fly.vertical_open_fee("SPX")
    entry_fees = 10.94 - 7.5
    # The fly's three strikes are all ITM at the print (15.00 modelled) where the spread's two were
    # (10.00): the completion adds one event, $5.00, on top of the 7.50 really paid.
    settle = 7.5 + 5.0
    # C1: net 2.35 - 4.80 = -2.45 a share, the fly pays 0 (14.16 from the centre).
    c1 = -245.0 - (entry_fees + open_fee + settle)
    assert run["c1_natural_debit"] == 4.8
    assert run["c1"] == round(3.11 + c1 - (-275.94), 2)
    c1_mid = -210.0 - (entry_fees + open_fee + settle)
    assert run["c1_at_mid"] == round(3.11 + c1_mid - (-275.94), 2)
    c1_bound = 0.0 - (entry_fees + open_fee + settle)
    assert run["c1_bound"] == round(3.11 + c1_bound - (-275.94), 2)

    # C2: bought back at 4.60, the entry's own 5.00 of slippage and a two-leg close fee.
    close_fee = core_fees.ic_close_fee("SPX", 1, legs=2, sell_legs=1, ndigits=4)
    c2 = (2.35 - 4.6) * 100 - entry_fees - close_fee - 5.0
    assert run["c2"] == round(3.11 + c2 - (-275.94), 2)

    assert out["c1"]["net"] == run["c1"]
    assert out["c2"]["per_session"] == {DAY: run["c2"]}
    assert out["c2"]["worst_session"] == {"trade_date": DAY, "net": run["c2"]}
    assert out["unmeasured"] == {"c1": 0, "c2": 0}


def test_a_completed_fly_replayed_at_its_own_debit_returns_its_recorded_pnl(script):
    # A real live row (2026-09-28): the fee unwinding has to put back exactly what was taken.
    row = {
        "kind": "fly",
        "side": "put",
        "center": 7695.0,
        "wing_width": 5.0,
        "quantity": 1,
        "symbol": "SPX",
        "net": 0.25,
        "credit": 2.2,
        "debit": 1.95,
        "fees": 19.38,
        "settlement_fees": 12.5,
        "settlement_price": 7683.69,
        "pnl": 5.62,
    }
    assert round(script.force_completed_net(row, row["debit"]), 2) == row["pnl"]


def test_without_a_paper_twin_the_run_is_unmatched_and_never_valued(script, ledgers):
    live, paper = ledgers
    paper.execute("DELETE FROM fly_positions")
    paper.commit()
    out = _run(script, live, paper)
    assert out["runs"][0]["status"] == "unmatched"
    assert out["status"] == {"unmatched": 1}
    for option in ("c1", "c1_bound", "c2"):
        assert out[option]["valued_runs"] == 0
        assert out[option]["net"] is None


def test_without_a_completion_quote_c1_is_unmeasured_not_zero(script, ledgers):
    live, paper = ledgers
    live.execute("DELETE FROM fly_order_path")
    live.commit()
    out = _run(script, live, paper)
    run = out["runs"][0]
    assert run["status"] == "valued"
    assert run["c1"] is None and run["c1_unmeasured"]
    assert out["unmeasured"]["c1"] == 1
    assert out["c1"]["net"] is None and out["c1"]["valued_runs"] == 0
    assert run["c2"] is not None  # the mark still values the abort


def test_a_quote_older_than_the_tolerance_is_unmeasured_not_carried_forward(script, ledgers):
    live, paper = ledgers
    live.execute("UPDATE fly_order_path SET observed_at = ?", (f"{DAY}T11:15:00-04:00",))
    live.commit()
    run = _run(script, live, paper)["runs"][0]
    assert run["c1"] is None


def test_a_quoteless_trail_row_is_unmeasured_but_still_bounds_c1(script, ledgers):
    # What the backfilled history looks like: the spot path and the resting limit, no quotes.
    live, paper = ledgers
    live.execute("UPDATE fly_order_path SET buy_bid = NULL, buy_ask = NULL, sell_bid = NULL, sell_ask = NULL")
    live.commit()
    run = _run(script, live, paper)["runs"][0]
    assert run["c1"] is None
    assert run["c1_bound"] is not None


def test_the_freed_spread_is_the_stalest_not_the_newest(script, ledgers):
    """Point the pick at the newest open spread and the hand-computed held net no longer appears."""
    live, paper = ledgers
    run = _run(script, live, paper)["runs"][0]
    assert run["position_id"] == STALE
    assert run["held_net"] == -275.94
