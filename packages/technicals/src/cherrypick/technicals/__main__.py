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
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import home as _home

from . import land as _land
from . import paths, store, vendor_check

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


def cmd_entry_context(args) -> int:
    """Every closed trade in a ledger with its B4 entry context (entrycontext.py): local data only."""
    rows = _context_rows(args.db, args.schema, args.arm)
    text = json.dumps(rows, indent=1, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{len(rows)} rows -> {args.out}")
    else:
        print(text)
    return 0


def _context_rows(db: str, schema: str, arm: str | None) -> list[dict]:
    from cherrypick.core import killcriteria

    from . import entrycontext, fundamentals

    records = killcriteria.evidence_records(Path(db), schema)
    if arm:
        records = [r for r in records if r.get("arm") == arm]
    conn = store.connect()
    sectors = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    return entrycontext.annotate(records, lambda s: store.adjusted_bars(conn, s), sectors)


def cmd_slices(args) -> int:
    """B5: one arm's in-sample slice report, for a month (`--month`) or all of it; local data only.
    Written under `data/technicals/slices/` unless `--out` says otherwise."""
    from cherrypick.core import slices

    rows = _context_rows(args.db, args.schema, args.arm)
    if args.month:
        rows = [r for r in rows if str(r.get("session") or "").startswith(args.month)]
    label = f"{args.schema}/{args.arm or 'all'}/{args.month or 'all'}"
    report = slices.report(rows, label=label)
    out = args.out or (paths.data_dir() / "slices" / f"{label.replace('/', '_')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{report['trades']} trades, {report['cells']} cells ({report['ranked_cells']} ranked) -> {out}")
    return 0


def cmd_liquidity(_args) -> int:
    """Tonight's liquidity verdicts (liquidity.py): local data only."""
    from . import liquidity

    report = liquidity.build()
    for key in ("newly_illiquid", "readmitted"):
        if len(report[key]) > 30:
            report[key] = report[key][:30] + [f"... {len(report[key]) - 30} more"]
    print(json.dumps(report, indent=1))
    return 0


def cmd_securities(args) -> int:
    """The public security list and the pointer naming the current one (securities.py)."""
    from . import securities

    report = securities.build(args.session)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_stage_lists(args) -> int:
    """Our six stage lists, tradable names only, scored against the vendor's (stagelists.py)."""
    from . import stagelists

    report = stagelists.build(args.session)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


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


def cmd_board(args) -> int:
    from . import board

    doc = board.build(args.session)
    if not doc.get("ok"):
        print(json.dumps(doc))
        return 1
    path = board.write(doc)
    listed = [r for r in doc["rows"] if r["side"]]
    print(
        json.dumps(
            {
                "ok": True,
                "path": path,
                "session": doc["session"],
                "names": doc["names"],
                "bullish": sum(r["side"] == "bullish" for r in listed),
                "bearish": sum(r["side"] == "bearish" for r in listed),
                "revisions_through": doc["revisions_through"],
            },
            indent=1,
        )
    )
    return 0


def cmd_score_board(_args) -> int:
    from . import board

    print(json.dumps(board.score(), indent=1))
    return 0


def cmd_score_revisions(_args) -> int:
    from . import editions, revisions

    boards = editions.load_boards()
    since = min((date.fromisoformat(s) for s in boards), default=None)
    events = revisions.load(since=since - timedelta(days=7) if since else None)
    if not events:
        print(json.dumps({"ok": False, "reason": "no events; run scripts/fetch_analyst_revisions.py"}))
        return 1
    print(json.dumps(revisions.score_boards(events, boards), indent=1))
    return 0


def cmd_score_bearing(_args) -> int:
    from . import compass

    result = compass.score_across_days(compass.load_snapshots())
    print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


def cmd_score_screeners(args) -> int:
    from . import screeners

    print(json.dumps(screeners.score_all(rescore=args.rescore), indent=1))
    return 0


def cmd_seed_universe(args) -> int:
    """Show (or with --write, rewrite) the shipped bootstrap universe (securities.SEED_UNIVERSE)."""
    from . import securities

    if args.write:
        report = securities.write_seed()
    else:
        names, source = securities.liquid_optionable()
        report = {
            "ok": True,
            "source": source,
            "symbols": len(names),
            "seed": len(securities.seed_universe()),
        }
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_income_lists(args) -> int:
    """Our own short-put and covered-call lists from the afternoon income chains (incomelists.py)."""
    from pathlib import Path

    from . import incomelists

    report = incomelists.build(day=args.day, source=Path(args.source) if args.source else None)
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_skate(args) -> int:
    """The skate screen's bands and their history (skate.py): the history store and local files only."""
    from . import skate

    result = skate.build(day=args.day, cfg=_config().get("skate"))
    # The forward record rides the same job (plan step 4): yesterday's shown rows recorded, expired ones
    # settled from the bars this build just read. Its failure is reported and never fails the bands.
    try:
        from . import skate_record

        rec = skate_record.run()
        result["record"] = {k: rec[k] for k in ("recorded", "added", "settled_now")} | {
            "decision_grade": rec["verdict"]["decision_grade"],
            "expiries": rec["verdict"]["expiries"],
        }
    except Exception as exc:  # noqa: BLE001 -- the record is a reading, the bands are the product
        result["record"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


def cmd_skate_record(args) -> int:
    """The skate screen's forward record alone (skate_record.py): record, settle, read the verdict."""
    from . import skate_record

    print(json.dumps(skate_record.run(through=args.through), indent=1))
    return 0


def cmd_earnings_calendar(args) -> int:
    """The earnings calendar for a session (earnings_calendar.py): local data and the local Dolt only."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from cherrypick.core import calendar as cal

    from . import earnings_calendar

    if args.day:
        day = args.day
    else:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        day = (today if cal.is_trading_day(today) else cal.previous_trading_day(today)).isoformat()
    cfg = _config()
    try:
        result = earnings_calendar.build(day, cfg.get("dolt") or {}, cfg.get("earnings_calendar") or {})
    except Exception as exc:  # noqa: BLE001 -- no Dolt is a failed run, reported, not a traceback
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    report = {"ok": True, **earnings_calendar.summary(result)}
    if not args.dry_run:
        report["written"] = str(earnings_calendar.write(result))
    print(json.dumps(report, indent=1))
    return 0


def cmd_earnings_record(args) -> int:
    """The earnings calendar's forward record (earnings_record.py): local files and the local Dolt."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from . import earnings_record

    today = (
        date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo("America/New_York")).date()
    )
    try:
        result = earnings_record.build(today, _config().get("dolt") or {})
    except Exception as exc:  # noqa: BLE001 -- no Dolt is a failed run, reported, not a traceback
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    report = {"ok": True, "as_of": result["as_of"], **result["summary"]}
    if not args.dry_run:
        report["written"] = str(earnings_record.write(result))
    print(json.dumps(report, indent=1))
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


def cmd_score_levels(args) -> int:
    from . import chart_score

    print(json.dumps(chart_score.score_levels(every=args.every, sessions=args.sessions), indent=1))
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


def cmd_history_fundamentals(_args) -> int:
    """Round 5's inputs: Dolt's next-year EPS and sales estimates and quarterly income statements."""
    from . import fundamentals

    def progress(k, n, lo, rows):
        if k % 12 == 0 or k == n:
            print(f"window {k}/{n} from {lo}: {rows:,} estimate rows", flush=True)

    out = fundamentals.land(progress=progress)
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
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": _tests(result)}, indent=1))
    return 0


def cmd_study_round5(args) -> int:
    """Round 5 (docs/signal-log-plan.md): the 1-10 score inside the fundamentals label, against the
    same label and trigger at the other scores."""
    from . import round5

    result = round5.run(workers=args.workers, progress=lambda m: print(m, flush=True))
    path = round5.write(result)
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": _tests(result)}, indent=1))
    return 0


def cmd_study_round6(args) -> int:
    """Round 6 (docs/signal-log-plan.md): an analyst revision inside the trend+RS direction, against
    the same direction without one. `--count` reports frequencies only and scores nothing."""
    from . import round6

    progress = lambda m: print(m, flush=True)  # noqa: E731
    if args.count:
        b = round6.build(workers=args.workers, progress=progress)
        b["pool"].shutdown()
        print(json.dumps(round6.counts(b), indent=1))
        return 0
    result = round6.run(workers=args.workers, progress=progress)
    path = round6.write(result)
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": _tests(result)}, indent=1))
    return 0


def cmd_study_round7(args) -> int:
    """Round 7 (docs/signal-log-plan.md): entering a bearing, against random entry. `--count`
    reports frequencies only and scores nothing."""
    from . import round7

    progress = lambda m: print(m, flush=True)  # noqa: E731
    if args.count:
        b = round7.build(workers=args.workers, progress=progress)
        b["pool"].shutdown()
        print(json.dumps(round7.counts(b), indent=1))
        return 0
    result = round7.run(workers=args.workers, progress=progress)
    path = round7.write(result)
    print(json.dumps({"path": path, "seconds": result["seconds"], "summary": _tests(result)}, indent=1))
    return 0


def _tests(result: dict) -> list[dict]:
    """One line per declared test: verdict, size, net R, baseline, edge and its t."""
    out = []
    for hid, v in result["hypotheses"].items():
        d, t = v["describe"], v["test"]
        out.append(
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
    return out


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
    ec = sub.add_parser("entry-context", help="every closed trade in a ledger, with its entry context (B4)")
    ec.add_argument("--db", required=True, help="a module's ledger, or a replay's directory of them")
    ec.add_argument("--schema", required=True, help="its trade schema (meic_ic, bwb_132, ...)")
    ec.add_argument("--arm", help="one arm only")
    ec.add_argument("--out", type=Path, help="write the rows as JSON here instead of printing")
    ec.set_defaults(fn=cmd_entry_context)
    sl = sub.add_parser("slices", help="one arm's in-sample slice report by setup and condition (B5)")
    sl.add_argument("--db", required=True, help="a module's ledger, or a replay's directory of them")
    sl.add_argument("--schema", required=True)
    sl.add_argument("--arm")
    sl.add_argument("--month", help="YYYY-MM: that month's closed trades only")
    sl.add_argument("--out", type=Path)
    sl.set_defaults(fn=cmd_slices)
    se = sub.add_parser(
        "securities",
        help="the public security list (local files), and which list -- it or the vendor's -- is current",
    )
    se.add_argument("--session", help="the session to build for (default today)")
    se.set_defaults(fn=cmd_securities)
    sl = sub.add_parser(
        "stage-lists",
        help="our six stage lists on one close, options-tradable names only, scored against the vendor's",
    )
    sl.add_argument("--session", help="the close to build for (default the newest the store holds)")
    sl.set_defaults(fn=cmd_stage_lists)
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
    sub.add_parser(
        "score-revisions", help="score the analyst-revision rule against every saved edition's board"
    ).set_defaults(fn=cmd_score_revisions)
    sub.add_parser(
        "score-bearing", help="the bearing lookup fitted on earlier security lists, scored on the latest"
    ).set_defaults(fn=cmd_score_bearing)
    ss = sub.add_parser(
        "score-screeners", help="the income screens' expiry and strike rules, scored on every saved list"
    )
    ss.add_argument("--rescore", action="store_true", help="score sessions already scored again")
    ss.set_defaults(fn=cmd_score_screeners)
    il = sub.add_parser(
        "income-lists", help="our own short-put and covered-call lists, from the afternoon income chains"
    )
    il.add_argument("--day", help="the newest reading on or before this day (YYYY-MM-DD); default today")
    il.add_argument("--source", help="build from this income file instead (written as a .trial.json)")
    il.set_defaults(fn=cmd_income_lists)
    su = sub.add_parser(
        "seed-universe", help="the income lists' universe source; --write rewrites the shipped seed"
    )
    su.add_argument(
        "--write", action="store_true", help="rewrite seed_universe.json from the newest public list"
    )
    su.set_defaults(fn=cmd_seed_universe)
    sk = sub.add_parser(
        "skate", help="the skate screen's range bands and how often each has held, per name and expiry"
    )
    sk.add_argument("--day", help="the session (YYYY-MM-DD); default the history store's newest")
    sk.set_defaults(fn=cmd_skate)
    sr = sub.add_parser(
        "skate-record",
        help="the skate screen's forward record: what it showed, what happened, the declared test",
    )
    sr.add_argument("--through", help="record sessions up to this one (YYYY-MM-DD); default every one")
    sr.set_defaults(fn=cmd_skate_record)
    ec = sub.add_parser(
        "earnings-calendar", help="the next 30 days' announcements, as the earnings tab shows them"
    )
    ec.add_argument("--day", help="the session (YYYY-MM-DD); default today's, or the last one")
    ec.add_argument("--dry-run", action="store_true", help="build and summarise, write nothing")
    ec.set_defaults(fn=cmd_earnings_calendar)
    er = sub.add_parser(
        "earnings-record", help="what each past announcement did against what the calendar said"
    )
    er.add_argument("--today", help="as of this date (YYYY-MM-DD); default today, New York")
    er.add_argument("--dry-run", action="store_true", help="build and summarise, write nothing")
    er.set_defaults(fn=cmd_earnings_record)
    sr = sub.add_parser("score-rotation", help="score the rotation rule against every saved edition")
    sr.set_defaults(fn=cmd_score_rotation)
    br = sub.add_parser("breadth", help="daily leaders/laggards/net/bullish share over recent sessions")
    br.add_argument("--sessions", type=int, default=10)
    br.set_defaults(fn=cmd_breadth)
    sub.add_parser("score-trends", help="our trend scores against every vendor chart capture").set_defaults(
        fn=cmd_score_trends
    )
    sl = sub.add_parser("score-levels", help="how many vendor levels our grid places")
    sl.add_argument("--every", action="store_true", help="every capture on file, not each name's newest")
    sl.add_argument(
        "--sessions", type=int, help="this many sessions instead of the calendar year (old rule: 250)"
    )
    sl.set_defaults(fn=cmd_score_levels)
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
    hs.add_parser("fundamentals", help="land round 5's estimates and income statements").set_defaults(
        fn=cmd_history_fundamentals
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
    s5 = ss.add_parser("round5", help="round 5: the 1-10 score inside the fundamentals label")
    s5.add_argument("--workers", type=int, default=14)
    s5.set_defaults(fn=cmd_study_round5)
    s6 = ss.add_parser("round6", help="round 6: an analyst revision inside the trend+RS direction")
    s6.add_argument("--workers", type=int, default=14)
    s6.add_argument("--count", action="store_true", help="frequencies only: entries and pools, no scoring")
    s6.set_defaults(fn=cmd_study_round6)
    s7 = ss.add_parser("round7", help="round 7: entering a bearing, against random entry")
    s7.add_argument("--workers", type=int, default=14)
    s7.add_argument("--count", action="store_true", help="frequencies only: entries and pools, no scoring")
    s7.set_defaults(fn=cmd_study_round7)
    rp = sub.add_parser("report", help="write one session's market-report readings for the console")
    rp.add_argument("--session", help="ISO date (default: the latest session stored)")
    rp.set_defaults(fn=cmd_report)
    bp = sub.add_parser(
        "board", help="write one session's signal board: each name's count of agreeing methods"
    )
    bp.add_argument("--session", help="ISO date (default: the latest session stored)")
    bp.set_defaults(fn=cmd_board)
    sub.add_parser(
        "score-board", help="the signal board's methods against every saved edition's board"
    ).set_defaults(fn=cmd_score_board)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
