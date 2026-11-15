"""Item 87: item 86's earnings calendar with a lean: an out-of-the-money call or put calendar by a bias.

The declaration is research/edge-hypotheses-plan.md section 87; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

The trader nudges the calendar's strike toward his lean and picks the call or put calendar so a
leg-out leaves the back month on the right side. A backtest cannot read a lean, so three mechanical
ones stand in, each its own arm, on item 86's reports, expiries, exits and paths:

- `trend`: the entry close against its 50-session average (above: bullish).
- `skew`: contrarian on the front's 25-delta risk reversal against the name's median at its earlier
  reports in the sample (more put-rich than usual: bullish), as the 2026-10-13 study found it.
- `prior`: the direction most of the name's last four reports moved (a tie: no trade).

Bullish buys the call calendar at the strike nearest spot + half the expected move, bearish the put
calendar nearest spot - half of it; the expected move is 0.85 x the front's at-the-money straddle.

- `plan`: item 86's requests file, copied as this study's own.
- `study`: one run, judged by RULE (the kit's three bars, the delta bar applying: a lean is a
  directional bet) and EVENT_RULE (item 86's, plus the lean beating item 86's neutral calendar on the
  same reports), cut at the split.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as cal

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

_spec = importlib.util.spec_from_file_location(
    "earnings_calendar_item86", Path(__file__).with_name("earnings_calendar_item86.py")
)
m86 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_calendar_item86", m86)
_spec.loader.exec_module(m86)

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

SPEC = {
    "sma_sessions": 50,
    "skew_delta": 0.25,
    "skew_prior_min": 4,
    "skew_prior_max": 8,
    "prior_reports": 4,
    "em_factor": 0.85,
    "em_share": 0.5,
}

#: The kit's pass rule per arm. t 2.4 keeps Holm across the three arms from binding.
RULE = {
    "t_bar": 2.4,
    "min_n": 100,
    "holdout_min_n": 40,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 87},
    "delta": True,
    "splits": ["2024-07-01"],
}

#: Item 86's event rule at this item's t, plus the lean beating the neutral calendar.
EVENT_RULE = {**m86.EVENT_RULE, "t_bar": 2.4, "quiet_beats_unselected": False, "beats_neutral": True}

ARMS = ("trend", "skew", "prior")

#: Queued with `queue-chain --after item86/fetch-2`: the paths are item 86's.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

BULL, BEAR = 1, -1


# --------------------------------------------------------------------------- pure: the leans


def trend_lean(bars: dict[date, tuple], entry: date, n: int = SPEC["sma_sessions"]) -> int | None:
    closes, d = [], entry
    while len(closes) < n:
        b = bars.get(d)
        if b is None:
            return None
        closes.append(b[3])
        d = cal.previous_trading_day(d)
    return BULL if closes[0] > statistics.fmean(closes) else BEAR


def risk_reversal(front) -> float | None:
    """IV of the call nearest 25 delta less IV of the put nearest 25 delta, on the front chain."""
    c, p = front.nearest("C", SPEC["skew_delta"]), front.nearest("P", SPEC["skew_delta"])
    if c is None or p is None:
        return None
    ci, pi = front.iv(c), front.iv(p)
    return ci - pi if ci and pi else None


def skew_lean(rr: float | None, earlier: list[float]) -> int | None:
    """Contrarian: more put-rich than the name's median at its earlier reports (the last
    `skew_prior_max`, at least `skew_prior_min`) is bullish, less is bearish, equal is no lean."""
    if rr is None or len(earlier) < SPEC["skew_prior_min"]:
        return None
    med = statistics.median(earlier[-SPEC["skew_prior_max"] :])
    return BULL if rr < med else BEAR if rr > med else None


def prior_lean(moves: list[float], n: int = SPEC["prior_reports"]) -> int | None:
    """The direction most of the last `n` reaction moves took; None on a tie or too few."""
    last = moves[-n:]
    if len(last) < n:
        return None
    up = sum(1 for x in last if x > 0)
    down = sum(1 for x in last if x < 0)
    return BULL if up > down else BEAR if down > up else None


def reaction_moves(report_rows, report: date, bars: dict) -> list[float]:
    """Each earlier stated-timing report's signed move, reaction close over entry close, oldest first."""
    out = []
    for d, when in sorted(report_rows):
        timing = m86.e31.TIMINGS.get(when or "")
        if timing is None or d >= report:
            continue
        a = bars.get(m86.e31.entry_session(d, timing))
        b = bars.get(m86.e31.reaction_session(d, timing))
        if a and b and a[3]:
            out.append(b[3] / a[3] - 1.0)
    return out


