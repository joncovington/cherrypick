"""curve, replayed on VXX's real closes: the signal, the spread at intrinsic, and the hook arm.

Recovered from the 2026-10-05 scratch study behind the curve calibration (`curve_replay.py` +
`curve_width.py`, never committed) and extended for the hook question. Read-only throughout: VXX
closes from the technicals history store, VIX / VIX3M / VVIX from the Cboe files the market-files
fetcher keeps, and (for `--validate`) the curve paper ledger opened `mode=ro`.

Sections (all by default; pick with `--section`):

- **signal** -- short VXX on a session whose PRIOR close had VIX/VIX3M under the gate, flat
  otherwise, rebalanced daily, borrow ignored. Against always-short.
- **intrinsic** -- the 2026-10-05 study as it ran: one spread at a time, entered on a gated day,
  held 20 sessions (35 DTE entry to the 7-DTE close) and settled at intrinsic, with credit and
  round-trip cost per width taken from 2026-10-05's closing Nov-20 chain as fractions of spot. No
  profit-take and no flip exit, which need marks this section cannot price.
- **hook** -- the arms on a Black-Scholes mark path, so the exits can run: control (contango entry,
  50% profit-take, flip exit, 7-DTE close), noflip, and the hook arm both as built (control's exits,
  flip included) and without the flip. The hook is `cherrypick.curve.regime.hook_signal` on daily
  closes: ratio above 1.10 and below the prior session's.

**The hook arm as built cannot hold a position.** `management.evaluate` closes a flip-book position
whenever today's regime reads backwardation (ratio >= `contango_max`, 0.97), and the hook only ever
fires above 1.10 -- so the tick after a hook entry closes it on `regime_flip`, and every hook trade
is a round trip for its costs. The replay prints both that and the hook held to control's other
exits, which is the trade the arm was meant to measure.

**The option model** (hook section only): VXX's implied vol is proxied by VVIX, ATM IV =
`c x VVIX / 100` with an upside skew `IV(K) = ATM + s x ln(K/S)`. c 0.68, s 0.6 are fitted to the
paper ledger's five recorded entries (2026-09-02..10-12, all in contango) on the CREDIT, which is
what the P&L depends on: median error +1%, range -22%..+8% (`--validate`). Fitting the leg IVs
instead (c 0.68, s 0.9) matched each leg's IV to a few points yet priced the credit 7-35% low: a
narrow spread is a small difference of two large prices. The module's net-credit floor is not
applied here. A hook day is a vol spike, far outside the fit: the IV
level there is an extrapolation, and the `--iv-scale` rows are the sensitivity, not noise.
Strikes are continuous (a 30-delta short, the long `width` x spot above it); the $2 structure on a
$17.22 VXX is 11.6% of spot, and widths scale with VXX across its reverse splits. Costs per spread,
as fractions of spot per share: entry 0.36% and exit 0.20%, from the ledger's recorded
commissions and slippage (`--validate` prints them). Daily closes only: a profit-take or flip that
would fire intraday fires at that session's close here.

**Validation** (`--validate`): the replay's regime read against `curve_regime` (every recorded
session: Cboe close ratio vs the loop's recorded ratio, classification and hook agreement), and the
option model against every recorded entry (modelled vs recorded credit, short delta, costs).

Usage:
    python scripts/curve_replay.py                         # every section
    python scripts/curve_replay.py --section hook --iv-scale 0.8 1.0 1.25
    python scripts/curve_replay.py --validate
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as st
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.curve import regime as _regime
from cherrypick.overview import files as _files
from cherrypick.technicals import adjust as _adjust
from cherrypick.technicals import paths as _paths
from cherrypick.technicals import store as _store

START = "2018-01-26"  # the current VXX series (VXXB until 2019) starts trading
GATE = 0.97  # contango_max
HOOK_THRESHOLD = 1.10
PROFIT_TAKE = 0.50
CLOSE_DTE = 7
DTE_MIN, DTE_MAX, DTE_TARGET = 25, 60, 35
SHORT_DELTA = 0.30
WIDTH_FRAC = 2.0 / 17.22  # the $2 structure on 2026-10-05's spot
COST_ENTRY = 0.0036  # per share, fraction of spot: commissions + slippage to open one spread
COST_EXIT = 0.0020  # the same to close
IV_C, IV_SKEW = 0.68, 0.6
RATE = 0.03
RISK_PER_TRADE = 0.10  # fraction of equity put at max loss per trade, for the equity curve


# --------------------------------------------------------------------------- inputs
def vxx_closes(symbol: str = "VXX") -> dict[str, float]:
    """Split-adjusted closes from the technicals history store, opened read-only."""
    conn = sqlite3.connect(f"file:{_paths.history_db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = _store.raw_bars(conn, symbol)
    splits = _adjust.dedupe_splits(raw, _store.splits(conn, symbol))
    bars = _adjust.adjust(raw, splits, [])
    bars = bars[_adjust.series_break(bars) :]
    return {b.date: b.close for b in bars}


def cboe(name: str, cboe_dir: Path | None) -> dict[str, float]:
    path = (cboe_dir / f"{name}.csv") if cboe_dir else _files.cboe_path(name)
    return {d.isoformat(): v for d, v in _files.parse_cboe(path.read_text(encoding="utf-8"))}


class Market:
    def __init__(self, cboe_dir: Path | None, since: str = START):
        self.px = vxx_closes()
        vix, v3m = cboe("VIX", cboe_dir), cboe("VIX3M", cboe_dir)
        self.vvix = cboe("VVIX", cboe_dir)
        self.days = [d for d in sorted(self.px) if d >= since and d in vix and d in v3m and d in self.vvix]
        self.ratio = {d: _regime.ratio(vix[d], v3m[d]) for d in self.days}
        self.hook = {}
        prev = None
        for d in self.days:
            self.hook[d] = _regime.hook_signal(self.ratio[d], prev, {"hook_threshold": HOOK_THRESHOLD})
            prev = self.ratio[d]


# --------------------------------------------------------------------------- stats
def nav_stats(navs: list[tuple[str, float]]) -> dict:
    vals = [v for _, v in navs]
    yrs = (date.fromisoformat(navs[-1][0]) - date.fromisoformat(navs[0][0])).days / 365.25
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    cagr = vals[-1] ** (1 / yrs) - 1 if vals[-1] > 0 and yrs > 0 else -1.0
    vol = st.pstdev(rets) * math.sqrt(252) if len(rets) > 1 else 0.0
    return {"cagr": cagr, "vol": vol, "mdd": mdd, "worst": min(rets) if rets else 0.0, "years": yrs}


def trade_stats(trades: list[dict], years: float) -> dict:
    """Per-trade P&L is in units of max loss. The equity curve risks RISK_PER_TRADE of equity on each
    trade's max loss, compounding, one trade at a time."""
    if not trades:
        return {"n": 0}
    pl = [t["pl"] for t in trades]
    eq, peak, mdd = 1.0, 1.0, 0.0
    for p in pl:
        eq *= 1 + RISK_PER_TRADE * p
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {
        "n": len(pl),
        "win": sum(p > 0 for p in pl) / len(pl),
        "mean": st.mean(pl),
        "worst": min(pl),
        "sum": sum(pl),
        "per_year": sum(pl) / years,
        "cagr": eq ** (1 / years) - 1 if eq > 0 else -1.0,
        "mdd": mdd,
    }


