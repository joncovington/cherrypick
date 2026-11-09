"""Item 57, item 46's lone pass out of sample (research/edge-hypotheses-plan.md): the equity-index 10-delta
put at 7 days on the weekly expiries item 46 never read, hedged and unhedged.

    python packages/backtest/scripts/index_put_weekly_item57.py plan [--out FILE] [--through DATE]  #
    chain-path requests python packages/backtest/scripts/index_put_weekly_item57.py coverage [--out FILE]
    # what the store holds, MDE python packages/backtest/scripts/index_put_weekly_item57.py study [--account
    USD] [--out FILE]  # two arms, results.json

**The sample.** Every weekly expiry, the Friday or the Thursday before it when the Friday is a holiday,
from `FIRST_EXPIRY` to `LAST_EXPIRY`, that is not a standard monthly (item 46's `monthly_expiry`). A 7-day
trade into a non-monthly Friday occupies a different week from every monthly 7-day trade, so these
trades are disjoint from the ones item 46 read. A holiday Friday moves its expiry, and so its entry, to
the Thursday, which in a handful of weeks shares a session with a monthly's trade; those weeks are left
out and listed in the result, and `disjoint_from_monthly` checks the rest on the calendar. The
names are the equity-index group, SPX (SPXW), SPY, QQQ and IWM, equal-weighted within a cycle, so SPX
and SPY count once between them, as in item 46.

**No arithmetic is copied.** The forward, the leg pick (Black delta nearest 0.10 on the parity forward),
the daily forward hedge and its 0.5 bp cost, the fees, the settlement, the Newey-West t, the cell
statistics, the judge and the store access are item 46's, loaded from its script so the two cannot drift
apart. What is new here: the weekly calendar, the path request (a 10-day path, enough for the 7-day
entry and a holiday's slack), the unhedged trade, and the two-arm judge.

**The two arms.** `hedged` is item 46's trade. `unhedged` is the same trade without the hedge: the
same entry, option costs, settlement and expiry, no hedge pnl and no hedge cost. Holm across the two at
0.025. Per arm, all must hold: mean net per cycle above zero with the one-sided Newey-West p (lag 1)
passing Holm; above zero in both time-halves (split 2021-06-01); above zero at 1.5x costs; above zero
on 3 of the 4 names; at least 80 cycles. The strike neighbours at 8 and 12 delta, the worst cycle,
`drawdown_range`, `max_size` and the forward read (monthly and weekly cycles expiring after
`FORWARD_AFTER`, as they become available) are reported beside, never judged.

Writes under the backtest data dir (results/research/item57/), never into the repo: the inputs are
licensed.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "variance_premium_item46", Path(__file__).with_name("variance_premium_item46.py")
)
i46 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("variance_premium_item46", i46)
_spec.loader.exec_module(i46)

MEMBERS = i46.GROUPS["equity_index"]
GROUP = "equity_index"
TENOR = 7
LEG = "put10"
NEIGHBOUR_LEGS = {0.08: "put8", 0.10: "put10", 0.12: "put12"}
ARMS = ("hedged", "unhedged")
#: The first weekly Friday of 2016-03; item 46's own first monthly is the 18th.
FIRST_EXPIRY = date(2016, 3, 4)
LAST_EXPIRY = date(2026, 10, 16)
#: The forward read starts with expiries after this day (item 46's "sessions after 2026-10-30").
FORWARD_AFTER = date(2026, 10, 30)
#: Calendar days of end-of-day path requested before an expiry: the 7-day entry plus slack for a
#: holiday on the entry day (the next session is at most three days on).
PATH_DAYS = 10
ALPHA = i46.ALPHA
MIN_CYCLES = i46.MIN_CYCLES

#: Replay corrections to this study (docs/conventions/working-rules.md). None yet.
REPLAY_CORRECTIONS: list[dict] = []


# ------------------------------------------------------------------------------------------------
# Pure: the calendar and the requests.


def expiry_for(friday: date) -> date:
    """A week's expiry: the Friday, or the session before it when the Friday is a holiday."""
    return friday if i46.cal.is_trading_day(friday) else i46.cal.previous_trading_day(friday)


