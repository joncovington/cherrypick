"""The read side's one query layer: each arm's NAV tear sheet, the buy-and-hold benchmark, and the
path the arm's own rule should have produced.

Three series, one per question:

- **The arm's NAV** (`contango_sessions.nav`, cash plus holding at mid at the decision tick): what it
  did.
- **Buy-and-hold of the risk fund**, from the same tick's recorded marks, scaled to the arm's
  starting capital on its first session: what holding SVXY outright did. The replay said holding
  earned more and fell further; this is that comparison, forward.
- **The expected path**: `replay.run` -- the rule the loop runs -- over this module's own recorded
  ratios and fund marks, with the cash fund's distributions added back, at the replay's 2 bps a
  side. Where the arm's NAV drifts from it, the drift is execution (spread paid over 2 bps, whole
  shares, missed windows), not the rule. It needs no other package's store, which is why it is
  priced off the module's own marks rather than off the technicals store's closes.

Every reading is `core.metrics.nav.nav_reading`: days, not trades.
"""

from __future__ import annotations

from cherrypick.core.metrics import nav as _nav

from cherrypick.contango import db, paper_loop, provider, replay


def nav_series(conn, arm: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT trade_date, nav FROM contango_sessions WHERE arm = ? AND nav IS NOT NULL ORDER BY trade_date",
        (arm,),
    )
    return [(r["trade_date"], float(r["nav"])) for r in rows]


def ratio_series(conn) -> dict[str, float]:
    rows = conn.execute(
        "SELECT trade_date, ratio FROM contango_regime WHERE usable = 1 AND ratio IS NOT NULL"
    )
    return {r["trade_date"]: float(r["ratio"]) for r in rows}


def total_return(mids: dict[str, float], dividends: list[dict]) -> dict[str, float]:
    """A price series with each distribution reinvested on its ex-date: the replay's cash leg must
    earn what a holder earned, and for a T-bill fund that is mostly the distribution."""
    by_ex: dict[str, float] = {}
    for d in dividends:
        by_ex[d["ex_date"]] = by_ex.get(d["ex_date"], 0.0) + d["amount"]
    out, factor, prev = {}, 1.0, None
    for day in sorted(mids):
        if prev is not None:
            paid = sum(a for ex, a in by_ex.items() if prev < ex <= day)
            if paid:
                factor *= 1 + paid / mids[prev]
        out[day] = mids[day] * factor
        prev = day
    return out


def benchmark(conn, symbol: str, start: str, capital: float) -> list[tuple[str, float]]:
    mids = {d: m for d, m in db.marks(conn, symbol).items() if d >= start}
    if not mids:
        return []
    first = mids[min(mids)]
    return [(d, round(capital * m / first, 2)) for d, m in sorted(mids.items())]


def expected_path(
    conn, config: dict, arm: str, *, technicals_path: str, start: str, capital: float, cost_bps: float = 2.0
) -> list[tuple[str, float]]:
    params = paper_loop.merged_params(config, arm)
    risk_symbol, cash_symbol = paper_loop._symbols(params)
    risk, cash_mids = db.marks(conn, risk_symbol), db.marks(conn, cash_symbol)
    days = sorted(d for d in risk if d >= start and d in cash_mids)
    if not days:
        return []
    divs = provider.read_dividends(technicals_path, [cash_symbol], since=days[0], through=days[-1]) or []
    cash = total_return({d: cash_mids[d] for d in days}, divs)
    run = replay.run(days, ratio_series(conn), risk, cash, params, cost_bps=cost_bps)
    return [(d, round(capital * v, 2)) for d, v in run["navs"]]


def metrics(conn, config: dict, *, technicals_path: str) -> dict:
    out: dict = {"basis": "daily", "arms": {}, "benchmarks": {}}
    for arm in paper_loop.enabled_arms(config):
        acct = db.account(conn, arm)
        series = nav_series(conn, arm)
        if acct is None or not series:
            out["arms"][arm] = {
                "reading": _nav.nav_reading([]),
                "series": [],
                "expected": [],
                "tracking": None,
            }
            continue
        capital, start = float(acct["starting_capital"]), series[0][0]
        expected = expected_path(
            conn, config, arm, technicals_path=technicals_path, start=start, capital=capital
        )
        exp_by_day = dict(expected)
        last_common = next((d for d, _ in reversed(series) if d in exp_by_day), None)
        tracking = (
            round(dict(series)[last_common] / exp_by_day[last_common] - 1, 6)
            if last_common is not None and exp_by_day[last_common] > 0
            else None
        )
        out["arms"][arm] = {
            "starting_capital": capital,
            "reading": _nav.nav_reading(series),
            "series": series,
            "expected": expected,
            "expected_reading": _nav.nav_reading(expected),
            "tracking": tracking,
        }
        risk_symbol = paper_loop._symbols(paper_loop.merged_params(config, arm))[0]
        if risk_symbol not in out["benchmarks"]:
            bench = benchmark(conn, risk_symbol, start, capital)
            out["benchmarks"][risk_symbol] = {"series": bench, "reading": _nav.nav_reading(bench)}
    return out