def fmt_trades(label: str, s: dict) -> str:
    if not s["n"]:
        return f"  {label:34} no trades"
    return (
        f"  {label:34} n {s['n']:3}  win {s['win'] * 100:4.0f}%  mean {s['mean'] * 100:+6.1f}%  "
        f"worst {s['worst'] * 100:+6.0f}%  sum {s['sum']:+6.2f} ML ({s['per_year']:+5.2f} ML/yr)  "
        f"@{RISK_PER_TRADE:.0%} risk: CAGR {s['cagr'] * 100:+5.1f}%  MDD {s['mdd'] * 100:6.1f}%"
    )


# --------------------------------------------------------------------------- A. the signal
def section_signal(m: Market) -> None:
    days = m.days
    print(f"A. the signal: daily short VXX, borrow ignored ({days[0]}..{days[-1]}, {len(days)} sessions,")
    share = sum(1 for d in days if m.ratio[d] < GATE) / len(days)
    print(f"   contango (< {GATE}) on {share:.0%} of them)")
    for label, gate in (
        ("always short", lambda r: True),
        (f"short when prior ratio < {GATE}", lambda r: r < GATE),
        ("short when prior ratio < 0.90", lambda r: r < 0.90),
    ):
        nav, navs = 1.0, [(days[0], 1.0)]
        for i in range(1, len(days)):
            r = m.px[days[i]] / m.px[days[i - 1]] - 1
            if gate(m.ratio[days[i - 1]]):
                nav *= 1 - r
            navs.append((days[i], max(nav, 1e-9)))
        s = nav_stats(navs)
        print(
            f"  {label:34} CAGR {s['cagr'] * 100:6.1f}%  vol {s['vol'] * 100:5.1f}%  "
            f"MDD {s['mdd'] * 100:6.1f}%  worst day {s['worst'] * 100:6.1f}%"
        )


