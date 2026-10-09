"""cherrypick-technicals command line.

python -m cherrypick.technicals land [--symbols A B ...]
python -m cherrypick.technicals status
python -m cherrypick.technicals bars SYMBOL [--raw] [--last N]
python -m cherrypick.technicals check-vendor [--all]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.core import home as _home

from . import land as _land
from . import store, vendor_check

PKG_ROOT = Path(__file__).resolve().parents[3]


def _config() -> dict:
    return _home.load_module_config("technicals", PKG_ROOT)


def cmd_land(args) -> int:
    report = _land.land(_config().get("dolt") or {}, wanted=args.symbols or None)
    missing = report.get("missing") or []
    if len(missing) > 20:
        report["missing"] = missing[:20] + [f"... {len(missing) - 20} more"]
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_liquidity(_args) -> int:
    """Tonight's liquidity verdicts (liquidity.py): local data only."""
    from . import liquidity

    report = liquidity.build(_config().get("dolt") or {})
    for key in ("newly_illiquid", "readmitted"):
        if len(report[key]) > 30:
            report[key] = report[key][:30] + [f"... {len(report[key]) - 30} more"]
    print(json.dumps(report, indent=1))
    return 0


def cmd_status(_args) -> int:
    conn = store.connect()
    row = conn.execute(
        "SELECT COUNT(DISTINCT symbol) s, COUNT(*) n, MIN(date) lo, MAX(date) hi FROM bars"
    ).fetchone()
    last = conn.execute(
        "SELECT landed_at, through, symbols, bars FROM landings ORDER BY landed_at DESC LIMIT 1"
    ).fetchone()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("splits", "dividends", "iv")
    }
    print(
        json.dumps(
            {
                "symbols": row["s"],
                "bars": row["n"],
                "span": [row["lo"], row["hi"]],
                **counts,
                "last_landing": dict(last) if last else None,
            },
            indent=1,
        )
    )
    return 0


def cmd_bars(args) -> int:
    conn = store.connect()
    sym = args.symbol.upper()
    rows = store.raw_bars(conn, sym) if args.raw else store.adjusted_bars(conn, sym)
    for b in rows[-args.last :]:
        print(
            f"{b.date}  {b.open:10.2f} {b.high:10.2f} {b.low:10.2f} {b.close:10.2f} {b.volume:14.0f}"
            + (f"  x{b.price_factor:.6f}" if not args.raw else "")
        )
    print(json.dumps({"iv_rank": store.iv_rank(conn, sym)}))
    return 0


def cmd_check_vendor(args) -> int:
    result = vendor_check.check()
    if not args.all:
        result["by_symbol"] = {s: r for s, r in result["by_symbol"].items() if r["agree"] < r["prices"]}
    print(json.dumps(result, indent=1))
    return 0 if result["prices"] and result["agree"] == result["prices"] else 1


def cmd_score_stages(_args) -> int:
    from . import stage_score

    print(json.dumps(stage_score.score(), indent=1))
    return 0


def cmd_stages(args) -> int:
    from . import stage, symbols

    conn = store.connect()
    rule = stage.DEFAULT_RULE
    bench = {b.date: b.close for b in store.adjusted_bars(conn, rule.benchmark)}
    day = args.session or max(bench)
    names = store.stocks(conn, symbols.candidates())
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in names}
    result = stage.stages_on(day, {s: c for s, c in closes.items() if c}, bench, rule)
    leaders = sorted(s for s, v in result.items() if v.side == "leader")
    laggards = sorted(s for s, v in result.items() if v.side == "laggard")
    print(
        json.dumps(
            {
                "session": day,
                "rule": rule.name,
                "leaders": len(leaders),
                "laggards": len(laggards),
                "stages": {s: f"{v.side}/{v.stage}" for s, v in sorted(result.items())},
            },
            indent=1,
        )
    )
    return 0


def cmd_score_rotation(_args) -> int:
    from . import stage_score

    print(json.dumps(stage_score.score_rotation(), indent=1))
    return 0


def cmd_rotation(args) -> int:
    from . import rotation, symbols

    conn = store.connect()
    rule = rotation.DEFAULT_RULE
    names = sorted({*symbols.ROTATION_ETFS, rule.benchmark, rule.asset_benchmark})
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in names}
    day = args.session or max(closes[rule.benchmark])
    states = rotation.states_on(day, closes, symbols.ASSET_ETFS, rule)
    by_state = {st: sorted(f for f, v in states.items() if v == st) for st in rotation.STATES}
    by_state["none"] = sorted(f for f, v in states.items() if v is None)
    print(json.dumps({"session": day, "rule": rule.name, **by_state}, indent=1))
    return 0


