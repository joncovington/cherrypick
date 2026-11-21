"""Item 101: the wheel run as a public webinar teaches it, against owning the same shares.

The declaration is research/edge-hypotheses-plan.md section 101; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

- `plan`: record the universe (the liquidity verdict's `tradable` names whose chains our store covers),
  the report dates (Dolt's earnings calendar) and the chain-path requests the study needs
  (`{results}/universe.json`, `earnings_dates.json`, `path_requests.json`).
- `study`: one run over the whole history, judged by the declared RULE (the study kit's capital and
  tail bars, cut at its split), written to `{results}/results.json` as the kit's verdict block.

**The wheel, per name and arm** (one contract, one position at a time): sell a cash-secured put on a
standard monthly; at its expiry a close under the strike assigns it and the shares come in at the
strike. Then, each session with no call open, sell the call at the lowest listed strike at or above
the effective cost (the assignment strike less every premium banked, per share) on the standard
monthly 30-45 days out; when its bid is under 1% of the stock, wait and look again next session.
Held to expiry, never rolled: a close at or over the strike calls the shares away, else the call
expires and the next one is sold the next session. After a call-away or an expired put the next put
is sold the next session. `vendor` sells the put at 40 delta on the monthly 21-45 days out nearest
30; `vendor-stop` is the same and buys the put back when the stock's close is 15% under the entry
close, selling a new put once the close is back at the exit close; `ours` sells the put at 16 delta
on the monthly 45-60 days out and buys it back at half its credit.

**Rows tile each name's timeline.** A row runs from the end of the previous cycle (the first row from
the arm's first put) to the end of this one, so it holds the idle days before its put. Its wheel
net is every cash flow in it (premiums, fees, slippage, settlement, the shares at strike, dividends
while the shares are held, and the open position marked at the run's end). Its `hold` is 100 shares
bought at the row's start close (the first row also pays the shares' costs) held to its end with
dividends. Both are on the same capital, 100 shares at the row's start close: the wheel's cash
earns the bill while it holds cash. The row's difference is wheel less hold; a week's rows from
every name are pooled to one return on capital, which is what the kit's bars judge.

**Declared limits.** Daily closes only; assignment is read at the expiry close, early assignment
and the dividend a call forfeits are not modelled (an upper bound); deltas are Black-76 off each
leg's mid and the chain's parity forward (the bill rate and dividends live in the forward), not
the vendor's; the liquidity label is today's, so it is hindsight, paired with a point-in-time check
at each entry that the monthly's at-the-money spread is within the label's bar.
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees, legbars, treasury
from cherrypick.core import progress as core_progress
from cherrypick.core import robustness as core_robustness

from cherrypick.backtest.study import calendars as kit_calendars
from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, filled by the declaration before any run. Rows are one per week of
#: row-entry sessions with every name pooled: the wheel's difference from holding the same shares, as
#: a return on the capital both sides hold. The difference already carries the bill (the wheel's cash
#: earns it), so a row's buying power is 0 and the capital bar's own bill term is nil. The delta bar
#: is not applicable: holding the shares is the delta comparison. 2.4 sits above Holm's first
#: threshold for three arms (about 2.13).
RULE = {
    "t_bar": 2.4,
    "min_n": 150,
    "holdout_min_n": 52,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 101},
    "delta": False,
    "splits": ["2023-10-01"],
}

#: The second design condition: the pooled mean difference is above zero in each time-half of the
#: design part (split) and in each name-half (`core.robustness.name_half`). Read with the design
#: part, before the holdout.
HALF_RULE = {"time_split": "2020-06-01", "name_halves": True}

#: The base trade, declared (the plan's section 101 states each in words).
SPEC = {
    "entry_start": "2016-03-01",
    "entry_end": "2026-10-16",
    "run_end": "2026-10-28",
    "arms": {
        "vendor": {"delta": 0.40, "dte": (21, 45), "target": 30, "manage": "hold"},
        "vendor-stop": {"delta": 0.40, "dte": (21, 45), "target": 30, "manage": "stop", "stop": 0.15},
        "ours": {"delta": 0.16, "dte": (45, 60), "target": None, "manage": "tp50"},
    },
    "call": {"band": (30, 45), "reach": (15, 66), "min_bid_pct_of_spot": 0.01},
    "fill_frac_of_spread": 0.25,
    "share_slip": 0.0005,
    "half_days": "no new entry on an early close",
    "atm_spread_bar": 0.075,
    "stuck_days": 90,
    "coverage_floor": 0.80,
    "universe": {"min_sessions": 2400, "min_two_sided": 0.60, "min_strikes": 15, "first_year": 2016},
    "report_span": ("2020-01-01", "2027-06-30"),
    "lead_days": 66,
}
ARMS = tuple(SPEC["arms"])
#: Funds are exempt from the report rule and judged with the stocks; reported apart.
FUNDS = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "SMH",
        "TLT",
        "GLD",
        "SLV",
        "GDX",
        "XLE",
        "XLF",
        "XLI",
        "XLP",
        "XLU",
        "XLV",
        "IBIT",
    }
)
#: A stock reports about every 91 days: no listed report within this many days of the last one is a
#: report the calendar has not reached, and two listed reports further apart than the gap's limit
#: have one missing between them (item 25's reading).
REPORT_DUE_DAYS = 80
REPORT_GAP_DAYS = 120
EPS = 1e-9

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
#: A sample of the paths is fetched first and the study runs on three names (`smoke`), so a crash or
#: an arm that judges no trades shows in minutes, not after the whole run (#1072).
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item101"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------------
# Pure: the calendar, the entry rules, the fills.


def pick_expiry(day: date, dte: tuple[int, int], target: int | None) -> date | None:
    """The standard monthly `dte[0]` to `dte[1]` calendar days out, nearest `target` (the earlier on a
    tie), or the earliest when there is no target; None when none falls in the band."""
    found = kit_calendars.monthlies_between(day + timedelta(days=dte[0]), day + timedelta(days=dte[1]))
    if not found:
        return None
    if target is None:
        return found[0]
    return min(found, key=lambda e: (abs((e - day).days - target), e))


def pick_expiry_near_band(day: date, band: tuple[int, int], reach: tuple[int, int]) -> date | None:
    """The standard monthly nearest the `band` of days out (distance 0 inside it), among those within
    `reach`; the later on a tie. A band narrower than the gap between monthlies has none some days,
    so the call's expiry is the nearest one, not a gate."""
    found = kit_calendars.monthlies_between(day + timedelta(days=reach[0]), day + timedelta(days=reach[1]))
    if not found:
        return None

    def miss(e: date) -> int:
        dte = (e - day).days
        return max(band[0] - dte, dte - band[1], 0)

    return min(found, key=lambda e: (miss(e), -(e - day).days))


