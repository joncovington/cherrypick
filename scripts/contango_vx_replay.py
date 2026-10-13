"""The contango switch held as a short second-month VIX future, beside SVXY on the same dates.

    python scripts/contango_vx_replay.py DATA_DIR [--fetch] [--start 2018-03-01] [--end ...]

`docs/vxm-plan.md` deferred a /VXM arm "until after a quarter of SVXY paper". That question does not
need the paper run: CFE publishes every VX contract's daily settlement from 2013-05-20, so the arm
can be replayed now. This script does that, through the module's own rule (`regime.target_state`, with
each arm's thresholds from the contango config) and sets the result beside
`scripts/contango_replay.py`'s SVXY arms over exactly the same sessions.

DATA_DIR holds CFE's per-contract settlement files, `vx/VX_<expiry>.csv` (and `vxm/VXM_<expiry>.csv`
for the VX-for-VXM check). `--fetch` downloads whatever is missing, and re-downloads contracts still
trading, from cdn.cboe.com into DATA_DIR. It refuses a directory under ~/.cherrypick: research data
does not go into the suite's home.

**The position, declared.** Each session, at the close:

- the ratio is Cboe's VIX/VIX3M close and the arm's state comes from `regime.target_state`, the
  same timing as the SVXY replay (decide at the close, fill at the close);
- in `risk`, the arm is short the **second-month** standard VX contract. From `--roll-sessions`
  sessions before the front month's final settlement it holds the contract after that instead,
  which is the second month once the front expires, so it never holds into a settlement print;
- size is `floor(fraction x NAV / (multiplier x settle))` whole contracts, set on entry and on each
  roll, and re-set between them only when the held count drifts more than `--resize-drift` from that
  target (so the arm does not trade a contract a day to stay exactly at the fraction);
- fills are at the settlement price. Each contract side costs `--fee` dollars (commission plus the
  CFE, clearing and NFA fees) and `--slip-ticks` ticks of 0.05 (half the usual one-tick spread by
  default). A roll is two legs, costed separately; a calendar-spread order would be cheaper, so
  this is the conservative side;
- the account's cash earns SHV's total return, less the initial margin posted against the short,
  which earns nothing. Out of the market, all of it earns SHV, exactly as the SVXY arms' cash does;
- `--margin-rate` is the initial margin as a fraction of notional. Brokers set it (CFE publishes
  no retail number), so it is an assumption, stated; peak utilisation over 100% is a margin call
  the replay does not model, and the table says how many sessions crossed it.

VXM is VX at a tenth of the size: same settlement prices, $100 a point against $1,000. Its own files
start 2020-09, so the replay prices VXM off VX settles, and checks the two agree where both exist.
A futures account that goes to zero stays at zero: the CAGR is then -100%, not a restart.

A second check rebuilds SVXY from the settles: -0.5x a constant-30-day blend of the front two
months, rebalanced daily (the index SVXY tracks), plus T-bills, less its expense ratio. Against the
real fund from 2018-03 it says whether the data and the calendar are right. Run through the same
rule it is the SVXY arms' stand-in for 2013-2018, when the real SVXY was a -1x fund.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contango_replay as _svxy  # noqa: E402  (the SVXY replay's inputs and arms, same dates)
from cherrypick.contango import cli as _cli  # noqa: E402
from cherrypick.contango import paper_loop as _loop  # noqa: E402
from cherrypick.contango import regime as _regime  # noqa: E402
from cherrypick.contango import replay as _replay  # noqa: E402

CFE_URL = "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/{p}/{p}_{d}.csv"
TICK = 0.05
MULTIPLIER = {"VX": 1000.0, "VXM": 100.0}
# Dollars a contract side, before slippage: the broker's commission (about $0.85 a micro and $1.25
# a standard contract at the suite's broker) plus the CFE transaction, clearing and NFA fees,
# rounded up. An assumption, stated, not a published schedule: --fee overrides it.
DEFAULT_FEE = {"VX": 3.50, "VXM": 2.00}


# --------------------------------------------------------------------------- data
def standard_expiry(year: int, month: int) -> date:
    """A monthly VX contract's nominal final settlement: the Wednesday 30 days before the third
    Friday of the following month. A holiday moves it a session earlier; `fetch` probes for that."""
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    d = date(ny, nm, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d - timedelta(days=30)


# The first contract year fetched. CFE's files start with the 2013 contracts (earlier ones are
# refused) and carry a zero settle before 2013-05-20, so the VX history starts there; VXM listed in
# 2020-08.
FIRST_YEAR = {"VX": 2013, "VXM": 2020}


def fetch(data_dir: Path, products=("VX", "VXM")) -> None:
    home = (Path.home() / ".cherrypick").resolve()
    if home == data_dir.resolve() or home in data_dir.resolve().parents:
        raise SystemExit(f"refusing to fetch into {data_dir}: research data stays out of ~/.cherrypick")
    today = date.today()
    for prod in products:
        folder = data_dir / prod.lower()
        folder.mkdir(parents=True, exist_ok=True)
        y, m = FIRST_YEAR[prod], 1
        while standard_expiry(y, m) <= today + timedelta(days=270):
            nominal = standard_expiry(y, m)
            candidates = [nominal + timedelta(days=off) for off in (0, -1, 1, -2)]
            have = [d for d in candidates if (folder / f"{prod}_{d.isoformat()}.csv").exists()]
            settled = nominal < today - timedelta(days=7)
            for d in [] if have and settled else (have or candidates):
                path = folder / f"{prod}_{d.isoformat()}.csv"
                if _download(CFE_URL.format(p=prod, d=d.isoformat()), path):
                    print(f"fetched {path.name}")
                    break
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _download(url: str, path: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except OSError:
        return False
    if not body.startswith(b"Trade Date") or len(body) < 200:
        return False
    path.write_bytes(body)
    return True


def load_settles(folder: Path, prod: str) -> dict[str, dict[str, float]]:
    """{expiry: {trade date: settle}} from CFE's per-contract files; zero settles dropped."""
    out: dict[str, dict[str, float]] = {}
    for path in sorted(folder.glob(f"{prod}_*.csv")):
        expiry = path.stem[len(prod) + 1 :]
        series: dict[str, float] = {}
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    v = float(row["Settle"])
                except (KeyError, ValueError):
                    continue
                if v > 0:
                    series[row["Trade Date"]] = v
        if series:
            out[expiry] = series
    return out