def cmd_breadth(args) -> int:
    """The daily breadth history: leaders, laggards, net and bullish share per session, over the
    candidates -- the report's 2-week chart, rebuilt from prices."""
    from . import stage, symbols

    conn = store.connect()
    rule = stage.DEFAULT_RULE
    bench = {b.date: b.close for b in store.adjusted_bars(conn, rule.benchmark)}
    names = store.stocks(conn, symbols.candidates())
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in names}
    closes = {s: c for s, c in closes.items() if c}
    rows = []
    for day in sorted(bench)[-args.sessions :]:
        result = stage.stages_on(day, closes, bench, rule)
        leaders = sum(v.side == "leader" for v in result.values())
        laggards = sum(v.side == "laggard" for v in result.values())
        both = leaders + laggards
        rows.append(
            {
                "session": day,
                "leaders": leaders,
                "laggards": laggards,
                "net": leaders - laggards,
                "bullish_share": round(leaders / both, 3) if both else None,
            }
        )
    print(json.dumps({"rule": rule.name, "universe": len(closes), "sessions": rows}, indent=1))
    return 0


def cmd_score_trends(_args) -> int:
    from . import chart_score

    result = chart_score.score_trends()
    print(json.dumps({k: v for k, v in result.items() if k != "by_symbol"}, indent=1))
    return 0


def cmd_score_levels(_args) -> int:
    from . import chart_score

    print(json.dumps(chart_score.score_levels(), indent=1))
    return 0


def cmd_score_level_selection(_args) -> int:
    from . import chart_score

    print(json.dumps(chart_score.score_level_selection(), indent=1))
    return 0


def cmd_score_rank(_args) -> int:
    from . import chart_score

    print(json.dumps(chart_score.score_rank(), indent=1))
    return 0


def cmd_score_signals(_args) -> int:
    from . import chart_score

    print(json.dumps(chart_score.score_signals(), indent=1))
    return 0


def cmd_report(args) -> int:
    from . import chart, report

    doc = report.build(args.session)
    if not doc.get("ok"):
        print(json.dumps(doc))
        return 1
    path = report.write(doc)
    # The per-name chart files the console's chart page reads, for the same session.
    charts = chart.write_all(doc["session"])
    print(
        json.dumps(
            {"ok": True, "path": path, "session": doc["session"], "universe": doc["universe"], **charts}
        )
    )
    return 0


def cmd_history_land(_args) -> int:
    from . import history

    def progress(k, n, lo, bars):
        if k % 12 == 0 or k == n:
            print(f"window {k}/{n} from {lo}: {bars:,} bars", flush=True)

    out = history.land(progress=progress)
    print(json.dumps(out, indent=1))
    return 0 if out.get("ok") else 1


def cmd_history_check(args) -> int:
    """The history store against the nightly one where they overlap, and what fifteen years of a
    free dataset should be looked at for before it is trusted."""
    from . import history, tuning_names

    hist, eod = history.connect(), store.connect()
    overlap = history.compare_overlap(hist, eod, sorted(tuning_names.NAMES))
    jumps = {}
    for sym in history.symbols_held(hist):
        found = history.unexplained_jumps(hist, sym)
        if found:
            jumps[sym] = found
    out = {
        "overlap": {
            **overlap,
            "disagree": dict(sorted(overlap["disagree"].items(), key=lambda kv: -kv[1])[:30]),
        },
        "names_with_disagreement": len(overlap["disagree"]),
        "unexplained_jumps": {"names": len(jumps), "jumps": sum(len(v) for v in jumps.values())},
        "jump_examples": dict(list(sorted(jumps.items(), key=lambda kv: -len(kv[1])))[: args.examples]),
    }
    print(json.dumps(out, indent=1))
    return 0