def earnings_state(reports: list[date], start: date, end: date) -> str:
    """`crosses` when a report falls from start through end; `clear` when none does and the calendar
    shows the quarter around the span; else `unknown`, which is never `clear`."""
    if any(start <= r <= end for r in reports):
        return "crosses"
    prev = max((r for r in reports if r < start), default=None)
    nxt = min((r for r in reports if r > end), default=None)
    if prev is None:
        return "unknown"
    if nxt is not None:
        return "clear" if (nxt - prev).days <= REPORT_GAP_DAYS else "unknown"
    return "clear" if (end - prev).days < REPORT_DUE_DAYS else "unknown"


def spread_ok(q: dict | None) -> bool:
    """`core.legbars`' spread bar on one leg: two-sided, within 10% of the mid or a nickel."""
    if not legbars.two_sided(q):
        return False
    bars, width, mid = legbars.LEG_BARS, q["ask"] - q["bid"], (q["ask"] + q["bid"]) / 2
    return width <= bars["max_spread_abs"] + EPS or width / mid <= bars["max_spread_pct"] + EPS


def atm_spread_ok(chain: Chain) -> bool:
    """The liquidity label's own point-in-time bar: at the strike nearest the forward, the wider of
    the call's and the put's bid-ask is within 7.5% of its mid."""
    if chain.forward is None:
        return False
    both = [k for k in chain.by["C"] if k in chain.by["P"]]
    if not both:
        return False
    k = min(both, key=lambda s: (abs(s - chain.forward), s))
    worst = 0.0
    for q in (chain.by["C"][k], chain.by["P"][k]):
        if not legbars.two_sided(q):
            return False
        mid = (q["bid"] + q["ask"]) / 2
        worst = max(worst, (q["ask"] - q["bid"]) / mid)
    return worst <= SPEC["atm_spread_bar"] + EPS


def slip(q: dict) -> float:
    """What a fill concedes from mid: a quarter of the bid-ask, capped at a share of the mid."""
    mid = (q["bid"] + q["ask"]) / 2
    return min(
        SPEC["fill_frac_of_spread"] * max(q["ask"] - q["bid"], 0.0),
        fees.DEFAULT_COSTS["slippage_cap_frac_of_mid"] * max(mid, 0.0),
    )