# --------------------------------------------------------------------------- the position
class Curve:
    """The VX term structure by session: which contract the arm holds, at what settle."""

    def __init__(self, settles: dict[str, dict[str, float]], calendar: list[str], roll_sessions: int):
        self.settles = settles
        self.expiries = sorted(settles)
        self.calendar = calendar
        self.index = {d: i for i, d in enumerate(calendar)}
        self.roll_sessions = roll_sessions

    def listed(self, d: str) -> list[str]:
        return [e for e in self.expiries if e > d and d in self.settles[e]]

    def sessions_to(self, d: str, expiry: str) -> int:
        i, j = self.index[d], self.index.get(expiry)
        return j - i if j is not None else 10**6

    def second_month(self, d: str) -> str | None:
        live = self.listed(d)
        if len(live) < 3:
            return None
        k = 1 if self.sessions_to(d, live[0]) <= self.roll_sessions else 0
        return live[k + 1]

    def price(self, expiry: str, d: str) -> float:
        s = self.settles[expiry]
        if d in s:
            return s[d]
        before = [x for x in s if x <= d]  # a missing settle carries the last one, never a later one
        if not before or expiry <= d:
            raise ValueError(f"no settle for {expiry} on {d}")
        return s[max(before)]


def run_futures(
    curve: Curve,
    days: list[str],
    ratio: dict[str, float],
    cash_tr: dict[str, float],
    params: dict,
    *,
    multiplier: float,
    fraction: float,
    account: float,
    fee: float,
    slip_ticks: float,
    resize_drift: float,
    margin_rate: float,
) -> dict:
    nav, state, held, n = account, None, None, 0
    navs: list[tuple[str, float]] = []
    side_cost = fee + slip_ticks * TICK * multiplier
    trades = rolls = contracts_traded = margin_calls = sessions_in = sessions_zero = 0
    costs = peak_util = peak_lev = 0.0
    prev = None
    for d in days:
        if prev is not None:
            posted = n * margin_rate * multiplier * curve.price(held, prev) if n else 0.0
            nav += max(nav - posted, 0.0) * (cash_tr[d] / cash_tr[prev] - 1)
            if n:
                nav -= n * multiplier * (curve.price(held, d) - curve.price(held, prev))
        if nav <= 0:
            navs.append((d, 0.0))
            break
        if n:
            notional = n * multiplier * curve.price(held, d)
            util = notional * margin_rate / nav
            peak_util, peak_lev = max(peak_util, util), max(peak_lev, notional / nav)
            margin_calls += util > 1.0
        target = _regime.target_state(ratio[d], state, params)
        want = curve.second_month(d) if target == _regime.RISK else None
        if target == _regime.RISK and want is None:
            want, target = held, state  # the curve is too thin to choose: keep what is held
        want_n = math.floor(fraction * nav / (multiplier * curve.price(want, d))) if want else 0
        traded = 0
        if want != held:
            traded = n + want_n
            rolls += held is not None and want is not None
            trades += 1
            held, n = want, want_n
        elif want and want_n and abs(n - want_n) > resize_drift * want_n:
            traded, n = abs(n - want_n), want_n
            trades += 1
        if traded:
            nav -= traded * side_cost
            costs += traded * side_cost
            contracts_traded += traded
        if held is None:
            n = 0
        state = target
        sessions_in += state == _regime.RISK
        sessions_zero += state == _regime.RISK and n == 0
        navs.append((d, nav))
        prev = d
    stats = _replay.stats([(d, v / account) for d, v in navs]) if navs[-1][1] > 0 else _ruin(navs, account)
    return {
        "navs": navs,
        "stats": stats,
        "trades": trades,
        "rolls": rolls,
        "contracts_traded": contracts_traded,
        "costs": costs,
        "peak_util": peak_util,
        "peak_leverage": peak_lev,
        "margin_call_sessions": margin_calls,
        "sessions_in_risk": sessions_in,
        "sessions_too_small": sessions_zero,
    }


