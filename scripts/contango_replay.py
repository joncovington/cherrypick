"""Replay the contango switch over stored history, through the module's own rule.

    python scripts/contango_replay.py [--start 2018-03-01] [--cost-bps 2]
    python scripts/contango_replay.py --validate [--ledger PATH]

Reads SVXY/SHV total-return closes from the technicals store and VIX/VIX3M closes from Cboe's
files (`scripts/fetch_market_files.py` keeps both indexes), then runs `cherrypick.contango.replay`
for each arm the module's config declares, beside buy-and-hold of the risk fund. A script rather
than a module verb because it reads another package's store.

The default start is 2018-03-01: SVXY was -1x short VIX futures until 2018-02-27 and -0.5x since, so
the history before that is a different fund, and pooling the two is how the 2018 drawdown gets
either hidden or double-counted.

`--validate` checks the replay against the paper ledger instead, session by session, opening the
ledger read-only. For every session from each arm's first decision it compares:

- **the rule**: the ledger's own ratio, run through `regime.target_state` from the ledger's
  `state_before`, must give the ledger's `state_after`. A mismatch here is a bug in the loop;
- **the replay's decision**: the replay's state path, run on Cboe's closing ratios from the arm's
  opening session, against the ledger's holding and fund. A mismatch here is the 15:50 read
  disagreeing with the 16:15 close across a threshold, or a session the ledger never decided;
- **the prices**: each fund's decision-tick mid (`contango_marks`) against the store's raw close
  for the day, and the mid-to-mid return against the total-return close-to-close return the
  replay earns. A session the store has not reached yet is said so, never compared.

It ends with each arm's NAV against the replay run over the ledger's own marks (the module's
expected path), whose gap is execution: spread over the 2 bps, whole shares, missed windows.

A session with no ledger row at all (the loop was not ticking through the window, so not even a
`missed` row) is counted as unrecorded. It exits 1 on a rule break or a price beyond the
tolerance; decision differences and unrecorded sessions are reported, not failed, because the
15:50 read is not the close and a host can be down.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pmcc_shield_replay as _shield  # noqa: E402  (its split- and dividend-aware close reader)
from cherrypick.contango import analytics as _analytics  # noqa: E402
from cherrypick.contango import cli as _cli  # noqa: E402
from cherrypick.contango import db as _db  # noqa: E402
from cherrypick.contango import paper_loop as _loop  # noqa: E402
from cherrypick.contango import regime as _regime  # noqa: E402
from cherrypick.contango import replay as _replay  # noqa: E402
from cherrypick.overview import files as _files  # noqa: E402
from cherrypick.technicals import paths as _paths  # noqa: E402

# A decision-tick mid and the day's close are different prints (15:50 against 16:00), so they are
# compared for size, not equality: beyond this a price is reported as a mismatch. SVXY moves about
# 2% a day, so ten minutes can carry tens of basis points honestly.
PRICE_TOLERANCE_BPS = 100.0


def cboe(symbol: str) -> dict[str, float]:
    text = _files.cboe_path(symbol).read_text(encoding="utf-8")
    return {d.isoformat(): v for d, v in _files.parse_cboe(text)}


def ratio_series() -> dict[str, float]:
    """VIX/VIX3M at each Cboe close: the replay's regime read."""
    vix, vix3m = cboe("VIX"), cboe("VIX3M")
    return {d: vix[d] / vix3m[d] for d in vix if d in vix3m and vix3m[d] > 0}


def history_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_paths.history_db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_arms(
    config: dict, conn, ratio: dict[str, float], *, start: str, end: str, cost_bps: float
) -> list[dict]:
    """Each enabled arm, then buy-and-hold of each risk fund, over `start..end`: one dict per row
    with `label`, `arm`, `stats`, `switches`, `days_in_risk` and the replay's `navs`. Importable:
    `scripts/contango_vx_replay.py` sets the futures arms beside these on the same dates."""
    series: dict[str, dict[str, float]] = {}
    rows: list[dict] = []
    held: set[str] = set()
    for arm in _loop.enabled_arms(config):
        params = _loop.merged_params(config, arm)
        risk_sym, cash_sym = _loop._symbols(params)
        for sym in (risk_sym, cash_sym):
            if sym not in series:
                series[sym] = _shield.closes(conn, sym, total_return=True)[0]
        risk, cash = series[risk_sym], series[cash_sym]
        days = [d for d in sorted(risk) if start <= d <= end]
        out = _replay.run(days, ratio, risk, cash, params, cost_bps=cost_bps)
        rows.append({"label": f"{arm} ({risk_sym}/{cash_sym})", "arm": arm, **out})
        if risk_sym not in held:
            held.add(risk_sym)
            hold = {**params, "enter_below": 99.0, "exit_at_or_above": 99.0}
            bh = _replay.run(days, ratio, risk, cash, hold, cost_bps=cost_bps)
            rows.append({"label": f"buy-and-hold {risk_sym}", "arm": f"buy-and-hold {risk_sym}", **bh})
    return rows