def sell_price(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2 - slip(q)


def buy_price(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2 + slip(q)


def cost_strike(chain: Chain, cost: float) -> dict | None:
    """The lowest listed call strike at or above `cost` that carries a two-sided quote."""
    for k in sorted(chain.by["C"]):
        if k >= cost - EPS and legbars.two_sided(chain.by["C"][k]):
            return chain.by["C"][k]
    return None


def option_fee(symbol: str, contracts: float, *, opening: bool) -> float:
    """One short option's fee at `contracts` (fractional after a split): the open adds the $1
    commission; a buy-back to close is the pass-through fees only."""
    if opening:
        return fees.ic_open_fee(symbol, 1, legs=1, sell_legs=1, ndigits=4) * contracts
    return fees.ic_close_fee(symbol, 1, legs=1, sell_legs=0, ndigits=4) * contracts


# ------------------------------------------------------------------------------------------------
# One name's data.


class NameData:
    """One name's raw closes, report dates, splits (ex-date to new shares per old), dividends and a
    `chain_at(day, expiry) -> Chain | None` reader."""

    def __init__(self, symbol, fund, closes, reports, splits, dividends, chain_at):
        self.symbol, self.fund, self.closes = symbol, fund, closes
        self.reports, self.splits, self.dividends, self.chain_at = reports, splits, dividends, chain_at
        self._chains: dict = {}
        self.days = sorted(closes)
        self._split_days = sorted(splits)

    def close(self, d: date) -> float | None:
        if d in self.closes:
            return self.closes[d]
        i = bisect.bisect_right(self.days, d)
        return self.closes[self.days[i - 1]] if i else None

    def chain(self, d: date, e: date) -> Chain | None:
        key = (d, e)
        if key not in self._chains:
            self._chains[key] = self.chain_at(d, e)
        return self._chains[key]

    def g(self, d: date) -> float:
        """The product of the split ratios gone ex on or before `d`."""
        out = 1.0
        for s in self._split_days:
            if s <= d:
                out *= self.splits[s]
        return out

    def split_between(self, a: date, b: date) -> bool:
        return any(a < s <= b for s in self._split_days)

    def blocked(self, d: date, e: date) -> str | None:
        if self.split_between(d, e):
            return "split"
        if not self.fund:
            state = earnings_state(self.reports, d, e)
            if state != "clear":
                return "earnings" if state == "crosses" else "earnings_unknown"
        return None

    def hold_leg(self, a: date, b: date, base: date) -> float:
        """The change in value, dividends in, of 100 shares bought at `base`'s close (share count scaled
        by the splits since) from a's close to b's close."""
        gb = self.g(base)

        def value(t: date) -> float:
            return 100.0 * self.g(t) / gb * self.close(t)

        divs = sum(100.0 * self.g(x) / gb * amt for x, amt in self.dividends.items() if a < x <= b)
        return value(b) - value(a) + divs


# ------------------------------------------------------------------------------------------------
# The replay.


def run_arm(nd: NameData, arm: str, rate, tick=None) -> dict:
    """One name's wheel under one arm: the rows that tile its timeline, the assignments and the counts."""
    spec = SPEC["arms"][arm]
    start, entry_end, run_end = (date.fromisoformat(SPEC[k]) for k in ("entry_start", "entry_end", "run_end"))
    sym = nd.symbol
    stats: dict = defaultdict(int)
    rows: list[dict] = []
    assigns: list[dict] = []
    state = "FLAT"  # FLAT, PUT, SHARES
    put = call = None
    shares = basis = banked = 0.0
    assign_day: date | None = None
    last_end: date | None = None
    call_ok_after: date | None = (
        None  # a call goes on from the session after the shares came in or a call ended
    )
    stop_exit: float | None = None
    row: dict | None = None
    seg: tuple[float, date] | None = None  # the delta-equivalent segment now open: (share fraction, from)

    def new_row(day: date) -> dict:
        return {"start": day, "entry": None, "cash": 0.0, "fees": 0.0, "slippage": 0.0, "settlement": 0.0,
                "segs": [], "assigned": None, "first": row is None}  # fmt: skip

    def end_seg(day: date) -> None:
        nonlocal seg
        if seg is not None:
            row["segs"].append((seg[0], seg[1], day))
            seg = None

    def end_row(day: date, how: str) -> None:
        """Close the row at `day`'s close and open the next one there."""
        nonlocal row, state, put, call, shares, assign_day
        end_seg(day)
        s = row["start"]
        c0 = nd.close(s)
        cap = 100.0 * c0
        r = rate(s.isoformat())
        hold = nd.hold_leg(s, day, s)
        if row["first"]:
            hold -= SPEC["share_slip"] * cap + fees.stock_trade_fee(100, c0, side="buy", ndigits=4)
        days = max((day - s).days, 1)
        cash_days = max(((row["assigned"] or day) - s).days, 0)
        interest = cap * r * cash_days / 365.0
        wheel = row["cash"]
        de_stock = sum(f * nd.hold_leg(a, b, s) for f, a, b in row["segs"])
        de_cash_days = (
            days
            - sum((b - a).days for _, a, b in row["segs"])
            + sum((1.0 - f) * (b - a).days for f, a, b in row["segs"])
        )
        de_interest = cap * r * de_cash_days / 365.0
        rows.append({
            "symbol": sym, "arm": arm, "start": s.isoformat(), "end": day.isoformat(),
            "entry": (row["entry"] or s).isoformat(), "how": how, "days": days, "capital": cap, "rate": r,
            "wheel": wheel, "interest": interest, "hold": hold, "diff": wheel + interest - hold,
            "de_diff": wheel + interest - (de_stock + de_interest),
            "fees": row["fees"], "slippage": row["slippage"], "settlement": row["settlement"],
        })  # fmt: skip
        row = new_row(day)
        row["first"] = False

    def entry_gate(d: date, exp: date | None, dte_stat: str):
        """Why a sale is not made today, or None. Counts the reason."""
        if d > entry_end:
            return "after_entries"
        if cal.is_early_close(d):
            stats["half_day"] += 1
            return "half_day"
        if exp is None:
            stats[dte_stat] += 1
            return "no_expiry"
        why = nd.blocked(d, exp)
        if why:
            stats[why] += 1
            return why
        return None

    def try_put(d: date) -> None:
        nonlocal state, put, row, seg, last_end
        exp = pick_expiry(d, spec["dte"], spec["target"])
        if entry_gate(d, exp, "no_expiry") is not None:
            return
        ch = nd.chain(d, exp)
        if ch is None or ch.forward is None:
            stats["no_chain"] += 1
            return
        if not atm_spread_ok(ch):
            stats["atm_spread"] += 1
            return
        q = ch.nearest("P", spec["delta"])
        if q is None:
            stats["no_strike"] += 1
            return
        if not spread_ok(q):
            stats["leg_spread"] += 1
            return
        px = sell_price(q)
        if px <= 0:
            stats["no_credit"] += 1
            return
        dl = ch.delta(q)
        if row is None:
            row = new_row(d)
        fee = option_fee(sym, 1, opening=True)
        row["cash"] += px * 100.0 - fee
        row["fees"] += fee
        row["slippage"] += slip(q) * 100.0
        if row["entry"] is None:
            row["entry"] = d
        put = {"k": q["strike"], "exp": exp, "day": d, "credit": px, "entry_close": nd.close(d)}
        seg = (abs(dl) if dl is not None else 0.5, d)
        if dl is None:
            stats["no_delta"] += 1
        state = "PUT"
        stats["puts"] += 1

    def settle_put(d: date) -> None:
        nonlocal state, put, shares, basis, banked, assign_day, last_end, stop_exit, seg, call_ok_after
        xc = nd.close(put["exp"])
        end_day = put["exp"] if put["exp"] in nd.closes else d
        if xc < put["k"] - EPS:
            row["cash"] -= put["k"] * 100.0 + fees.stock_trade_fee(100, put["k"], side="buy", ndigits=4)
            row["fees"] += fees.stock_trade_fee(100, put["k"], side="buy", ndigits=4)
            row["cash"] -= fees.ASSIGNMENT_FEE_PER_SETTLEMENT
            row["settlement"] += fees.ASSIGNMENT_FEE_PER_SETTLEMENT
            end_seg(end_day)
            shares, basis, banked = 100.0, put["k"], put["credit"]
            assign_day = row["assigned"] = end_day
            seg = (1.0, end_day)
            call_ok_after = end_day
            state = "SHARES"
            stats["assigned"] += 1
            put = None
        else:
            state, put = "FLAT", None
            stats["expired_otm"] += 1
            last_end = end_day
            end_row(end_day, "put expired")

    def close_put(d: date, px: float, q: dict, how: str) -> None:
        nonlocal state, put, last_end, stop_exit
        fee = option_fee(sym, 1, opening=False)
        row["cash"] -= px * 100.0 + fee
        row["fees"] += fee
        row["slippage"] += slip(q) * 100.0
        if how == "stop":
            stop_exit = nd.close(d)
        state, put, last_end = "FLAT", None, d
        stats[how] += 1
        end_row(d, how)

    def try_call(d: date) -> None:
        nonlocal call, seg, banked
        cdte = SPEC["call"]
        exp = pick_expiry_near_band(d, cdte["band"], cdte["reach"])
        gate = entry_gate(d, exp, "no_call_expiry")
        if gate is not None:
            return
        ch = nd.chain(d, exp)
        if ch is None or ch.forward is None:
            stats["no_chain"] += 1
            return
        if not atm_spread_ok(ch):
            stats["atm_spread"] += 1
            return
        q = cost_strike(ch, basis - banked)
        if q is None:
            stats["wait_no_strike"] += 1
            return
        if q["bid"] < cdte["min_bid_pct_of_spot"] * nd.close(d) - EPS:
            stats["wait_bid"] += 1
            return
        if not spread_ok(q):
            stats["leg_spread"] += 1
            return
        px = sell_price(q)
        if px <= 0:
            stats["no_credit"] += 1
            return
        contracts = shares / 100.0
        dl = ch.delta(q)
        end_seg(d)
        fee = option_fee(sym, contracts, opening=True)
        row["cash"] += px * 100.0 * contracts - fee
        row["fees"] += fee
        row["slippage"] += slip(q) * 100.0 * contracts
        banked += px
        call = {"k": q["strike"], "exp": exp, "day": d, "credit": px}
        seg = (1.0 - (dl if dl is not None else 0.5), d)
        stats["calls"] += 1

    def settle_call(d: date) -> None:
        nonlocal state, call, shares, basis, banked, assign_day, last_end, seg, call_ok_after
        xc = nd.close(call["exp"])
        end_day = call["exp"] if call["exp"] in nd.closes else d
        if xc >= call["k"] - EPS:
            row["cash"] += call["k"] * shares
            fee = fees.stock_trade_fee(int(round(shares)), call["k"], side="sell", ndigits=4)
            row["cash"] -= fee + fees.ASSIGNMENT_FEE_PER_SETTLEMENT
            row["fees"] += fee
            row["settlement"] += fees.ASSIGNMENT_FEE_PER_SETTLEMENT
            assigns.append({"day": assign_day.isoformat(), "end": end_day.isoformat(),
                            "days": (end_day - assign_day).days, "called_away": True})  # fmt: skip
            stats["called_away"] += 1
            shares = basis = banked = 0.0
            state, call, assign_day, last_end = "FLAT", None, None, end_day
            end_row(end_day, "called away")
        else:
            call = None
            end_seg(end_day)
            seg = (1.0, end_day)
            call_ok_after = end_day
            stats["call_expired"] += 1

    last_session = max((x for x in nd.days if x <= run_end), default=None)
    for d in nd.days:
        if d < start or d > run_end:
            continue
        if tick is not None:
            tick(d)
        if state == "SHARES":
            if d in nd.splits:
                r = nd.splits[d]
                shares, basis, banked = shares * r, basis / r, banked / r
            if d in nd.dividends:
                row["cash"] += nd.dividends[d] * shares
        if state == "PUT" and d >= put["exp"]:
            settle_put(d)
        elif state == "SHARES" and call is not None and d >= call["exp"]:
            settle_call(d)
        if state == "PUT" and d > put["day"]:
            ch = nd.chain(d, put["exp"])
            q = ch.by["P"].get(put["k"]) if ch is not None else None
            if spec["manage"] == "tp50" and legbars.two_sided(q):
                px = buy_price(q)
                if px <= 0.5 * put["credit"] + EPS:
                    close_put(d, px, q, "tp50")
            elif spec["manage"] == "stop" and nd.close(d) <= (1.0 - spec["stop"]) * put["entry_close"] + EPS:
                if legbars.two_sided(q):
                    close_put(d, buy_price(q), q, "stop")
                else:
                    stats["stop_unpriced"] += 1
        if last_end is not None and d <= last_end:
            continue
        if state == "FLAT" and d <= entry_end:
            if stop_exit is not None and nd.close(d) < stop_exit - EPS:
                stats["stopped_days"] += 1
                continue
            stop_exit = None
            try_put(d)
        elif state == "SHARES":
            stats["share_sessions"] += 1
            if call is None and d <= entry_end and d > call_ok_after:
                try_call(d)
            if call is None:
                stats["waiting_sessions"] += 1
    # The run's end: whatever is open is marked at the last session's close.
    if row is not None and last_session is not None and (row["entry"] is not None or state != "FLAT"):
        if state == "PUT":
            ch = nd.chain(last_session, put["exp"])
            q = ch.by["P"].get(put["k"]) if ch is not None else None
            if legbars.two_sided(q):
                px, fee = buy_price(q), option_fee(sym, 1, opening=False)
            else:
                px, fee = max(put["k"] - nd.close(last_session), 0.0), 0.0
                stats["marked_intrinsic"] += 1
            row["cash"] -= px * 100.0 + fee
            row["fees"] += fee
            stats["open_put_at_end"] += 1
        elif state == "SHARES":
            if call is not None:
                ch = nd.chain(last_session, call["exp"])
                q = ch.by["C"].get(call["k"]) if ch is not None else None
                if legbars.two_sided(q):
                    px, fee = buy_price(q), option_fee(sym, shares / 100.0, opening=False)
                else:
                    px, fee = max(nd.close(last_session) - call["k"], 0.0), 0.0
                    stats["marked_intrinsic"] += 1
                row["cash"] -= px * 100.0 * shares / 100.0 + fee
                row["fees"] += fee
            row["cash"] += shares * nd.close(last_session)
            assigns.append({"day": assign_day.isoformat(), "end": last_session.isoformat(),
                            "days": (last_session - assign_day).days, "called_away": False})  # fmt: skip
            stats["shares_at_end"] += 1
        end_row(last_session, "run end")
    elif (
        row is not None and row["entry"] is None and last_session is not None and row["start"] < last_session
    ):
        end_row(last_session, "run end")
    stats["rows"] = len(rows)
    return {"rows": rows, "assigns": assigns, "stats": dict(stats)}


# ------------------------------------------------------------------------------------------------
# Pure: pooling, the rule, the report.


def pool(rows: list[dict]) -> list[dict]:
    """The kit's rows: one per week (Monday to Sunday) of the rows' entry sessions, every name pooled.
    `net` is the week's difference over its capital (a return), `bp` 0: the bill is inside the
    difference. Both split dates fall on a week's edge, so a week is wholly in one part."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = date.fromisoformat(r["entry"])
        by[(d - timedelta(days=d.weekday())).isoformat()].append(r)
    out = []
    for week in sorted(by):
        rs = by[week]
        cap = sum(r["capital"] for r in rs)
        out.append({
            "date": week,
            "net": sum(r["diff"] for r in rs) / cap,
            "bp": 0.0,
            "days": sum(r["days"] * r["capital"] for r in rs) / cap,
            "rate": sum(r["rate"] * r["capital"] for r in rs) / cap,
            "rows": len(rs),
        })  # fmt: skip
    return out


def mean_return(rows: list[dict]) -> float | None:
    cap = sum(r["capital"] for r in rows)
    return sum(r["diff"] for r in rows) / cap if cap else None


def halves(rows: list[dict]) -> dict:
    """HALF_RULE on the design part's rows: the pooled mean difference above zero in each time-half and
    each name-half."""
    split = RULE["splits"][0]
    design = [r for r in rows if r["entry"] < split]
    cut = HALF_RULE["time_split"]
    parts = {
        "time_before": [r for r in design if r["entry"] < cut],
        "time_from": [r for r in design if r["entry"] >= cut],
        "name_0": [r for r in design if core_robustness.name_half(r["symbol"]) == 0],
        "name_1": [r for r in design if core_robustness.name_half(r["symbol"]) == 1],
    }
    means = {k: mean_return(v) for k, v in parts.items()}
    return {"means": means, "rows": {k: len(v) for k, v in parts.items()},
            "ok": all(m is not None and m > 0 for m in means.values())}  # fmt: skip


def judge(by_arm: dict[str, list[dict]]) -> dict:
    """The verdict block: the kit's design read, the half-rule, and, for an arm that passed both, the
    holdout (the kit reads it only after its design part passes)."""
    pooled = {a: pool(rows) for a, rows in by_arm.items()}
    design_rule = {**RULE, "splits": []}
    design = kit_rule.evaluate(
        design_rule, {a: [r for r in p if r["date"] < RULE["splits"][0]] for a, p in pooled.items()}
    )
    half = {a: halves(rows) for a, rows in by_arm.items()}
    passed = [a for a in by_arm if design["verdict"][a] == ck.PASS and half[a]["ok"]]
    full = kit_rule.evaluate(RULE, {a: pooled[a] for a in passed}) if passed else {"arms": {}, "verdict": {}}
    verdict, arms = {}, {}
    for a in by_arm:
        if a in passed:
            verdict[a], arms[a] = full["verdict"][a], full["arms"][a]
        else:
            word = ck.FAIL if design["verdict"][a] == ck.PASS else design["verdict"][a]
            verdict[a] = word
            arms[a] = {**design["arms"][a], "holdout": kit_rule.NOT_READ}
    return {"rule": RULE, "half_rule": HALF_RULE, "arms": arms, "half": half, "verdict": verdict}


def name_table(results: dict[str, dict]) -> dict:
    """Per name and arm, the reported measure: the wheel's difference from holding, % of mean capital
    a year, with the stuck-share counts."""
    out = {}
    for sym, arms in results.items():
        out[sym] = {}
        for arm, res in arms.items():
            rows = res["rows"]
            if not rows:
                out[sym][arm] = {"rows": 0}
                continue
            cap = statistics.fmean(r["capital"] for r in rows)
            years = sum(r["days"] for r in rows) / 365.25
            asg = res["assigns"]
            stuck = SPEC["stuck_days"]
            out[sym][arm] = {
                "rows": len(rows),
                "years": years,
                "diff_pct_per_year": 100.0 * sum(r["diff"] for r in rows) / cap / years,
                "de_diff_pct_per_year": 100.0 * sum(r["de_diff"] for r in rows) / cap / years,
                "wheel_pct_per_year": 100.0 * sum(r["wheel"] + r["interest"] for r in rows) / cap / years,
                "hold_pct_per_year": 100.0 * sum(r["hold"] for r in rows) / cap / years,
                "assigned": len(asg),
                "called_away": sum(1 for a in asg if a["called_away"]),
                "shares_at_end": sum(1 for a in asg if not a["called_away"]),
                "stuck_cycles": sum(1 for a in asg if a["days"] > stuck),
                "stuck_days": sum(max(a["days"] - stuck, 0) for a in asg),
                "mean_days_held": statistics.fmean(a["days"] for a in asg) if asg else None,
                "stats": res["stats"],
            }
    return out


def report_beside(by_arm: dict[str, list[dict]], tables: dict) -> dict:
    """Reported, never judged: each arm's delta-equivalent comparison, the mid view, funds and stocks
    apart, each year, and the sign count across names."""
    out = {}
    split = RULE["splits"][0]
    for a, rows in by_arm.items():

        def part(rs):
            return {"rows": len(rs), "mean_return": mean_return(rs)} if rs else {"rows": 0}

        names = [t[a]["diff_pct_per_year"] for t in tables.values() if t.get(a, {}).get("rows")]
        out[a] = {
            "design": part([r for r in rows if r["entry"] < split]),
            "holdout": part([r for r in rows if r["entry"] >= split]),
            "funds": part([r for r in rows if r["symbol"] in FUNDS]),
            "stocks": part([r for r in rows if r["symbol"] not in FUNDS]),
            "years": {
                y: part([r for r in rows if r["entry"][:4] == y])
                for y in sorted({r["entry"][:4] for r in rows})
            },
            "delta_equivalent_mean_return": (
                sum(r["de_diff"] for r in rows) / sum(r["capital"] for r in rows) if rows else None
            ),
            "at_mid_mean_return": (
                sum(r["diff"] + r["slippage"] for r in rows) / sum(r["capital"] for r in rows)
                if rows
                else None
            ),
            "names_positive": sum(1 for x in names if x > 0),
            "names": len(names),
            "median_name_pct_per_year": statistics.median(names) if names else None,
        }
    return out


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > 10:
                raise SystemExit(f"no Treasury 3-month yield within 10 days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def requests(names: list[str]) -> list[dict]:
    """Every standard monthly from the first entry month through the run's end and the longest put's
    reach, each name's path from `lead_days` before it (never before the first entry) through the
    expiry or the run's end."""
    first, run_end = (date.fromisoformat(SPEC[k]) for k in ("entry_start", "run_end"))
    out = []
    for sym in sorted(names):
        for e in kit_calendars.monthlies_between(first, run_end + timedelta(days=70)):
            start = max(e - timedelta(days=SPEC["lead_days"]), first)
            through = min(e, run_end)
            if start > through:
                continue
            out.append({"symbol": sym, "expiration": e.isoformat(), "start": start.isoformat(),
                        "through": through.isoformat()})  # fmt: skip
    return out


def pick_universe(conn) -> dict:
    """The liquidity verdict's `tradable` names (and the funds that pass the same test) whose chains
    the store covers: enough complete sessions, and the declared quality floors every year."""
    from cherrypick.technicals import liquidity, skate

    from cherrypick.backtest import paths, storequality

    u = SPEC["universe"]
    verdicts = liquidity.load()
    tradable = liquidity.tradable_names(verdicts) | liquidity.reference_tradable(liquidity.load_reference())
    leveraged = set(skate.leveraged_funds())
    doc = json.loads((paths.results_dir() / "quality" / "eod_chains.json").read_text(encoding="utf-8"))
    years = [str(y) for y in range(u["first_year"], date.fromisoformat(SPEC["entry_end"]).year + 1)]
    counts = dict(
        conn.execute(
            "SELECT symbol, COUNT(DISTINCT session) FROM eod_chain_requests WHERE status = 'complete' "
            "AND session BETWEEN ? AND ? GROUP BY symbol",
            (SPEC["entry_start"], SPEC["entry_end"]),
        ).fetchall()
    )
    chosen, left = [], {}
    for sym in sorted(tradable):
        if sym in leveraged:
            left[sym] = "leveraged fund"
        elif counts.get(sym, 0) < u["min_sessions"]:
            left[sym] = f"{counts.get(sym, 0)} complete sessions"
        else:
            short = storequality.meets(
                doc, sym, years, min_two_sided=u["min_two_sided"], min_strikes=u["min_strikes"]
            )
            if short:
                left[sym] = short[0]
            else:
                chosen.append(sym)
    return {
        "label": "the liquidity verdict's tradable names and funds, today's: hindsight",
        "names": chosen,
        "funds": [s for s in chosen if s in FUNDS],
        "left_out": left,
    }


def fetch_reports(names: list[str]) -> dict[str, list[str]]:
    """Dolt's earnings calendar for the stocks, once, so the study needs neither the server nor the
    network. A day Dolt lists nothing for is a hole left as it is: an unknown report is never clear."""
    from cherrypick.technicals import land

    lo, hi = SPEC["report_span"]
    conn = land._connect(land.DEFAULTS, "earnings")
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(names))
        cur.execute(
            f"SELECT act_symbol, date FROM earnings_calendar WHERE act_symbol IN ({marks}) "
            "AND date BETWEEN %s AND %s",
            [*names, lo, hi],
        )
        got: dict[str, set[str]] = defaultdict(set)
        for sym, d in cur.fetchall():
            got[sym].add(d.isoformat() if hasattr(d, "isoformat") else str(d))
    finally:
        conn.close()
    return {s: sorted(got.get(s, set())) for s in names}


def cmd_plan(args) -> int:
    out = Path(args.out).parent if args.out else out_dir()
    conn = chain_store()
    try:
        uni = pick_universe(conn)
    finally:
        conn.close()
    stocks = [s for s in uni["names"] if s not in FUNDS]
    (out / "universe.json").write_text(json.dumps(uni, indent=1), encoding="utf-8")
    (out / "earnings_dates.json").write_text(
        json.dumps({"source": "Dolt earnings_calendar", "dates": fetch_reports(stocks)}), encoding="utf-8"
    )
    path = Path(args.out) if args.out else out / "path_requests.json"
    path.write_text(json.dumps({"requests": requests(uni["names"])}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "names": len(uni["names"]), "left_out": len(uni["left_out"])}))
    return 0