def _ruin(navs, account) -> dict:
    s = _replay.stats([(d, max(v, 1e-9) / account) for d, v in navs])
    return {**s, "cagr": -1.0, "max_drawdown": -1.0}


def per_year(navs: list[tuple[str, float]]) -> dict[str, float]:
    out, start = {}, navs[0][1]
    for i, (d, v) in enumerate(navs):
        last = i + 1 == len(navs) or navs[i + 1][0][:4] != d[:4]
        if last:
            out[d[:4]] = v / start - 1 if start > 0 else -1.0
            start = v
    return out


# --------------------------------------------------------------------------- checks
def vxm_matches_vx(vx: dict, vxm: dict) -> tuple[int, int, float]:
    """Sessions where both a VX and a VXM contract of the same expiry settle, how many agree to a
    tick, and the largest gap."""
    both = agree = 0
    worst = 0.0
    for e, s in vxm.items():
        for d, v in s.items():
            if d in vx.get(e, {}):
                both += 1
                gap = abs(v - vx[e][d])
                agree += gap < TICK / 2
                worst = max(worst, gap)
    return both, agree, worst


def synthetic_svxy(curve: Curve, days: list[str], cash_tr: dict) -> dict[str, float]:
    """A total-return level for -0.5x a constant-30-day blend of the front two months, rebalanced
    daily, plus T-bills, less SVXY's 0.95% expense ratio: the fund rebuilt from settles, so the SVXY
    arms have a stand-in before 2018-03, when the real fund was a different (-1x) product."""
    level, out = 1.0, {days[0]: 1.0}
    for prev, d in zip(days, days[1:], strict=False):
        live = curve.listed(prev)
        t1 = (date.fromisoformat(live[0]) - date.fromisoformat(prev)).days
        t2 = (date.fromisoformat(live[1]) - date.fromisoformat(prev)).days
        a = min(max((t2 - 30) / (t2 - t1), 0.0), 1.0)
        idx = sum(
            w * (curve.price(e, d) / curve.price(e, prev) - 1) for e, w in ((live[0], a), (live[1], 1 - a))
        )
        gap = (date.fromisoformat(d) - date.fromisoformat(prev)).days
        level *= 1 - 0.5 * idx + (cash_tr[d] / cash_tr[prev] - 1) - 0.0095 * gap / 365
        out[d] = level
    return out