def expected_move(front, spot: float) -> float | None:
    """`em_factor` x the front's straddle at the strike nearest spot quoted both sides."""
    ks = [k for k in front.by["C"] if m86.two_sided(front.by["C"][k]) and m86.two_sided(front.by["P"].get(k))]
    if not ks:
        return None
    k = min(ks, key=lambda x: (abs(x - spot), x))
    return SPEC["em_factor"] * (m86.mid(front.by["C"][k]) + m86.mid(front.by["P"][k]))


def lean_strike(front, back, lean: int, spot: float) -> tuple[str, float] | None:
    """(right, strike): the call calendar above spot by half the expected move for a bullish lean, the
    put calendar below it for a bearish one."""
    em = expected_move(front, spot)
    if em is None:
        return None
    right = "C" if lean == BULL else "P"
    k = m86.strike_near(front, back, right, spot + lean * SPEC["em_share"] * em)
    return (right, k) if k is not None else None


def position_delta(front, back, right: str, strike: float) -> float | None:
    """The calendar's delta in shares: long the back, short the front."""
    db, df = back.delta(back.by[right][strike]), front.delta(front.by[right][strike])
    return None if db is None or df is None else 100.0 * (db - df)


# --------------------------------------------------------------------------- the run


def lean_trade(inputs, conn, sym: str, d: dict, bars: dict, lean: int | None, rate, **kw) -> dict:
    if lean is None:
        return {"skip": "no_lean"}
    chains = m86.path_chains(inputs, conn, sym, d)
    f0, b0 = chains.get(d["entry"], (None, None))
    spot = bars.get(d["entry"])
    if not f0 or not b0:
        return {"skip": "no_entry_chain"}
    if spot is None:
        return {"skip": "no_bars"}
    got = lean_strike(f0, b0, lean, spot[3])
    if got is None:
        return {"skip": "no_strike"}
    right, k = got
    t = m86.price_trade(sym, right, k, m86.path_marks(chains, right, k), bars, d, **kw)
    if "skip" in t:
        return t
    delta = position_delta(f0, b0, right, k)
    exit_bar = bars.get(date.fromisoformat(t["exit"]))
    if delta is None or exit_bar is None:
        return {"skip": "no_delta"}
    t.update(
        {
            "right": right,
            "strike": k,
            "lean": lean,
            "financed": ck.financed_delta(
                delta, spot[3], exit_bar[3], t["paid"], rate(d["entry"].isoformat()), t["days"]
            ),
        }
    )
    return t


def leans(
    sym: str, d: dict, bars: dict, report: date, rows, rr_history: list[tuple[date, float]], front
) -> dict:
    earlier = [rr for when, rr in rr_history if when < d["entry"]]
    return {
        "trend": trend_lean(bars, d["entry"]),
        "skew": skew_lean(risk_reversal(front) if front else None, earlier),
        "prior": prior_lean(reaction_moves(rows, report, bars)),
    }


def run(inputs, conn, plan: list[dict], bars_all: dict, doc: dict, rate) -> dict:
    rows_by = {s: [(date.fromisoformat(x), w) for x, w in v] for s, v in doc["reports"].items()}
    trades = {a: [] for a in ARMS}
    reported = defaultdict(list)
    skips: dict[str, dict[str, int]] = {a: defaultdict(int) for a in ARMS}
    rr_hist: dict[str, list[tuple[date, float]]] = defaultdict(list)
    base = {"stop_frac": m86.SPEC["stop_frac"]}
    for e in sorted(plan, key=lambda r: r["entry_session"]):
        sym, b = e["symbol"], bars_all.get(e["symbol"], {})
        d = m86._dates(e["dates"])
        report = date.fromisoformat(e["report_date"])
        front0 = inputs.chain(conn, sym, d["entry"].isoformat(), d["front"].isoformat())
        ls = leans(sym, d, b, report, rows_by.get(sym, []), rr_hist[sym], front0)
        neutral = m86.atm_trade(inputs, conn, sym, d, b, "C", **base)
        cd = m86._dates(e["control"]["dates"]) if e.get("control") else None
        c_front = inputs.chain(conn, sym, cd["entry"].isoformat(), cd["front"].isoformat()) if cd else None
        c_report = date.fromisoformat(e["control"]["report_date"]) if cd else None
        c_ls = leans(sym, cd, b, c_report, rows_by.get(sym, []), rr_hist[sym], c_front) if cd else {}
        for a in ARMS:
            t = lean_trade(inputs, conn, sym, d, b, ls[a], rate, **base)
            if "skip" in t:
                skips[a][t["skip"]] += 1
                continue
            s = lean_trade(inputs, conn, sym, d, b, ls[a], rate, stress=EVENT_RULE["stress"], **base)
            c = (
                lean_trade(inputs, conn, sym, cd, b, c_ls.get(a), rate, **base)
                if cd
                else {"skip": "no_control"}
            )
            t.update(
                {
                    "symbol": sym,
                    "sector": e["sector"],
                    "entry": e["entry_session"],
                    "stress_ret": s.get("ret"),
                    "control_ret": c.get("ret"),
                    "neutral_ret": neutral.get("ret"),
                }
            )
            trades[a].append(t)
        # The bounds, reported: the side the stock actually went over the hold, and the other one.
        last_bar, first_bar = b.get(d["last"]), b.get(d["entry"])
        if last_bar and first_bar:
            actual = BULL if last_bar[3] > first_bar[3] else BEAR
            for name, lean in (("oracle", actual), ("wrong", -actual)):
                r = lean_trade(inputs, conn, sym, d, b, lean, rate, **base)
                if "skip" not in r:
                    reported[name].append({**r, "entry": e["entry_session"]})
        if front0 is not None:
            rr = risk_reversal(front0)
            if rr is not None:
                rr_hist[sym].append((d["entry"], rr))
    return {"trades": trades, "reported": dict(reported), "skips": {a: dict(v) for a, v in skips.items()}}