def load_name(inputs: Inputs, conn, hist, sym: str, reports: list[date]) -> NameData:
    from cherrypick.technicals import adjust, store

    raw = store.raw_bars(hist, sym)
    closes = {date.fromisoformat(b.date): b.close for b in raw if b.close}
    spl = adjust.dedupe_splits(raw, store.splits(hist, sym))
    divs, _ = store.dividends(hist, sym, spl)

    def chain_at(d: date, e: date) -> Chain | None:
        try:
            return inputs.chain(conn, sym, d.isoformat(), e.isoformat())
        except KeyError:
            return None

    return NameData(
        sym, sym in FUNDS, closes, reports,
        {date.fromisoformat(s.ex_date): s.to_factor / s.for_factor for s in spl},
        {date.fromisoformat(x.ex_date): x.amount for x in divs},
        chain_at,
    )  # fmt: skip


def coverage(inputs: Inputs, conn, sym: str, days: list[date]) -> dict[str, float]:
    """Per year, the share of the name's sessions with a stored chain on any monthly 21-60 days out."""
    first, run_end = date.fromisoformat(SPEC["entry_start"]), date.fromisoformat(SPEC["run_end"])
    held: dict[date, list[date]] = defaultdict(list)
    for e in kit_calendars.monthlies_between(first, run_end + timedelta(days=70)):
        try:
            for s in inputs.sessions(conn, sym, e.isoformat()):
                held[date.fromisoformat(s)].append(e)
        except KeyError:
            continue
    seen: dict[int, list[bool]] = defaultdict(list)
    for d in days:
        if first <= d <= run_end:
            seen[d.year].append(any(21 <= (e - d).days <= 60 for e in held.get(d, [])))
    return {str(y): sum(v) / len(v) for y, v in sorted(seen.items())}