def tracking(model: dict[str, float], svxy_tr: dict[str, float], start: str) -> dict:
    """The synthetic fund against SVXY's own total return: CAGR each and the daily correlation."""
    use = [d for d in sorted(model) if d >= start and d in svxy_tr]
    rm = [model[d] / model[p] - 1 for p, d in zip(use, use[1:], strict=False)]
    ra = [svxy_tr[d] / svxy_tr[p] - 1 for p, d in zip(use, use[1:], strict=False)]
    yrs = (date.fromisoformat(use[-1]) - date.fromisoformat(use[0])).days / 365.25
    mm, ma = sum(rm) / len(rm), sum(ra) / len(ra)
    cov = sum((x - mm) * (y - ma) for x, y in zip(rm, ra, strict=True))
    sd = math.sqrt(sum((x - mm) ** 2 for x in rm) * sum((y - ma) ** 2 for y in ra))
    return {
        "start": use[0],
        "end": use[-1],
        "model_cagr": (model[use[-1]] / model[use[0]]) ** (1 / yrs) - 1,
        "svxy_cagr": (svxy_tr[use[-1]] / svxy_tr[use[0]]) ** (1 / yrs) - 1,
        "corr": cov / sd,
    }


# --------------------------------------------------------------------------- report
def _row(label: str, s: dict, extra: str) -> str:
    return (
        f"  {label:44} {s['start']}..{s['end']}  CAGR {s['cagr'] * 100:6.1f}%  "
        f"max DD {s['max_drawdown'] * 100:6.1f}%  worst day {s['worst_day'] * 100:6.1f}%  {extra}"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir", type=Path, help="holds vx/VX_<expiry>.csv (and vxm/); never ~/.cherrypick")
    ap.add_argument("--fetch", action="store_true", help="download missing and still-trading contracts first")
    ap.add_argument(
        "--start", action="append", help="a window start (repeatable; default 2018-03-01 and 2013-05-20)"
    )
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--roll-sessions", type=int, default=5)
    ap.add_argument("--fee", type=float, default=None, help="$ per contract side (default VXM 2.00, VX 3.50)")
    ap.add_argument("--slip-ticks", type=float, default=0.5, help="ticks of 0.05 per contract side")
    ap.add_argument("--resize-drift", type=float, default=0.25)
    ap.add_argument(
        "--margin-rate", type=float, default=0.45, help="initial margin as a fraction of notional"
    )
    ap.add_argument(
        "--cost-bps", type=float, default=2.0, help="the SVXY arms' switching cost, as their replay"
    )
    ap.add_argument("--config")
    args = ap.parse_args(argv)

    if args.fetch:
        fetch(args.data_dir)
    vx = load_settles(args.data_dir / "vx", "VX")
    if not vx:
        print(f"no VX files under {args.data_dir / 'vx'} (try --fetch)", file=sys.stderr)
        return 2
    vxm = load_settles(args.data_dir / "vxm", "VXM")
    if vxm:
        both, agree, worst = vxm_matches_vx(vx, vxm)
        print(f"VXM against VX settles: {agree} of {both} contract-sessions agree (worst gap {worst:.3f})")

    def fee_for(prod: str) -> float:
        return args.fee if args.fee is not None else DEFAULT_FEE[prod]

    config = _cli.load_config(args.config)
    ratio = _svxy.ratio_series()
    conn = _svxy.history_conn()
    shv = _svxy._shield.closes(conn, "SHV", total_return=True)[0]
    svxy = _svxy._shield.closes(conn, "SVXY", total_return=True)[0]
    traded = sorted(set().union(*(set(s) for s in vx.values())))
    calendar = [d for d in traded if d in ratio and d in shv]
    curve = Curve(vx, calendar, args.roll_sessions)
    usable = [d for d in calendar if d <= args.end and curve.second_month(d)]

    synth = synthetic_svxy(curve, usable, shv)
    t = tracking(synth, svxy, "2018-03-01")
    print(
        f"engine check, SVXY rebuilt from settles (ungated) vs SVXY {t['start']}..{t['end']}: "
        f"CAGR {t['model_cagr'] * 100:.1f}% vs {t['svxy_cagr'] * 100:.1f}%, daily correlation {t['corr']:.3f}"
    )

    sizes = [  # (product, fraction, account)
        ("VXM", 0.5, 10_000),
        ("VXM", 0.5, 25_000),
        ("VXM", 0.5, 100_000),
        ("VX", 0.5, 1_000_000),
        ("VXM", 1.0, 100_000),
    ]
    arms = _loop.enabled_arms(config)
    for start in args.start or ["2018-03-01", "2013-05-20"]:
        days = [d for d in usable if d >= start]
        print(f"\n== {days[0]}..{days[-1]} ({len(days)} sessions) ==")
        print(
            f"  costs a contract side: VXM ${fee_for('VXM'):.2f}, VX ${fee_for('VX'):.2f}, each plus "
            f"{args.slip_ticks:g} tick; margin {args.margin_rate:.0%} of notional; roll "
            f"{args.roll_sessions} sessions before the front settles"
        )
        svxy_rows = {}
        if days[0] >= "2018-02-27":
            for r in _svxy.run_arms(config, conn, ratio, start=days[0], end=days[-1], cost_bps=args.cost_bps):
                svxy_rows[r["arm"]] = r
                print(_row(f"SVXY {r['label']}", r["stats"], f"switches {r['switches']}"))
        else:
            print("  SVXY: no comparable history (it was -1x before 2018-02-27, a different fund)")
        years: dict[str, dict[str, float]] = {}
        for arm in arms:
            params = _loop.merged_params(config, arm)
            synth_run = _replay.run(days, ratio, synth, shv, params, cost_bps=args.cost_bps)
            print(
                _row(
                    f"{arm} synthetic SVXY (settles)", synth_run["stats"], f"switches {synth_run['switches']}"
                )
            )
            if arm in svxy_rows:
                years[f"SVXY {arm}"] = per_year(svxy_rows[arm]["navs"])
            else:
                years[f"synth SVXY {arm}"] = per_year(synth_run["navs"])
            for prod, frac, acct in sizes:
                out = run_futures(
                    curve,
                    days,
                    ratio,
                    shv,
                    params,
                    multiplier=MULTIPLIER[prod],
                    fraction=frac,
                    account=acct,
                    fee=fee_for(prod),
                    slip_ticks=args.slip_ticks,
                    resize_drift=args.resize_drift,
                    margin_rate=args.margin_rate,
                )
                label = f"{arm} /{prod} m2 {frac:g}x on ${acct:,}"
                yrs = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
                extra = (
                    f"trades {out['trades']} rolls {out['rolls']} cost ${out['costs'] / yrs:,.0f}/yr  "
                    f"peak margin {out['peak_util']:.0%} (calls {out['margin_call_sessions']})  "
                    f"peak lev {out['peak_leverage']:.2f}x  in risk too small {out['sessions_too_small']}"
                )
                print(_row(label, out["stats"], extra))
                if (prod, frac, acct) == ("VXM", 0.5, 100_000):
                    years[f"/VXM {arm}"] = per_year(out["navs"])
        if years:
            cols = list(years)
            print("\n  per year       " + "".join(f"{c:>20}" for c in cols))
            for y in sorted({y for v in years.values() for y in v}):
                cells = "".join(f"{years[c][y] * 100:19.1f}%" if y in years[c] else f"{'':>20}" for c in cols)
                print(f"  {y}           {cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
