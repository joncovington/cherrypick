from datetime import date

import pytest
from cherrypick.core import fees
from conftest import black_chain, load_script

from cherrypick.weeklyput import clock, engine

ENTRY, EXPIRY = date(2026, 11, 6), date(2026, 11, 13)
YEARS = clock.years_to(ENTRY, EXPIRY)


def spx_chain(fwd=6800.0, vol=0.16, lo=6000, hi=7000):
    return black_chain(fwd, vol, YEARS, range(lo, hi + 1, 5))


def test_forward_is_parity():
    assert engine.forward(spx_chain(fwd=6800.0)) == pytest.approx(6800.0, abs=0.06)


def test_pick_is_the_nearest_ten_delta_put():
    chain = spx_chain()
    fwd = engine.forward(chain)
    pick = engine.pick_put(chain, fwd, YEARS)
    assert pick["ok"]
    puts = engine.priced_puts(chain, fwd, YEARS)
    best = min(puts, key=lambda c: abs(abs(c["delta"]) - 0.10))
    assert pick["leg"]["strike"] == best["strike"]
    assert abs(pick["leg"]["delta"]) == pytest.approx(0.10, abs=0.01)
    assert set(pick["neighbours"]) == {"put8", "put12"}


def test_an_unbracketed_ten_delta_is_refused():
    """A window that stops short of the 10-delta strike must not sell the nearest put it has."""
    chain = [row for row in spx_chain() if row[0] >= 6650]
    fwd = engine.forward(chain)
    assert engine.pick_put(chain, fwd, YEARS) == {
        "ok": False,
        "reason": "delta_not_bracketed",
        "nearest": engine.nearest(engine.priced_puts(chain, fwd, YEARS), 0.10),
    }


def _entered(chain, fee_symbol="SPX"):
    plan = engine.plan_entry(chain=chain, years=YEARS, fee_symbol=fee_symbol)
    assert plan["ok"]
    return {**plan, "entry_fees": plan["entry_fees"], "entry_slippage": plan["entry_slippage"]}


@pytest.mark.parametrize(
    "style,price", [(engine.CASH, 6900.0), (engine.CASH, 6000.0), (engine.PHYSICAL, 6000.0)]
)
def test_rows_add_up(style, price):
    pos = _entered(spx_chain())
    res = engine.settle(pos, price, style)
    assert res["gross_pnl"] == pytest.approx(pos["entry_value"] + res["exit_value"])
    assert res["net_pnl"] == pytest.approx(
        res["gross_pnl"] - res["fees"] - res["settlement"] - res["slippage"], abs=1e-3
    )
    assert res["net_bp"] == pytest.approx(res["net_pnl"] / pos["notional"] * 1e4, abs=1e-3)


def test_settlement_costs_by_style():
    pos = _entered(spx_chain())
    otm = engine.settle(pos, 6900.0, engine.CASH)
    assert otm["exit_reason"] == "expired" and otm["exit_value"] == 0 and otm["settlement"] == 0
    cash = engine.settle(pos, 6000.0, engine.CASH)
    assert cash["exit_reason"] == "settled"
    assert cash["settlement"] == pytest.approx(fees.ic_expire_fee(1))
    phys = engine.settle(pos, 6000.0, engine.PHYSICAL)
    assert phys["exit_reason"] == "assigned"
    assert phys["settlement"] == pytest.approx(
        fees.assignment_round_trip_fee(100, pos["strike"], 6000.0, direction="long", ndigits=4)
    )
    with pytest.raises(ValueError):
        engine.settle(pos, 6000.0, "neither")


@pytest.mark.parametrize(
    "symbol,fee_symbol,f_exp", [("SPXW", "SPX", 6900.0), ("SPXW", "SPX", 6500.0), ("SPY", "SPY", 650.0)]
)
def test_matches_item57_unhedged_trade(symbol, fee_symbol, f_exp):
    """The module's pick, premium, costs and settlement are item 57's unhedged trade, to the cent,
    on the same quotes (item 57 settles at the expiry forward; the module at the posted close)."""
    i57 = load_script("index_put_weekly_item57")
    if symbol == "SPY":
        chain, exp_chain = (
            black_chain(680.0, 0.16, YEARS, range(600, 701)),
            black_chain(f_exp, 0.16, 0.5 / 365, range(600, 701)),
        )
    else:
        chain, exp_chain = spx_chain(), black_chain(f_exp, 0.16, 0.5 / 365, range(6000, 7001, 5))
    sessions = {ENTRY: chain, EXPIRY: exp_chain}
    fwds = {d: i57.i46.forward(c) for d, c in sessions.items()}
    theirs = i57.trade_pair(symbol, sessions, fwds, EXPIRY, "put10")["unhedged"]

    plan = engine.plan_entry(chain=chain, years=YEARS, fee_symbol=fee_symbol)
    style = engine.CASH if symbol == "SPXW" else engine.PHYSICAL
    ours = engine.settle(plan, fwds[EXPIRY], style)
    assert [[plan["strike"], "P"]] == theirs["strikes"]
    assert plan["entry_value"] == pytest.approx(theirs["premium"], abs=0.01)
    assert ours["fees"] + ours["slippage"] + ours["settlement"] == pytest.approx(theirs["costs"], abs=0.01)
    assert ours["net_pnl"] == pytest.approx(theirs["net"], abs=0.01)
    assert ours["net_bp"] == pytest.approx(theirs["net_bp"], abs=0.01)