def study(inputs: Inputs, conn, hist, names: list[str], reports: dict, rep) -> dict:
    rate = rate_reader()
    first, run_end = (date.fromisoformat(SPEC[k]) for k in ("entry_start", "run_end"))
    data = {
        s: load_name(inputs, conn, hist, s, [date.fromisoformat(d) for d in reports.get(s, [])])
        for s in names
    }
    # Every name's sessions, once for each arm: counted overall and per symbol as the replay goes.
    rep.phase(
        "sessions",
        total=sum(len(ARMS) * sum(1 for d in nd.days if first <= d <= run_end) for nd in data.values()),
        unit="sessions",
        by={s: len(ARMS) * sum(1 for d in nd.days if first <= d <= run_end) for s, nd in data.items()},
    )
    results: dict[str, dict] = {}
    cover: dict[str, dict] = {}
    dropped: dict[str, int] = defaultdict(int)
    for sym in names:
        nd = data[sym]
        cover[sym] = coverage(inputs, conn, sym, nd.days)
        results[sym] = {}
        for arm in ARMS:
            res = run_arm(
                nd,
                arm,
                rate,
                tick=lambda d, s=sym, a=arm: rep.advance("sessions", key=s, current=f"{s} {a} {d}"),
            )
            kept = [
                r for r in res["rows"]
                if cover[sym].get(r["entry"][:4], 0.0) >= SPEC["coverage_floor"]
            ]  # fmt: skip
            dropped[f"{sym} {arm}"] += len(res["rows"]) - len(kept)
            res["rows"] = kept
            results[sym][arm] = res
        nd._chains.clear()
    by_arm = {a: [r for s in results for r in results[s][a]["rows"]] for a in ARMS}
    block = judge(by_arm)
    tables = name_table(results)
    block["per_name"] = tables
    block["reported"] = report_beside(by_arm, tables)
    block["coverage"] = cover
    block["rows_below_coverage_floor"] = {k: v for k, v in dropped.items() if v}
    return block


def cmd_study(args) -> int:
    d = out_dir()
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    uni = json.loads((d / "universe.json").read_text(encoding="utf-8"))
    names = list(uni["names"])
    if args.smoke:
        funds = [n for n in names if n in FUNDS][:1]
        stocks = [n for n in names if n not in FUNDS][:2]
        names = funds + stocks
    reports = json.loads((d / "earnings_dates.json").read_text(encoding="utf-8"))["dates"]
    inputs = Inputs(d / "path_requests.json")
    conn = chain_store()
    hist = store.connect(tpaths.history_db())
    try:
        with core_progress.Reporter("item101-smoke" if args.smoke else "item101-study") as rep:
            block = study(inputs, conn, hist, names, reports, rep)
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 101, **block}
    doc["inputs"] = read
    doc["universe"] = {"names": names, "left_out": uni["left_out"], "label": uni["label"]}
    doc["corrections"] = list(REPLAY_CORRECTIONS)
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else d / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument(
                "--smoke", action="store_true", help="run three names on the sample fetched so far"
            )
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
