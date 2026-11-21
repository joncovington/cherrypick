"""Item 101's script (wheel_cycle_item101.py): invented chains and trades only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs, fees
from cherrypick.core import calendar as cal
from cherrypick.core import robustness as core_robustness

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "wheel_cycle_item101", Path(__file__).resolve().parents[1] / "scripts" / "wheel_cycle_item101.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


def test_the_declaration_is_what_the_plan_states():
    # A change to any of these is a change to the declared test: it needs a logged correction.
    assert m.RULE["splits"] == ["2023-10-01"] and m.RULE["delta"] is False and m.RULE["t_bar"] == 2.4
    assert (m.RULE["min_n"], m.RULE["holdout_min_n"]) == (150, 52) and m.RULE["tail"]["seed"] == 101
    assert m.HALF_RULE == {"time_split": "2020-06-01", "name_halves": True}
    assert m.ARMS == ("vendor", "vendor-stop", "ours")
    arms = m.SPEC["arms"]
    assert (arms["vendor"]["delta"], arms["vendor"]["dte"], arms["vendor"]["target"]) == (0.40, (21, 45), 30)
    assert arms["vendor-stop"]["stop"] == 0.15 and arms["vendor-stop"]["delta"] == 0.40
    assert (arms["ours"]["delta"], arms["ours"]["dte"], arms["ours"]["manage"]) == (0.16, (45, 60), "tp50")
    assert m.SPEC["call"] == {"band": (30, 45), "reach": (15, 66), "min_bid_pct_of_spot": 0.01}
    assert m.SPEC["fill_frac_of_spread"] == 0.25 and m.SPEC["atm_spread_bar"] == 0.075
    assert (m.SPEC["entry_start"], m.SPEC["entry_end"], m.SPEC["run_end"]) == (
        "2016-03-01",
        "2026-10-16",
        "2026-10-28",
    )


# ------------------------------------------------------------------------------------------------
# The pure rules.


def test_the_put_expiry_is_the_monthly_in_its_band_nearest_the_target():
    # 2025-02-21 is 38 days after 2025-01-14: inside 21-45, and the only monthly there.
    assert m.pick_expiry(date(2025, 1, 14), (21, 45), 30) == date(2025, 2, 21)
    # Nothing in 21-45 days from 2024-12-27: the January monthly is 21 days out... and February's 56.
    assert m.pick_expiry(date(2025, 1, 2), (21, 45), 30) is None
    assert m.pick_expiry(date(2025, 1, 2), (45, 60), None) == date(2025, 2, 21)
    # Both of two monthlies in a wide band: the nearer the target, the earlier on a tie.
    assert m.pick_expiry(date(2025, 1, 21), (10, 70), 50) == date(2025, 3, 21)
    assert m.pick_expiry(date(2025, 1, 21), (10, 70), 30) == date(2025, 2, 21)
    assert m.pick_expiry(date(2025, 1, 21), (10, 70), 45) == date(2025, 2, 21)  # 14 off each: the earlier


def test_the_call_expiry_is_the_monthly_nearest_the_band_never_none_in_reach():
    band, reach = (30, 45), (15, 66)
    assert m.pick_expiry_near_band(date(2025, 1, 21), band, reach) == date(2025, 2, 21)  # 31 days
    # Monthlies 12 and 47 days out cannot both be in reach 15+: the one at 47 (2 over the band).
    assert m.pick_expiry_near_band(date(2025, 3, 5), band, reach) == date(2025, 4, 17)
    # A tie between 10 under and 10 over takes the later.
    assert m.pick_expiry_near_band(date(2025, 4, 28), (30, 45), (15, 66)) == date(2025, 6, 20)
    assert m.pick_expiry_near_band(date(2025, 2, 20), (30, 45), (15, 20)) is None


def test_a_report_in_the_span_or_an_unknown_one_blocks_a_stock_and_never_a_fund():
    reports = [date(2025, 1, 29), date(2025, 4, 30)]
    assert m.earnings_state(reports, date(2025, 2, 1), date(2025, 3, 15)) == "clear"
    assert m.earnings_state(reports, date(2025, 4, 1), date(2025, 5, 15)) == "crosses"
    assert m.earnings_state([], date(2025, 2, 1), date(2025, 3, 15)) == "unknown"
    # Reports more than 120 days apart have one missing between them.
    assert (
        m.earnings_state([date(2024, 10, 1), date(2025, 4, 30)], date(2025, 1, 5), date(2025, 2, 5))
        == "unknown"
    )
    stock = _name({d: 100.0 for d in _sessions(date(2025, 2, 3), 5)}, fund=False, reports=reports)
    fund = _name({d: 100.0 for d in _sessions(date(2025, 2, 3), 5)}, fund=True, reports=[])
    assert stock.blocked(date(2025, 4, 1), date(2025, 5, 15)) == "earnings"
    assert stock.blocked(date(2025, 2, 3), date(2025, 3, 15)) is None
    assert fund.blocked(date(2025, 4, 1), date(2025, 5, 15)) is None
    assert (
        _name({}, fund=False, reports=[]).blocked(date(2025, 2, 3), date(2025, 3, 15)) == "earnings_unknown"
    )


def test_a_split_inside_a_span_blocks_the_sale():
    nd = _name({d: 100.0 for d in _sessions(date(2025, 2, 3), 5)}, splits={date(2025, 3, 3): 2.0})
    assert nd.blocked(date(2025, 2, 3), date(2025, 3, 21)) == "split"
    assert nd.blocked(date(2025, 3, 3), date(2025, 3, 21)) is None


def test_the_fill_concedes_a_quarter_of_the_spread_capped_at_a_share_of_the_mid():
    q = {"bid": 1.00, "ask": 1.08}
    assert m.slip(q) == pytest.approx(0.02) and m.sell_price(q) == pytest.approx(1.02)
    assert m.buy_price(q) == pytest.approx(1.06)
    wide = {"bid": 0.01, "ask": 0.31}  # a quarter of 0.30 is 0.075, past 15% of the 0.16 mid
    assert m.slip(wide) == pytest.approx(0.15 * 0.16)


def test_the_cost_strike_is_the_lowest_listed_strike_at_or_above_cost_with_a_quote():
    ch = _chain(date(2025, 2, 3), date(2025, 3, 21), 100.0)
    assert m.cost_strike(ch, 103.2)["strike"] == 104.0
    assert m.cost_strike(ch, 104.0)["strike"] == 104.0
    assert m.cost_strike(ch, 999.0) is None
    # A strike with no bid is passed over.
    ch.by["C"][104.0] = {"strike": 104.0, "right": "C", "bid": 0.0, "ask": 0.1}
    assert m.cost_strike(ch, 103.2)["strike"] == 105.0


def test_the_point_in_time_spread_bar_reads_the_wider_of_the_call_and_put_at_the_money():
    ok = _chain(date(2025, 2, 3), date(2025, 3, 21), 100.0)
    assert m.atm_spread_ok(ok)
    wide = _chain(date(2025, 2, 3), date(2025, 3, 21), 100.0, spread=1.0)  # about 35% of the mid
    assert not m.atm_spread_ok(wide)


def test_the_spread_bar_on_a_leg():
    assert m.spread_ok({"bid": 1.0, "ask": 1.05}) and not m.spread_ok({"bid": 1.0, "ask": 1.30})
    assert not m.spread_ok({"bid": 0.0, "ask": 0.05}) and not m.spread_ok(None)


# ------------------------------------------------------------------------------------------------
# The replay, on invented chains priced from a model.


def _sessions(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _chain(session: date, expiry: date, spot: float, vol: float = 0.25, spread: float = 0.04) -> Chain:
    years = max((expiry - session).days, 1) / 365.0
    rows = []
    for k in range(40, 200):
        for r in ("C", "P"):
            px = bs.price(spot, float(k), years, vol, r, 0.0)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(px - spread / 2, 0.0), px + spread / 2))
    return Chain(rows, session.isoformat(), expiry.isoformat())


def _name(closes: dict, *, fund: bool = True, reports=(), splits=None, dividends=None, symbol: str = "TST"):
    def chain_at(d: date, e: date):
        return _chain(d, e, closes[d]) if d in closes and d <= e else None

    return m.NameData(symbol, fund, closes, list(reports), splits or {}, dividends or {}, chain_at)


def _path(legs: list[tuple[date, float]], through: date) -> dict:
    """Closes on every session: each leg's price from its date until the next leg's."""
    out = {}
    for d in _sessions(legs[0][0], (through - legs[0][0]).days + 1):
        if d > through:
            break
        price = [p for s, p in legs if s <= d][-1]
        out[d] = price
    return out


def _arm(closes: dict, arm: str, **kw):
    nd = _name(closes, **kw)
    return nd, m.run_arm(nd, arm, lambda s: 0.04)


def _tiles(res: dict) -> None:
    rows = res["rows"]
    for a, b in zip(rows, rows[1:], strict=False):
        assert b["start"] == a["end"]
    for r in rows:
        assert r["diff"] == pytest.approx(r["wheel"] + r["interest"] - r["hold"])


def test_a_flat_market_expires_every_put_and_each_row_ends_on_an_expiry(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    closes = _path([(date(2025, 1, 2), 100.0)], date(2025, 6, 30))
    _, res = _arm(closes, "vendor")
    assert res["stats"].get("assigned", 0) == 0 and res["stats"]["expired_otm"] >= 3
    _tiles(res)
    assert {r["how"] for r in res["rows"]} <= {"put expired", "run end"}
    # A put's row starts at the previous expiry (the idle day is in it), and its wheel earns premium.
    assert all(r["wheel"] > 0 for r in res["rows"] if r["how"] == "put expired")


def test_an_assignment_waits_for_a_bid_then_sells_the_cost_strike_and_is_called_away(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    legs = [(date(2025, 1, 2), 100.0), (date(2025, 2, 10), 85.0), (date(2025, 4, 1), 112.0)]
    closes = _path(legs, date(2025, 6, 30))
    _, res = _arm(closes, "vendor")
    st = res["stats"]
    assert st["assigned"] >= 1 and st["wait_bid"] > 0  # a far-out call bids under 1% of the stock
    assert st["called_away"] >= 1 and res["assigns"][0]["called_away"] is True
    assert res["assigns"][0]["days"] > 30  # held through the weeks the call had no bid
    _tiles(res)
    called = [r for r in res["rows"] if r["how"] == "called away"][0]
    assert called["settlement"] == 2 * fees.ASSIGNMENT_FEE_PER_SETTLEMENT  # in on the put, out on the call
    assert called["entry"] < called["end"]


def test_the_call_is_never_sold_below_the_effective_cost(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    legs = [(date(2025, 1, 2), 100.0), (date(2025, 2, 10), 85.0), (date(2025, 3, 25), 112.0)]
    closes = _path(legs, date(2025, 6, 30))
    sold = []
    real = m.cost_strike

    def spy(chain, cost):
        q = real(chain, cost)
        sold.append((cost, q["strike"] if q else None))
        return q

    monkeypatch.setattr(m, "cost_strike", spy)
    _arm(closes, "vendor")
    assert sold and all(k is None or k >= cost for cost, k in sold)


def test_the_stop_arm_buys_the_put_back_on_a_fall_and_waits_for_the_exit_close(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    legs = [
        (date(2025, 1, 2), 100.0),
        (date(2025, 1, 21), 84.0),
        (date(2025, 1, 22), 78.0),
        (date(2025, 2, 25), 100.0),
    ]
    closes = _path(legs, date(2025, 6, 30))
    _, res = _arm(closes, "vendor-stop")
    st = res["stats"]
    assert st["stop"] == 1 and st["stopped_days"] > 0 and st.get("assigned", 0) == 0
    stopped = [r for r in res["rows"] if r["how"] == "stop"][0]
    nxt = res["rows"][res["rows"].index(stopped) + 1]
    assert nxt["entry"] >= "2025-02-25"  # re-entered only once the close was back at the exit close
    _tiles(res)
    # The plain vendor arm, on the same path, holds the put to expiry and is assigned.
    _, held = _arm(closes, "vendor")
    assert held["stats"].get("stop", 0) == 0 and held["stats"]["assigned"] >= 1


def test_the_ours_arm_closes_the_put_at_half_its_credit(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    closes = _path([(date(2025, 1, 2), 100.0)], date(2025, 6, 30))
    _, res = _arm(closes, "ours")
    assert res["stats"]["tp50"] >= 2 and res["stats"].get("assigned", 0) == 0
    _tiles(res)
    first = res["rows"][0]
    assert first["how"] == "tp50" and first["wheel"] > 0


def test_a_half_day_takes_no_entry(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-11-28")
    early = date(2025, 11, 28)
    assert cal.is_early_close(early)
    closes = _path([(early, 100.0)], date(2026, 1, 30))
    _, res = _arm(closes, "vendor")
    assert res["stats"]["half_day"] == 1 and res["rows"][0]["entry"] > early.isoformat()


def test_shares_held_across_a_split_scale_and_are_called_away_on_the_new_basis(monkeypatch):
    monkeypatch.setitem(m.SPEC, "entry_start", "2025-01-02")
    legs = [
        (date(2025, 1, 2), 100.0),
        (date(2025, 2, 10), 90.0),
        (date(2025, 3, 3), 45.0),
        (date(2025, 4, 1), 60.0),
    ]
    closes = _path(legs, date(2025, 6, 30))
    nd = _name(closes, splits={date(2025, 3, 3): 2.0})
    res = m.run_arm(nd, "vendor", lambda s: 0.04)
    _tiles(res)
    assert res["stats"]["assigned"] >= 1
    # No option is sold across the split: the call waits for the first sale after it.
    assert res["stats"]["split"] > 0


def test_the_hold_leg_carries_dividends_and_scales_with_a_split():
    nd = _name(
        {date(2025, 1, 2): 100.0, date(2025, 1, 3): 50.0, date(2025, 1, 6): 55.0},
        splits={date(2025, 1, 3): 2.0},
        dividends={date(2025, 1, 6): 0.5},
    )
    # 100 shares become 200 at 50: no change on the split day; then +5 on 200 shares and 0.5 on 200.
    assert nd.hold_leg(date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 2)) == pytest.approx(0.0)
    assert nd.hold_leg(date(2025, 1, 2), date(2025, 1, 6), date(2025, 1, 2)) == pytest.approx(1000.0 + 100.0)


def test_the_requests_cover_each_monthly_from_66_days_before_it():
    reqs = m.requests(["JNJ"])
    first, last = reqs[0], reqs[-1]
    assert (first["symbol"], first["expiration"], first["start"]) == ("JNJ", "2016-03-18", "2016-03-01")
    assert last["through"] == "2026-10-28" and len({r["expiration"] for r in reqs}) == len(reqs)
    mid = next(r for r in reqs if r["expiration"] == "2025-06-20")
    assert mid["start"] == "2025-04-15" and mid["through"] == "2025-06-20"


# ------------------------------------------------------------------------------------------------
# The rule: pooled weeks, the halves, the verdict.


def _name_of_half(half: int, taken: str = "") -> str:
    for i in range(200):
        s = f"N{i}"
        if core_robustness.name_half(s) == half and s != taken:
            return s
    raise AssertionError


def _rows(per_week: float, first=date(2018, 1, 1), weeks=460, jitter: float = 0.002) -> list[dict]:
    """Two names a week, one in each name-half, each week's return a little off `per_week` in a pattern
    that does not repeat in step with the name pair."""
    syms = [_name_of_half(0), _name_of_half(1)]
    out = []
    for i in range(weeks):
        day = (first + timedelta(days=7 * i + 2)).isoformat()  # a Wednesday
        for j, s in enumerate(syms):
            wobble = ((i * 7 + j * 3) % 5 - 2) / 2.0
            diff = (per_week + jitter * wobble) * 10_000.0
            out.append({"symbol": s, "entry": day, "capital": 10_000.0, "diff": diff,
                        "days": 40, "rate": 0.03, "slippage": 5.0})  # fmt: skip
    return out


def test_pooling_makes_one_return_per_entry_week_over_the_names_capital():
    rows = _rows(0.01, weeks=3)
    pooled = m.pool(rows)
    assert [p["date"] for p in pooled] == ["2018-01-01", "2018-01-08", "2018-01-15"]
    assert pooled[0]["net"] == pytest.approx(sum(r["diff"] for r in rows[:2]) / 20_000.0)
    assert pooled[0]["bp"] == 0.0 and pooled[0]["rows"] == 2
    # A Sunday and the Monday after it are in different weeks: the split date, a Sunday, is an edge.
    sunday, monday = date(2023, 10, 1), date(2023, 10, 2)
    rs = [{**rows[0], "entry": sunday.isoformat()}, {**rows[0], "entry": monday.isoformat()}]
    assert [p["date"] for p in m.pool(rs)] == ["2023-09-25", "2023-10-02"]


def test_an_arm_that_beats_hold_in_every_half_passes_and_its_holdout_is_read():
    by_arm = {"vendor": _rows(0.01), "vendor-stop": _rows(-0.01), "ours": _rows(0.0, jitter=0.001)}
    block = m.judge(by_arm)
    assert block["verdict"]["vendor"] == ck.PASS
    assert [p["verdict"] for p in block["arms"]["vendor"]["parts"]][0] == ck.PASS
    assert block["arms"]["vendor"]["parts"][1]["verdict"] == ck.PASS  # the holdout, read after a design pass
    assert block["verdict"]["vendor-stop"] == ck.FAIL
    assert block["arms"]["vendor-stop"]["holdout"] == kit_rule.NOT_READ
    assert block["verdict"]["ours"] == ck.FAIL


def test_a_design_pass_that_is_negative_in_one_half_fails_and_the_holdout_stays_unread():
    rows = _rows(0.01)
    for r in rows:  # the early time-half, 2018-01 to 2020-05, loses to holding
        if r["entry"] < m.HALF_RULE["time_split"]:
            r["diff"] = -r["diff"] * 0.05
    block = m.judge({"vendor": rows, "vendor-stop": _rows(0.01), "ours": _rows(0.01)})
    assert block["half"]["vendor"]["ok"] is False
    assert block["verdict"]["vendor"] == ck.FAIL
    assert block["arms"]["vendor"]["holdout"] == kit_rule.NOT_READ
    assert block["verdict"]["ours"] == ck.PASS


def test_too_few_design_weeks_is_no_verdict():
    block = m.judge({a: _rows(0.01, first=date(2022, 1, 3), weeks=60) for a in m.ARMS})
    assert set(block["verdict"].values()) == {ck.NO_VERDICT}
    assert kit_rule.zero_trade_arms(block) == []


def test_the_halves_split_design_rows_by_date_and_by_name():
    h = m.halves(_rows(0.01))
    assert set(h["rows"]) == {"time_before", "time_from", "name_0", "name_1"} and h["ok"] is True
    assert h["rows"]["time_before"] + h["rows"]["time_from"] == h["rows"]["name_0"] + h["rows"]["name_1"]


def test_the_name_table_counts_stuck_shares_and_the_measure_in_percent_of_capital_a_year():
    row = {"capital": 10_000.0, "days": 365.25, "diff": 500.0, "de_diff": 200.0, "wheel": 800.0,
           "interest": 100.0, "hold": 400.0}  # fmt: skip
    res = {
        "rows": [row],
        "assigns": [
            {"days": 30, "called_away": True},
            {"days": 200, "called_away": False},
        ],
        "stats": {},
    }
    t = m.name_table({"X": {"vendor": res}})["X"]["vendor"]
    assert t["diff_pct_per_year"] == pytest.approx(5.0) and t["de_diff_pct_per_year"] == pytest.approx(2.0)
    assert (t["assigned"], t["called_away"], t["shares_at_end"], t["stuck_cycles"]) == (2, 1, 1, 1)
    assert t["stuck_days"] == 110