@functools.cache
def monthlies(first: date, last: date) -> set[date]:
    """Item 46's standard monthly expiries, with a month either side of the window so an edge week is
    judged against its own month."""
    out, y, m = set(), first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.add(i46.monthly_expiry(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def all_expiries(first: date, last: date) -> list[date]:
    """Every weekly expiry in the window, monthlies included, in order."""
    d = first + timedelta(days=(4 - first.weekday()) % 7)  # the first Friday on or after `first`
    out = []
    while d <= last + timedelta(days=7):
        e = expiry_for(d)
        if first <= e <= last and (not out or e != out[-1]):
            out.append(e)
        d += timedelta(days=7)
    return out


def weekly_expiries(first: date = FIRST_EXPIRY, last: date = LAST_EXPIRY) -> list[date]:
    """The sample: weekly expiries that are not standard monthlies and whose 7-day trade does not
    overlap a monthly 7-day trade. A holiday moves an expiry to the Thursday, and its entry with it,
    so a few weeks would otherwise share a session with a monthly's trade; those are left out."""
    left_out = overlapping(first, last)
    return [e for e in not_monthly(first, last) if e not in left_out]


def not_monthly(first: date, last: date) -> list[date]:
    monthly = monthlies(first - timedelta(days=31), last + timedelta(days=31))
    return [e for e in all_expiries(first, last) if e not in monthly]


@functools.cache
def overlapping(first: date = FIRST_EXPIRY, last: date = LAST_EXPIRY) -> set[date]:
    """Non-monthly weeks whose 7-day trade shares a session with a monthly's: left out of the sample."""
    months = [trade_window(m) for m in monthlies(first - timedelta(days=31), last + timedelta(days=31))]
    return {e for e in not_monthly(first, last) if any(overlaps(trade_window(e), m) for m in months)}


def is_monthly(expiry: date) -> bool:
    return expiry == i46.monthly_expiry(expiry.year, expiry.month)


def trade_window(expiry: date) -> tuple[date, date]:
    """The sessions a 7-day trade into `expiry` occupies, entry to expiry."""
    return i46.entry_session(expiry, TENOR), expiry


def overlaps(a: tuple[date, date], b: tuple[date, date]) -> bool:
    """Two trades overlap when one is still open after the other has been entered. A trade entered at
    the close of the session another expires is not open with it."""
    return a[0] < b[1] and b[0] < a[1]


def disjoint_from_monthly(expiries: list[date]) -> int:
    """How many weekly trades overlap a monthly 7-day trade of item 46: zero for the sample."""
    lo, hi = min(expiries) - timedelta(days=31), max(expiries) + timedelta(days=31)
    months = [trade_window(m) for m in monthlies(lo, hi)]
    return sum(overlaps(trade_window(e), m) for e in expiries for m in months)


def path_request(symbol: str, expiry: date) -> dict:
    return {
        "symbol": symbol,
        "expiration": expiry.isoformat(),
        "start": (expiry - timedelta(days=PATH_DAYS)).isoformat(),
        "through": expiry.isoformat(),
    }


def build_requests(names=MEMBERS, expiries=None) -> list[dict]:
    expiries = weekly_expiries() if expiries is None else expiries
    return [path_request(sym, e) for sym in names for e in expiries]


def forward_expiries(through: date) -> list[date]:
    """Monthly and weekly expiries after `FORWARD_AFTER`, up to `through`: the forward read."""
    return all_expiries(FORWARD_AFTER + timedelta(days=1), through) if through > FORWARD_AFTER else []


# ------------------------------------------------------------------------------------------------
# The unhedged trade, from item 46's.


def trade_pair(symbol: str, sessions: dict, fwds: dict, expiry: date, leg: str, bid=None) -> dict:
    """Both arms' trade of one short leg: {"hedged": item 46's trade, "unhedged": the same without the
    hedge}, or {"drop": reason}. The unhedged trade keeps the premium, the payoff, the option costs
    and the settlement of the hedged one and takes out the hedge's pnl and its cost; nothing else is
    recomputed, so the two differ only by the hedge."""
    hedged = i46.trade(symbol, sessions, fwds, expiry, TENOR, leg, bid)
    if "drop" in hedged:
        return hedged
    s = i46.entry_session(expiry, TENOR)
    y = i46.years_to(s, expiry)
    contracts = i46.pick_leg(sessions[s], fwds[s], y, leg, bid)
    hg = i46.hedge(contracts, sessions, fwds, s, expiry)
    net = hedged["net"] - hedged["hedge_pnl"] + hg["cost"]
    costs = hedged["costs"] - hg["cost"]
    notional = 100.0 * fwds[s]
    return {
        "hedged": hedged,
        "unhedged": {
            **{
                k: v for k, v in hedged.items() if k not in ("hedge_pnl", "net", "net_bp", "costs", "cost_bp")
            },
            "costs": round(costs, 4),
            "net": round(net, 4),
            "net_bp": net / notional * 1e4,
            "cost_bp": costs / notional * 1e4,
        },
    }


# ------------------------------------------------------------------------------------------------
# The judge: item 46's, over two arms.


def judge_arms(cells: dict[str, dict]) -> dict[str, str]:
    """Each arm's verdict, in place on its cell, and the words. Holm runs across exactly the two
    declared arms at 0.025; item 46's `judge` carries the rest (both halves, 1.5x costs, 3 of 4 names,
    80 cycles)."""
    if set(cells) != set(ARMS):
        raise ValueError(f"the family is the two declared arms {ARMS}, not {sorted(cells)}")
    i46.judge({f"{GROUP}/{arm}": c for arm, c in cells.items()})
    return {arm: c["verdict"] for arm, c in cells.items()}


def study_word(words: dict[str, str]) -> str:
    vs = list(words.values())
    return "pass" if "pass" in vs else "fail" if "fail" in vs else "no verdict"


# ------------------------------------------------------------------------------------------------
# The stores and the steps.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item57"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sessions_for(conn, sym: str, expiry: date):
    req = path_request(sym, expiry)
    if i46._path_status(conn, req) != "complete":
        return None
    sessions = i46._path_sessions(conn, req)
    return sessions, {d: i46.forward(ch) for d, ch in sessions.items()}


def coverage() -> dict:
    """Entry quotes and forwards only, never an option's exit. The MDE reads the per-cycle realised
    variance term of the 10-delta put at 7 days, as item 46's coverage does."""
    expiries = weekly_expiries()
    conn = i46._chain_store()
    names, values = {}, {}
    try:
        for sym in MEMBERS:
            status_n: dict[str, int] = {}
            drops: dict[str, int] = {}
            first = None
            for e in expiries:
                st = i46._path_status(conn, path_request(sym, e))
                status_n[st] = status_n.get(st, 0) + 1
                got = _sessions_for(conn, sym, e)
                if got is None:
                    continue
                sessions, fwds = got
                first = first or e.isoformat()
                s = i46.entry_session(e, TENOR)
                why = (
                    "entry_missing"
                    if s not in sessions
                    else "expiry_missing"
                    if e not in sessions
                    else "no_forward"
                    if fwds.get(s) is None
                    else None
                )
                if why:
                    drops[why] = drops.get(why, 0) + 1
                    continue
                y = i46.years_to(s, e)
                contracts = i46.pick_leg(sessions[s], fwds[s], y, LEG)
                gam = i46.gamma(fwds[s], contracts, y) if contracts else None
                path = [fwds[d] for d in i46.trading_days(s, e) if fwds.get(d) is not None]
                v = i46.rv_term_bp(gam, path) if gam is not None else None
                if v is None:
                    drops["no_leg"] = drops.get("no_leg", 0) + 1
                else:
                    values.setdefault(sym, {})[e.isoformat()] = v
            names[sym] = {"paths": status_n, "first_complete_cycle": first, "drops": drops}
    finally:
        conn.close()
    from cherrypick.core.robustness import planned_mde

    means = list(i46.group_cycle_means(values, MEMBERS).values())
    cell = {"cycles": len(means), "enough": len(means) >= MIN_CYCLES}
    if len(means) >= 2:
        first = planned_mde(means, len(means), paired=True, alpha=ALPHA / len(ARMS), sided=1)
        last = planned_mde(means, len(means), paired=True, alpha=ALPHA, sided=1)
        cell.update({"sd_bp": last["sd"], "mde_bp_holm_first": first["mde"], "mde_bp_holm_last": last["mde"]})
    return {
        "item": "item57",
        "step": "coverage",
        "store": str(i46._chain_store_path()),
        "window": [FIRST_EXPIRY.isoformat(), LAST_EXPIRY.isoformat()],
        "weekly_expiries": len(expiries),
        "overlaps_with_monthly_trades": disjoint_from_monthly(expiries),
        "left_out_overlapping_a_monthly": [e.isoformat() for e in sorted(overlapping())],
        "names": names,
        "cell": cell,
        "note": (
            "entry quotes and forwards only, no option exit read; MDE in bp of entry forward notional from "
            f"the per-cycle realised-variance term, one-sided at {ALPHA / len(ARMS)} (Holm's first step "
            f"across the two arms) and {ALPHA} (its last); both arms share it, and the unhedged arm's "
            f"own spread is wider by the forward's drift, so its MDE is larger; fewer than {MIN_CYCLES} "
            "cycles is no verdict"
        ),
    }


def collect(expiries: list[date], legs, log=None) -> tuple[dict, dict]:
    """{arm: {(name, leg): {cycle: trade}}} and drop counts per name."""
    conn = i46._chain_store()
    trades: dict[str, dict] = {arm: {} for arm in ARMS}
    drops: dict[str, dict[str, int]] = {n: {} for n in MEMBERS}
    try:
        for sym in MEMBERS:
            for e in expiries:
                got = _sessions_for(conn, sym, e)
                if got is None:
                    continue
                sessions, fwds = got
                s = i46.entry_session(e, TENOR)
                bid = (
                    i46.priced(sessions[s], fwds[s], i46.years_to(s, e))
                    if s in sessions and fwds.get(s) is not None
                    else None
                )
                for leg in legs:
                    tr = trade_pair(sym, sessions, fwds, e, leg, bid)
                    if "drop" in tr:
                        if leg == LEG:
                            d = drops[sym]
                            d[tr["drop"]] = d.get(tr["drop"], 0) + 1
                        continue
                    for arm in ARMS:
                        trades[arm].setdefault((sym, leg), {})[e.isoformat()] = tr[arm]
            if log:
                log(f"{sym}: done")
    finally:
        conn.close()
    return trades, drops


def cells_for(trades: dict, leg: str) -> dict[str, dict]:
    return {
        arm: i46.cell_stats({n: trades[arm].get((n, leg), {}) for n in MEMBERS}, MEMBERS, TENOR)
        for arm in ARMS
    }


def _slim(cell: dict) -> dict:
    return {k: v for k, v in cell.items() if k != "series"}


def beside(arm: str, cell: dict, neighbours: dict, trades: dict, account: float) -> dict:
    """The robustness report for one arm, always written, never judged."""
    from cherrypick.core import robustness

    means = {
        d: (cell if leg == LEG else neighbours[leg][arm])["mean_bp"] for d, leg in NEIGHBOUR_LEGS.items()
    }
    out = {
        "strike_neighbourhood": robustness.neighbourhood(means, 0.10),
        **robustness.report(cell["series"]["nets"], cell["series"]["costs"], pass_stress=i46.PASS_STRESS),
        "worst_cycle": cell["worst_cycle"],
    }
    sizes = {}
    for n in MEMBERS:
        ts = list(trades[arm].get((n, LEG), {}).values())
        if ts:
            r = robustness.report(
                [x["net"] for x in ts],
                [x["costs"] for x in ts],
                account=account,
                account_share=i46.ACCOUNT_SHARE,
            )
            sizes[n] = r.get("max_size")
    out["max_size_by_name"] = sizes
    return out


def forward_read(today: date, log=None) -> dict:
    """Monthly and weekly cycles expiring after `FORWARD_AFTER`, both arms: reported, never judged."""
    expiries = forward_expiries(today)
    trades, _ = collect(expiries, (LEG,), log)
    out = {"after": FORWARD_AFTER.isoformat(), "judged": False}
    for arm in ARMS:
        by_kind = {}
        for kind, pick in (("monthly", True), ("weekly", False)):
            per = {
                n: {
                    c: t
                    for c, t in trades[arm].get((n, LEG), {}).items()
                    if is_monthly(date.fromisoformat(c)) is pick
                }
                for n in MEMBERS
            }
            nets = i46.group_cycle_means(
                {n: {c: t["net_bp"] for c, t in v.items()} for n, v in per.items()}, MEMBERS
            )
            by_kind[kind] = {"cycles": len(nets), "mean_bp": i46._mean(nets.values())}
        out[arm] = by_kind
    return out


def study(account: float = i46.ACCOUNT, today: date | None = None, log=None) -> dict:
    expiries = weekly_expiries()
    legs = tuple(NEIGHBOUR_LEGS.values())
    trades, drops = collect(expiries, legs, log)
    cells = cells_for(trades, LEG)
    words = judge_arms(cells)
    neighbours = {leg: cells_for(trades, leg) for leg in legs if leg != LEG}
    return {
        "item": "item57",
        "step": "study",
        "verdict": words,
        "overall": study_word(words),
        "window": [FIRST_EXPIRY.isoformat(), LAST_EXPIRY.isoformat()],
        "time_split": i46.TIME_SPLIT.isoformat(),
        "weekly_expiries": len(expiries),
        "overlaps_with_monthly_trades": disjoint_from_monthly(expiries),
        "left_out_overlapping_a_monthly": [e.isoformat() for e in sorted(overlapping())],
        "arms": {
            arm: {**_slim(c), "beside": beside(arm, c, neighbours, trades, account)}
            for arm, c in cells.items()
        },
        "neighbour_legs": {
            leg: {arm: _slim(c) for arm, c in by_arm.items()} for leg, by_arm in neighbours.items()
        },
        "forward": forward_read(today or date.today(), log),
        "drops": drops,
        "costs": {
            "option": "core.fees: slippage 0.125 of spread, ic_open_fee per contract",
            "hedge_bp_per_rebalance": i46.HEDGE_COST_BP,
            "settlement": "SPXW ic_expire_fee per ITM leg; ETFs assignment_round_trip_fee on 100 shares",
            "pass_stress": i46.PASS_STRESS,
        },
        "account": account,
        "corrections": REPLAY_CORRECTIONS,
        "limits": [
            "positions are held to expiry, so none is open at the window's end",
            "early assignment of ETF puts is not modelled: the ETF names are an upper bound",
            "a crash the sample does not hold is not priced (the peso case)",
            "the four names are today's choice",
        ],
    }


# ------------------------------------------------------------------------------------------------


def cmd_plan(args) -> int:
    through = date.fromisoformat(args.through) if args.through else LAST_EXPIRY
    expiries = weekly_expiries(FIRST_EXPIRY, min(through, LAST_EXPIRY)) + forward_expiries(through)
    reqs = build_requests(MEMBERS, expiries)
    path = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "names": len(MEMBERS),
                "expiries": len(expiries),
                "requests": len(reqs),
                "held_complete": i46.held(reqs, i46._complete_paths()),
                "file": str(path),
            }
        )
    )
    return 0


def cmd_coverage(args) -> int:
    if not i46._chain_store_path().exists():
        print("no chain store: run the item57/fetch-chain-paths row first", file=sys.stderr)
        return 2
    doc = coverage()
    path = Path(args.out) if args.out else _out_dir() / "coverage.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({"cycles": doc["cell"]["cycles"], "enough": doc["cell"]["enough"], "file": str(path)}))
    return 0


def cmd_study(args) -> int:
    if not i46._chain_store_path().exists():
        print("no chain store: run the item57/fetch-chain-paths row first", file=sys.stderr)
        return 2
    doc = study(args.account, log=lambda m: print(m, file=sys.stderr))
    path = Path(args.out) if args.out else _out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", default=None)
    p.add_argument("--through", default=None, help="extend the requests to this date (the forward read)")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("coverage")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_coverage)
    p = sub.add_parser("study")
    p.add_argument("--account", type=float, default=i46.ACCOUNT)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