# --------------------------------------------------------------------------- B. at intrinsic
# 2026-10-05's closing VXX Nov-20 chain, spot 17.22, short strike 20: width $ -> (credit, round-trip
# cost) per share. Scaled with spot as fractions of it across the whole history.
WIDTHS_20261005 = {1: (0.15, 0.10), 2: (0.265, 0.093), 5: (0.45, 0.14), 10: (0.51, 0.11)}
SPOT_20261005, SHORT_20261005, HOLD = 17.22, 20.0, 20


def section_intrinsic(m: Market) -> None:
    days, px, ratio = m.days, m.px, m.ratio
    years = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
    otm = SHORT_20261005 / SPOT_20261005 - 1
    print(
        f"\nB. the spread at intrinsic: gate < {GATE}, one at a time, {HOLD}-session hold, "
        f"short {otm:.1%} OTM"
    )
    print("   P&L per trade in units of max loss (ML); credit and cost from 2026-10-05's chain")
    for width, (credit, cost) in WIDTHS_20261005.items():
        w_f, cr_f, cost_f = width / SPOT_20261005, credit / SPOT_20261005, cost / SPOT_20261005
        for costed in (False, True) if width == 2 else (True,):
            trades, i = [], 1
            while i + HOLD < len(days):
                if ratio[days[i - 1]] < GATE:
                    s0, s1 = px[days[i]], px[days[i + HOLD]]
                    k, w = s0 * (1 + otm), s0 * w_f
                    loss = min(max(s1 - k, 0.0), w)
                    pl = cr_f * s0 - loss - (cost_f * s0 if costed else 0.0)
                    trades.append({"day": days[i], "pl": pl / ((w_f - cr_f) * s0)})
                    i += HOLD + 1
                else:
                    i += 1
            label = f"${width}-wide" + ("" if costed else " before costs")
            print(fmt_trades(label, trade_stats(trades, years)))


# --------------------------------------------------------------------------- C. the mark path
def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def bs_call(s: float, k: float, t: float, sig: float, r: float = RATE) -> float:
    if t <= 0 or sig <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
    return s * ncdf(d1) - k * math.exp(-r * t) * ncdf(d1 - sig * math.sqrt(t))