def cmd_study_run(args) -> int:
    from . import study

    tradable, label_day = study.tradable_today()
    result = study.run(
        workers=args.workers, progress=lambda m: print(m, flush=True), tradable=tradable or None
    )
    result["tradable_view"] = {"day": label_day, "names": len(tradable)}
    path = study.write(result)
    rows = []
    for setup_id, v in result["setups"].items():
        d, t = v["describe"], v["test"]
        rows.append(
            {
                "setup": setup_id,
                "verdict": v["verdict"],
                "entries": d.get("entries"),
                "expectancy_r": round(d["expectancy_r"], 3) if d.get("entries") else None,
                "baseline_r": round(d["baseline_r"], 3) if d.get("baseline_r") is not None else None,
                "edge_r": round(t["edge_r"], 3) if "edge_r" in t else None,
                "t": round(t["t"], 2) if "t" in t else None,
                "p": t.get("p"),
                "effective_entries": round(t.get("effective_entries", 0)),
            }
        )
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": rows}, indent=1))
    return 0


def cmd_study_round2(args) -> int:
    """Round 2 (docs/signal-log-plan.md): stage A tests the declared family on half A; stage B
    re-tests only stage A's survivors on half B."""
    from . import hypotheses as hy

    if args.stage == "B":
        a = hy.latest("A")
        if a is None:
            print(json.dumps({"ok": False, "reason": "no stage A result yet"}))
            return 1
        ids = hy.survivors(a)
        if not ids:
            print(json.dumps({"ok": True, "stage": "B", "survivors": [], "note": "nothing passed stage A"}))
            return 0
    else:
        ids = None
    result = hy.run(args.stage, ids=ids, workers=args.workers, progress=lambda m: print(m, flush=True))
    path = hy.write(result)
    summary = []
    for hid, v in result["hypotheses"].items():
        d, t = v["describe"], v["test"]
        summary.append(
            {
                "hypothesis": hid,
                "passed": v["passed"],
                "entries": d.get("entries"),
                "net_r": round(d["expectancy_r"], 3) if d.get("entries") else None,
                "base_setup_net_r": round(v["base_setup_describe"]["expectancy_r"], 3)
                if v["base_setup_describe"].get("entries")
                else None,
                "baseline_r": round(d["baseline_r"], 3) if d.get("baseline_r") is not None else None,
                "edge_r": round(t["edge_r"], 3) if "edge_r" in t else None,
                "t": round(t["t"], 2) if "t" in t else None,
                "p": t.get("p"),
            }
        )
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": summary}, indent=1))
    return 0


def cmd_study_round3(args) -> int:
    """Round 3 (docs/signal-log-plan.md): the two infographic setups, long and short, each against
    the random and the matched baseline, on every name."""
    from . import round3

    result = round3.run(workers=args.workers, progress=lambda m: print(m, flush=True))
    path = round3.write(result)
    summary = []
    for hid, v in result["hypotheses"].items():
        d, t = v["describe"], v["test"]
        summary.append(
            {
                "hypothesis": hid,
                "passed": v["passed"],
                "entries": d.get("entries"),
                "net_r": round(d["expectancy_r"], 3) if d.get("entries") else None,
                "baseline_r": round(d["baseline_r"], 3) if d.get("baseline_r") is not None else None,
                "edge_r": round(t["edge_r"], 3) if "edge_r" in t else None,
                "t": round(t["t"], 2) if "t" in t else None,
                "p": t.get("p"),
                "judged": t.get("judged"),
            }
        )
    verdicts = {k: v["verdict"] for k, v in result["setups"].items()}
    print(
        json.dumps(
            {"path": path, "seconds": result["seconds"], "summary": summary, "verdicts": verdicts}, indent=1
        )
    )
    return 0