# --------------------------------------------------------------------------- validation
def _bps(a: float, b: float) -> float:
    return (a / b - 1) * 10_000


def validate(config: dict, ledger: Path, *, out=print) -> dict:
    """Session-by-session comparison of the replay with the paper ledger (read-only). Returns the
    counts it prints, so a caller (or a test) can assert on them."""
    led = sqlite3.connect(f"file:{ledger.as_posix()}?mode=ro", uri=True)
    led.row_factory = sqlite3.Row
    ratio = ratio_series()
    hist = history_conn()
    store_end = hist.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    raw_close: dict[str, dict[str, float]] = {}
    tr_close: dict[str, dict[str, float]] = {}
    marks: dict[str, dict[str, float]] = {}
    for r in led.execute("SELECT trade_date, symbol, mid FROM contango_marks WHERE mid IS NOT NULL"):
        marks.setdefault(r["symbol"], {})[r["trade_date"]] = r["mid"]

    counts = {
        "sessions": 0,
        "unrecorded": 0,
        "rule_ok": 0,
        "rule_bad": 0,
        "decision_ok": 0,
        "decision_bad": 0,
    }
    counts.update({"price_ok": 0, "price_bad": 0, "price_unchecked": 0})
    out(f"ledger {ledger} (read-only); technicals store ends {store_end}; Cboe ratio ends {max(ratio)}")
    for arm in _loop.enabled_arms(config):
        params = _loop.merged_params(config, arm)
        risk_sym, cash_sym = _loop._symbols(params)
        fund = {_regime.RISK: risk_sym, _regime.CASH: cash_sym}
        for sym in (risk_sym, cash_sym):
            if sym not in raw_close:
                raw_close[sym] = {
                    r["date"]: r["close"]
                    for r in hist.execute(
                        "SELECT date, close FROM bars WHERE symbol = ? AND date >= '2026-01-01'", (sym,)
                    )
                }
                tr_close[sym] = _shield.closes(hist, sym, total_return=True)[0]
        sessions = {
            r["trade_date"]: r
            for r in led.execute("SELECT * FROM contango_sessions WHERE arm = ? ORDER BY trade_date", (arm,))
        }
        if not sessions:
            out(f"\n{arm}: no sessions in the ledger")
            continue
        first = min(sessions)
        last = max([*sessions, max(ratio)])
        days = sorted({d for d in ratio if first <= d <= last} | set(sessions))
        out(
            f"\n{arm} ({risk_sym}/{cash_sym}, in below {params.get('enter_below')}, "
            f"out at or above {params.get('exit_at_or_above')})"
        )
        state, prev_day, prev_state = None, None, None
        for d in days:
            row = sessions.get(d)
            notes: list[str] = []
            # The replay's decision on Cboe's close.
            replay_before = state
            if d in ratio:
                state = _regime.target_state(ratio[d], state, params)
                rp = f"close {ratio[d]:.4f} -> {state}"
            else:
                rp = "no Cboe close yet"
            if row is None:
                # Not even a `missed` row: the loop was not ticking through the window (a host
                # asleep or down). The arm kept its holding, which the replay may or may not share.
                counts["unrecorded"] += 1
                same = state is not None and prev_state == state
                out(
                    f"  {d}  replay {rp:28} ledger: NO SESSION ROW (loop not ticking in the window; "
                    f"the arm carried {prev_state or 'nothing'}"
                    f"{', as the replay holds' if same else ', the replay differs'})"
                )
                continue
            counts["sessions"] += 1
            # 1. The rule, on the ledger's own read.
            if row["ratio"] is not None and row["action"] != "missed":
                want = _regime.target_state(row["ratio"], row["state_before"], params)
                if want == row["state_after"]:
                    counts["rule_ok"] += 1
                else:
                    counts["rule_bad"] += 1
                    notes.append(
                        f"RULE: ledger ratio {row['ratio']:.4f} from {row['state_before']} "
                        f"gives {want}, ledger chose {row['state_after']}"
                    )
            # 2. The replay's decision against the ledger's holding.
            if d in ratio:
                ok = state == row["state_after"] and fund[state] == row["holding_symbol"]
                counts["decision_ok" if ok else "decision_bad"] += 1
                if not ok:
                    if row["action"] == "missed":
                        why = f"the ledger missed the window ({row['refusal']}) and kept its holding"
                    elif row["state_before"] != replay_before:
                        why = (
                            f"the paths split earlier (ledger came in {row['state_before']}, "
                            f"replay {replay_before})"
                        )
                    elif row["ratio"] is not None and _regime.target_state(
                        row["ratio"], replay_before, params
                    ) != _regime.target_state(ratio[d], replay_before, params):
                        why = "the 15:50 read and the 16:15 close fall either side of a threshold"
                    else:
                        why = "the ledger's choice breaks the rule (see RULE)"
                    notes.append(
                        f"DECISION: replay {state}/{fund[state]}, ledger "
                        f"{row['state_after']}/{row['holding_symbol']}: {why}"
                    )
                if row["ratio"] is not None:
                    notes.append(f"ratio 15:50 {row['ratio']:.4f} vs close {ratio[d]:.4f}")
            # 3. Prices: the decision-tick mid against the store.
            for sym in (risk_sym, cash_sym):
                mid = marks.get(sym, {}).get(d)
                if mid is None:
                    continue
                close = raw_close[sym].get(d)
                if close is None:
                    counts["price_unchecked"] += 1
                    notes.append(f"{sym} mid {mid:.3f}: store has no close (ends {store_end})")
                    continue
                gap = _bps(mid, close)
                bad = abs(gap) > PRICE_TOLERANCE_BPS
                msg = f"{sym} mid {mid:.3f} vs close {close:.3f} ({gap:+.1f} bps)"
                pm = marks[sym].get(prev_day) if prev_day else None
                tr = tr_close[sym]
                if pm and prev_day in tr and d in tr:
                    msg += (
                        f", return mid {_bps(mid, pm):+.1f} bps vs replay "
                        f"{_bps(tr[d], tr[prev_day]):+.1f} bps"
                    )
                counts["price_bad" if bad else "price_ok"] += 1
                notes.append(msg + (" MISMATCH" if bad else ""))
            out(
                f"  {d}  replay {rp:28} ledger {row['action']:6} {row['state_before'] or '-'}->"
                f"{row['state_after']} {row['holding_symbol']} @ {row['ratio']}"
            )
            for n in notes:
                out(f"      {n}")
            prev_day, prev_state = d, row["state_after"]
        # 4. The arm's NAV against the replay over its own marks.
        acct = led.execute("SELECT starting_capital FROM contango_accounts WHERE arm = ?", (arm,)).fetchone()
        if acct:
            exp = dict(
                _analytics.expected_path(
                    led,
                    config,
                    arm,
                    technicals_path=str(_paths.history_db()),
                    start=first,
                    capital=acct["starting_capital"],
                )
            )
            nav = dict(_analytics.nav_series(led, arm))
            common = sorted(set(exp) & set(nav))
            if common:
                d = common[-1]
                out(
                    f"  tracking at {d}: NAV {nav[d]:.2f} vs replay over the ledger's marks {exp[d]:.2f} "
                    f"({nav[d] - exp[d]:+.2f}, execution: spread over 2 bps, whole shares, missed windows)"
                )
    out(f"\n{counts}")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2018-03-01")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--cost-bps", type=float, default=2.0)
    ap.add_argument("--config")
    ap.add_argument(
        "--validate", action="store_true", help="compare the replay with the paper ledger, read-only"
    )
    ap.add_argument(
        "--ledger", help="the paper ledger (default: the module's, as `contango status` reads it)"
    )
    args = ap.parse_args(argv)

    config = _cli.load_config(args.config)
    if args.validate:
        ledger = Path(args.ledger or os.environ.get("CONTANGO_DB_PATH") or _db.default_db_path())
        if not ledger.exists():
            print(f"no ledger at {ledger}", file=sys.stderr)
            return 2
        counts = validate(config, ledger)
        return 1 if counts["rule_bad"] or counts["price_bad"] else 0

    rows = run_arms(
        config, history_conn(), ratio_series(), start=args.start, end=args.end, cost_bps=args.cost_bps
    )
    for r in rows:
        s = r["stats"]
        print(
            f"{r['label']:28} {s['start']}..{s['end']}  CAGR {s['cagr'] * 100:6.1f}%  "
            f"vol {s['vol'] * 100:5.1f}%  "
            f"max drawdown {s['max_drawdown'] * 100:6.1f}%  worst day {s['worst_day'] * 100:6.1f}%  "
            f"switches {r['switches']:4}  days in risk {r['days_in_risk']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