def judge(trades: dict[str, list[dict]], rate) -> dict:
    split = RULE["splits"][0]
    rows = {
        a: [{**r, "financed": t["financed"]} for r, t in zip(m86.kit_rows(ts, rate), ts, strict=True)]
        for a, ts in trades.items()
    }
    bars = kit_rule.evaluate(RULE, rows)
    event = {}
    for a, ts in trades.items():
        d = m86.arm_part([t for t in ts if t["entry"] < split], EVENT_RULE)
        h = m86.arm_part([t for t in ts if t["entry"] >= split], EVENT_RULE)
        ev = m86.judge_event(d, h, EVENT_RULE, None, min_n=RULE["min_n"], holdout_min_n=RULE["holdout_min_n"])
        nd = [
            t["ret"] - t["neutral_ret"] for t in ts if t["entry"] < split and t.get("neutral_ret") is not None
        ]
        beats = bool(nd) and statistics.fmean(nd) > 0
        if ev["verdict"] == m86.PASS and not beats:
            ev = {**ev, "verdict": m86.FAIL, "why": "does not beat the neutral calendar"}
        event[a] = {
            "design": d,
            "holdout": h,
            "beats_neutral": beats,
            "neutral_gap": statistics.fmean(nd) if nd else None,
            **ev,
        }
    return {
        **bars,
        "event": event,
        "verdict": {a: m86.both(bars["verdict"][a], event[a]["verdict"]) for a in trades},
    }


def cmd_plan(args) -> int:
    src = m86.results_dir("item86") / "path_requests.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    path = Path(args.out) if args.out else m86.results_dir("item87") / "path_requests.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(doc["requests"]), "from": str(src)}))
    return 0


def cmd_study(args) -> int:
    out = m86.results_dir("item87")
    plan = json.loads((m86.results_dir("item86") / "plan.json").read_text(encoding="utf-8"))["events"]
    doc = json.loads((m86.results_dir("item31") / "calendar.json").read_text(encoding="utf-8"))
    inputs = Inputs(out / "path_requests.json")
    bars = m86.load_bars(sorted(m86.SECTOR), args.host, args.port)
    rate = m86.rate_reader()
    conn = m86.chain_store()
    try:
        res = run(inputs, conn, plan, bars, doc, rate)
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    split = RULE["splits"][0]
    body = judge(res["trades"], rate)
    body["reported"] = {
        "bounds": {k: m86.summary([t for t in v if t["entry"] < split]) for k, v in res["reported"].items()},
        "arms": {
            a: {
                "design": m86.summary([t for t in ts if t["entry"] < split]),
                "holdout": m86.summary([t for t in ts if t["entry"] >= split]),
                "bullish": m86.summary([t for t in ts if t["lean"] == BULL and t["entry"] < split]),
                "bearish": m86.summary([t for t in ts if t["lean"] == BEAR and t["entry"] < split]),
            }
            for a, ts in res["trades"].items()
        },
        "skips": res["skips"],
    }
    doc_out = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 87,
        "spec": SPEC,
        "event_rule": EVENT_RULE,
        **body,
        "corrections": list(REPLAY_CORRECTIONS),
        "inputs": read,
    }
    path = Path(args.out) if args.out else out / "results.json"
    path.write_text(json.dumps(doc_out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc_out["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