def cmd_study_round4(args) -> int:
    """Round 4 (docs/signal-log-plan.md): the relative-strength breakout, with and without its volume
    rule, on the $20M-$300M slice the exploratory look never saw."""
    from . import round4

    result = round4.run(workers=args.workers, progress=lambda m: print(m, flush=True))
    path = round4.write(result)
    summary = []
    for hid, v in result["hypotheses"].items():
        d, t = v["describe"], v["test"]
        summary.append(
            {
                "hypothesis": hid,
                "passed": v["passed"],
                "entries": d.get("entries"),
                "net_r": round(d["expectancy_r"], 3) if d.get("entries") else None,
                "baseline_r": round(d["baseline_r"], 3) if d.get("baseline_r") is not None else None,
                "edge_r": round(t["edge_r"], 3) if "edge_r" in t else None,
                "t": round(t["t"], 2) if "t" in t else None,
                "p": t.get("p"),
                "judged": t.get("judged"),
            }
        )
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": summary}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.technicals", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    la = sub.add_parser("land", help="land bars, splits, dividends and IV from the local Dolt clones")
    la.add_argument(
        "--symbols", nargs="*", help="only these symbols (default: candidates + ETFs + benchmarks)"
    )
    la.set_defaults(fn=cmd_land)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser(
        "liquidity", help="judge every name liquid or not (local data); the illiquid are skipped and hidden"
    ).set_defaults(fn=cmd_liquidity)
    ba = sub.add_parser("bars")
    ba.add_argument("symbol")
    ba.add_argument("--raw", action="store_true")
    ba.add_argument("--last", type=int, default=10)
    ba.set_defaults(fn=cmd_bars)
    cv = sub.add_parser("check-vendor", help="our adjusted bars against every vendor chart capture")
    cv.add_argument("--all", action="store_true", help="list every symbol, not only disagreements")
    cv.set_defaults(fn=cmd_check_vendor)
    st = sub.add_parser("stages", help="the relative-strength stage of every candidate on a session")
    st.add_argument("--session", help="ISO date (default: the latest session stored)")
    st.set_defaults(fn=cmd_stages)
    sub.add_parser("score-stages", help="score the stage rule against every saved edition").set_defaults(
        fn=cmd_score_stages
    )
    ro = sub.add_parser("rotation", help="every rotation fund's state on a session")
    ro.add_argument("--session", help="ISO date (default: the latest session stored)")
    ro.set_defaults(fn=cmd_rotation)
    sr = sub.add_parser("score-rotation", help="score the rotation rule against every saved edition")
    sr.set_defaults(fn=cmd_score_rotation)
    br = sub.add_parser("breadth", help="daily leaders/laggards/net/bullish share over recent sessions")
    br.add_argument("--sessions", type=int, default=10)
    br.set_defaults(fn=cmd_breadth)
    sub.add_parser("score-trends", help="our trend scores against every vendor chart capture").set_defaults(
        fn=cmd_score_trends
    )
    sub.add_parser("score-levels", help="how many vendor levels our grid places").set_defaults(
        fn=cmd_score_levels
    )
    sub.add_parser(
        "score-level-selection", help="where the vendor's levels sit among the grid points, against chance"
    ).set_defaults(fn=cmd_score_level_selection)
    sub.add_parser("score-rank", help="our 1-10 rank against the vendor's").set_defaults(fn=cmd_score_rank)
    sub.add_parser("score-signals", help="our scan rules against every saved scan list").set_defaults(
        fn=cmd_score_signals
    )
    hi = sub.add_parser("history", help="the historical study's store (docs/signal-log-plan.md)")
    hs = hi.add_subparsers(dest="history_cmd", required=True)
    hs.add_parser("land", help="land Dolt's whole daily history into history.db").set_defaults(
        fn=cmd_history_land
    )
    hc = hs.add_parser("check", help="history.db against eod.db, and unexplained price jumps")
    hc.add_argument("--examples", type=int, default=15)
    hc.set_defaults(fn=cmd_history_check)
    sy = sub.add_parser("study", help="the historical study (analysis plan v2)")
    ss = sy.add_subparsers(dest="study_cmd", required=True)
    sr_ = ss.add_parser("run", help="score every setup over the history against its random baseline")
    sr_.add_argument("--workers", type=int, default=14)
    sr_.set_defaults(fn=cmd_study_run)
    s2 = ss.add_parser("round2", help="round 2: the declared improvements, stage A or B")
    s2.add_argument("--stage", choices=("A", "B"), required=True)
    s2.add_argument("--workers", type=int, default=14)
    s2.set_defaults(fn=cmd_study_round2)
    s3 = ss.add_parser("round3", help="round 3: Supertrend + Vortex and squeeze + RSI divergence")
    s3.add_argument("--workers", type=int, default=14)
    s3.set_defaults(fn=cmd_study_round3)
    s4 = ss.add_parser("round4", help="round 4: the relative-strength breakout, with and without volume")
    s4.add_argument("--workers", type=int, default=14)
    s4.set_defaults(fn=cmd_study_round4)
    rp = sub.add_parser("report", help="write one session's market-report readings for the console")
    rp.add_argument("--session", help="ISO date (default: the latest session stored)")
    rp.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