def call_delta(s: float, k: float, t: float, sig: float, r: float = RATE) -> float:
    d1 = (math.log(s / k) + (r + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
    return ncdf(d1)


def iv_at(s: float, k: float, vvix: float, scale: float = 1.0) -> float:
    return max(0.05, scale * IV_C * vvix / 100 + IV_SKEW * math.log(k / s))


def strike_for_delta(s: float, t: float, vvix: float, target: float, scale: float = 1.0) -> float:
    lo, hi = s * 0.8, s * 4.0
    for _ in range(60):
        k = (lo + hi) / 2
        if call_delta(s, k, t, iv_at(s, k, vvix, scale)) > target:
            lo = k
        else:
            hi = k
    return (lo + hi) / 2


def spread_value(s: float, k1: float, k2: float, t: float, vvix: float, scale: float = 1.0) -> float:
    return bs_call(s, k1, t, iv_at(s, k1, vvix, scale)) - bs_call(s, k2, t, iv_at(s, k2, vvix, scale))


def third_friday(y: int, mth: int) -> date:
    d = date(y, mth, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def monthly_expiry(day: str) -> date | None:
    """The monthly (third-Friday) expiry nearest DTE_TARGET inside [DTE_MIN, DTE_MAX]."""
    d0 = date.fromisoformat(day)
    best = None
    for k in range(4):
        y, mth = d0.year + (d0.month - 1 + k) // 12, (d0.month - 1 + k) % 12 + 1
        e = third_friday(y, mth)
        dte = (e - d0).days
        if DTE_MIN <= dte <= DTE_MAX and (
            best is None or abs(dte - DTE_TARGET) < abs((best - d0).days - DTE_TARGET)
        ):
            best = e
    return best


def run_arm(m: Market, *, entry: str, flip: bool, scale: float = 1.0, until: str | None = None) -> list[dict]:
    """One position at a time. `entry` is "contango" (close ratio < GATE) or "hook". Decision and
    fill at the same session's close. Exits checked at each later close (and at the entry close for
    the flip, which the live loop's next tick would see): flip, profit-take, CLOSE_DTE."""
    days = [d for d in m.days if until is None or d <= until]
    trades, i = [], 0
    while i < len(days):
        d = days[i]
        signal = m.ratio[d] < GATE if entry == "contango" else m.hook[d]
        exp = monthly_expiry(d) if signal else None
        if exp is None:
            i += 1
            continue
        s0, v0 = m.px[d], m.vvix[d]
        t0 = (exp - date.fromisoformat(d)).days / 365
        k1 = strike_for_delta(s0, t0, v0, SHORT_DELTA, scale)
        k2 = k1 + WIDTH_FRAC * s0
        credit = spread_value(s0, k1, k2, t0, v0, scale)
        max_loss = (k2 - k1) - credit
        if credit <= 0 or max_loss <= 0:
            i += 1
            continue
        exit_day, value, reason = None, None, None
        for j in range(i, len(days)):
            dj = days[j]
            dte = (exp - date.fromisoformat(dj)).days
            sj = m.px[dj]
            vj = spread_value(sj, k1, k2, dte / 365, m.vvix[dj], scale)
            if flip and m.ratio[dj] >= GATE:
                exit_day, value, reason = dj, vj, "regime_flip"
            elif j > i and dte <= CLOSE_DTE:
                exit_day, value, reason = dj, vj, "close_dte"
            elif j > i and vj <= PROFIT_TAKE * credit:
                exit_day, value, reason = dj, vj, "profit_take"
            if exit_day:
                break
        if exit_day is None:
            break  # still open at the end of the data
        cost = (COST_ENTRY + COST_EXIT) * s0
        pl = credit - value - cost
        trades.append(
            {
                "day": d,
                "exit": exit_day,
                "reason": reason,
                "credit_pct": credit / (k2 - k1),
                "ratio": m.ratio[d],
                "pl": pl / max_loss,
                "gross": (credit - value) / max_loss,
            }
        )
        i = days.index(exit_day) + 1
    return trades


def section_hook(m: Market, scales: list[float]) -> None:
    days = m.days
    years = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
    hooks = [d for d in days if m.hook[d]]
    above = [d for d in days if m.ratio[d] > HOOK_THRESHOLD]
    episodes, last = [], None
    for d in hooks:
        if last is None or days.index(d) - days.index(last) > 10:
            episodes.append([d])
        else:
            episodes[-1].append(d)
        last = d
    print(f"\nC. the arms on a modelled mark path ({days[0]}..{days[-1]}, {years:.1f}y)")
    print(
        f"   hook days: {len(hooks)} of {len(days)} sessions ({len(hooks) / len(days):.1%}); "
        f"ratio > {HOOK_THRESHOLD} on {len(above)}; {len(episodes)} episodes"
    )
    for ep in episodes:
        print(f"     {ep[0]}..{ep[-1]}  {len(ep)} hook day(s), peak ratio {max(m.ratio[d] for d in ep):.3f}")
    for scale in scales:
        print(f"   IV scale x{scale}:")
        arms = (
            ("control (contango, flip)", "contango", True),
            ("noflip (contango, no flip)", "contango", False),
            ("hook as built (flip on)", "hook", True),
            ("hook without the flip", "hook", False),
        )
        for label, entry, flip in arms:
            trades = run_arm(m, entry=entry, flip=flip, scale=scale)
            s = trade_stats(trades, years)
            print(fmt_trades(label, s))
            if entry == "hook" and trades and scale == scales[0]:
                for t in trades:
                    print(
                        f"       {t['day']} -> {t['exit']} {t['reason']:12} ratio {t['ratio']:.3f} "
                        f"credit {t['credit_pct']:.0%} of width, gross {t['gross'] * 100:+.0f}% "
                        f"net {t['pl'] * 100:+.0f}% of ML"
                    )
            if entry == "contango" and flip and scale == scales[0]:
                reasons = {}
                for t in trades:
                    reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
                print(
                    f"       exits {reasons}; mean credit "
                    f"{st.mean(t['credit_pct'] for t in trades):.0%} of width"
                )
    print("   per year, IV x1.0 (sum of per-trade P&L in ML): control | hook without flip")
    c = run_arm(m, entry="contango", flip=True)
    h = run_arm(m, entry="hook", flip=False)
    for y in sorted({d[:4] for d in days}):
        cy = [t["pl"] for t in c if t["day"][:4] == y]
        hy = [t["pl"] for t in h if t["day"][:4] == y]
        print(f"     {y}: control {sum(cy):+6.2f} (n {len(cy):2})  |  hook {sum(hy):+6.2f} (n {len(hy)})")


# --------------------------------------------------------------------------- validation
def validate(m: Market, ledger: Path) -> int:
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print(f"Validation against {ledger} (read-only)")

    # 1. The regime read: the loop's last usable ratio each session vs the Cboe close ratio.
    rows = conn.execute(
        "SELECT trade_date, ratio, regime, hook FROM curve_regime WHERE usable = 1 ORDER BY trade_date, tick"
    ).fetchall()
    last = {r["trade_date"]: r for r in rows}
    agree = hook_agree = n = 0
    diffs = []
    for d, r in sorted(last.items()):
        if d not in m.ratio:
            continue
        n += 1
        diffs.append(abs(r["ratio"] - m.ratio[d]))
        agree += _regime.classify(m.ratio[d]) == r["regime"]
        hook_agree += bool(r["hook"]) == m.hook[d]
    missing = sorted(set(last) - set(m.ratio))
    print(
        f"  regime: {n} recorded sessions with a Cboe close; classification agrees on {agree}, hook on "
        f"{hook_agree}; |recorded - close ratio| median {st.median(diffs):.4f}, max {max(diffs):.4f}"
        + (f"; no Cboe close yet for {missing}" if missing else "")
    )

    # 2. The option model against every recorded entry (deduplicated: paired arms share one fill).
    legs = {}
    for r in conn.execute("SELECT position_id, leg_role, strike, entry_iv, entry_delta FROM curve_legs"):
        legs.setdefault(r["position_id"], {})[r["leg_role"]] = r
    seen, fits, rows_out = set(), [], []
    for p in conn.execute("SELECT * FROM curve_positions ORDER BY entry_session"):
        key = (p["entry_session"], p["short_strike"], p["long_strike"])
        if key in seen or p["entry_session"] not in m.vvix:
            continue
        seen.add(key)
        s, vv = p["entry_spot"], m.vvix[p["entry_session"]]
        t = (date.fromisoformat(p["expiration"]) - date.fromisoformat(p["entry_session"])).days / 365
        for leg in legs.get(p["position_id"], {}).values():
            if leg["entry_iv"]:
                fits.append((leg["entry_iv"] - IV_SKEW * math.log(leg["strike"] / s)) / (vv / 100))
        model = spread_value(s, p["short_strike"], p["long_strike"], t, vv)
        delta = call_delta(s, p["short_strike"], t, iv_at(s, p["short_strike"], vv))
        q = p["quantity"] or 1
        entry_cost = ((p["entry_cost"] or 0) + (p["entry_slippage"] or 0)) / q / 100 / s
        exit_cost = (
            ((p["exit_cost"] or 0) + (p["exit_slippage"] or 0)) / q / 100 / s
            if p["exit_cost"] is not None
            else None
        )
        rows_out.append((p, model, delta, entry_cost, exit_cost, vv))
    print(f"  IV level fit: c = {st.median(fits):.3f} (median of {len(fits)} legs; script uses {IV_C})")
    for p, model, delta, ec, xc, vv in rows_out:
        print(
            f"  {p['entry_session']} {p['arm']:32} spot {p['entry_spot']:.2f} "
            f"{p['short_strike']:g}/{p['long_strike']:g} "
            f"VVIX {vv:5.1f}: credit recorded {p['entry_credit']:.3f} model {model:.3f} "
            f"({(model / p['entry_credit'] - 1) * 100:+.0f}%), "
            f"short delta {p['entry_short_delta']:.2f} model {delta:.2f}; "
            f"cost/spot entry {ec * 100:.2f}%" + (f" exit {xc * 100:.2f}%" if xc is not None else "")
        )
    errs = [model / p["entry_credit"] - 1 for p, model, *_ in rows_out]
    print(
        f"  credit error: median {st.median(errs) * 100:+.0f}%, "
        f"range {min(errs) * 100:+.0f}%..{max(errs) * 100:+.0f}%"
    )
    return 0


def main(argv=None) -> int:
    global IV_C, IV_SKEW
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--section", choices=("signal", "intrinsic", "hook", "all"), default="all")
    ap.add_argument("--iv-scale", type=float, nargs="+", default=[1.0, 0.8, 1.25])
    ap.add_argument("--since", default=START)
    ap.add_argument("--cboe-dir", type=Path, help="folder of VIX/VIX3M/VVIX CSVs (default: market-files)")
    ap.add_argument("--validate", action="store_true", help="check the replay against the curve paper ledger")
    ap.add_argument("--ledger", type=Path, default=_home.data_dir("curve") / "paper_trades.db")
    ap.add_argument("--iv-c", type=float, default=IV_C, help="ATM IV = c x VVIX / 100")
    ap.add_argument("--iv-skew", type=float, default=IV_SKEW, help="IV(K) = ATM + skew x ln(K/S)")
    args = ap.parse_args(argv)
    IV_C, IV_SKEW = args.iv_c, args.iv_skew
    m = Market(args.cboe_dir, args.since)
    if args.validate:
        return validate(m, args.ledger)
    if args.section in ("signal", "all"):
        section_signal(m)
    if args.section in ("intrinsic", "all"):
        section_intrinsic(m)
    if args.section in ("hook", "all"):
        section_hook(m, args.iv_scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
