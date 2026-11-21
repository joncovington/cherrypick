"""Item 101's context run: the same wheel arms on names the declared universe leaves out.

A sibling of `wheel_cycle_item101.py` (the declared study, which this file loads and never edits, so
the declared run and its stamps are untouched). It runs that script's arms, calls, costs, earnings
rule, half-day rule and point-in-time spread check, on the symbols given with `--symbols`, from each
name's first session with a stored monthly chain (or the declared entry start, whichever is later).
The one thing it leaves out is the universe's coverage floor (`universe.min_sessions`), which is why
a name that listed late is not in the declared run.

It is context and nothing else: no kit RULE, no Holm, no half-rule, no holdout is applied, and the
result says `"verdict": "context, not judged"`. It writes only files named `extra-<SYMS>*.json` under
the study's results folder and never `results.json`, `smoke.json`, `path_requests.json`,
`universe.json` or `earnings_dates.json`. Results are derived from licensed data and stay under the
backtest results directory, never the repo.

- `plan --symbols SYM[,SYM]`: Dolt's report calendar for the names and the chain-path requests they
  need (`extra-<SYMS>-earnings_dates.json`, `extra-<SYMS>-path_requests.json`).
- `study --symbols SYM[,SYM]`: the arms on those names, per-name rows and the per-name `m45-40` less
  `m45-40-tp50` difference, in `extra-<SYMS>.json`.

A report date Dolt does not list leaves the quarter unknown and nothing is entered there (unknown is
never clear); the study counts the holes. Nasdaq's calendar is not asked.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import progress as core_progress

from cherrypick.backtest.study import calendars as kit_calendars
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

_BASE = Path(__file__).resolve().parent / "wheel_cycle_item101.py"


def _load_base():
    spec = importlib.util.spec_from_file_location("wheel_cycle_item101", _BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()

#: The declared run's own files: never written here.
PROTECTED = frozenset(
    {"results.json", "smoke.json", "path_requests.json", "universe.json", "earnings_dates.json"}
)
VERDICT = "context, not judged"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,5}$")
#: Reported beside each name: the sessions' earnings state is read over this many days from each.
HOLE_SPAN_DAYS = 45


# ------------------------------------------------------------------------------------------------
# Pure: names, files, requests, starts, holes.


def parse_symbols(text: str) -> list[str]:
    """`SOFI,uber` -> ['SOFI', 'UBER']: sorted, unique, each a plain ticker."""
    out = sorted({s.strip().upper() for s in (text or "").split(",") if s.strip()})
    if not out:
        raise SystemExit("--symbols needs at least one symbol")
    bad = [s for s in out if not SYMBOL.match(s)]
    if bad:
        raise SystemExit(f"not a plain ticker: {', '.join(bad)}")
    return out


def tag_of(symbols: list[str]) -> str:
    return "-".join(symbols)


def checked_path(path: Path) -> Path:
    """The only files this script may write are `extra-*.json`, never one of the declared run's."""
    p = Path(path)
    if p.name in PROTECTED or not (p.name.startswith("extra-") and p.name.endswith(".json")):
        raise SystemExit(f"refusing to write {p.name}: the context run writes extra-<SYMS>*.json only")
    return p


def write_json(path: Path, doc: dict, **kw) -> Path:
    p = checked_path(path)
    p.write_text(json.dumps(doc, indent=1, **kw), encoding="utf-8")
    return p


def requests_for(symbol: str, first: date) -> list[dict]:
    """The declared study's requests for one name, from `first` (never before the declared entry
    start): each monthly's path from 66 days before it, clipped to start no earlier than `first`."""
    out = []
    for r in base.requests([symbol]):
        start = max(date.fromisoformat(r["start"]), first)
        if start > date.fromisoformat(r["through"]):
            continue
        out.append({**r, "start": start.isoformat()})
    return out


def first_stored(inputs, conn, symbol: str) -> date | None:
    """The first session the store holds a chain of any standard monthly for the name."""
    first, run_end = (date.fromisoformat(base.SPEC[k]) for k in ("entry_start", "run_end"))
    found = []
    for e in kit_calendars.monthlies_between(first, run_end + timedelta(days=70)):
        try:
            found.extend(inputs.sessions(conn, symbol, e.isoformat()))
        except KeyError:
            continue
    return date.fromisoformat(min(found)) if found else None


def earnings_holes(reports: list[date], days: list[date]) -> dict:
    """What the report calendar leaves unknown: reports on file, gaps over the declared limit between
    consecutive ones, and the sessions whose next 45 days read `unknown` (never clear)."""
    rs = sorted(reports)
    gaps = [
        {"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days}
        for a, b in zip(rs, rs[1:], strict=False)
        if (b - a).days > base.REPORT_GAP_DAYS
    ]
    entry_end = date.fromisoformat(base.SPEC["entry_end"])
    sessions = [d for d in days if d <= entry_end]
    unknown = sum(
        1 for d in sessions if base.earnings_state(rs, d, d + timedelta(days=HOLE_SPAN_DAYS)) == "unknown"
    )
    return {
        "reports": len(rs),
        "first": rs[0].isoformat() if rs else None,
        "last": rs[-1].isoformat() if rs else None,
        "gaps_over_limit": gaps,
        "sessions": len(sessions),
        "sessions_unknown": unknown,
    }


# ------------------------------------------------------------------------------------------------
# The run.


def context(inputs, conn, data: dict, rep, rate) -> dict:
    """The arms on each name in `data` (symbol -> NameData), from its start, unjudged."""
    run_end = date.fromisoformat(base.SPEC["run_end"])
    entry_start = date.fromisoformat(base.SPEC["entry_start"])
    starts: dict[str, date] = {}
    for sym, nd in data.items():
        stored = first_stored(inputs, conn, sym)
        if stored is None:
            raise SystemExit(f"no stored chain for {sym}: fetch its path requests first")
        starts[sym] = max(stored, entry_start, nd.days[0])
        nd.days = [d for d in nd.days if d >= starts[sym]]
    by = {s: len(base.ARMS) * sum(1 for d in nd.days if d <= run_end) for s, nd in data.items()}
    rep.phase("sessions", total=sum(by.values()), unit="sessions", by=by)
    results: dict[str, dict] = {}
    cover: dict[str, dict] = {}
    dropped: dict[str, int] = defaultdict(int)
    for sym, nd in data.items():
        cover[sym] = base.coverage(inputs, conn, sym, nd.days)
        results[sym] = {}
        for arm in base.ARMS:
            res = base.run_arm(
                nd,
                arm,
                rate,
                tick=lambda d, s=sym, a=arm: rep.advance("sessions", key=s, current=f"{s} {a} {d}"),
            )
            kept = [
                r for r in res["rows"] if cover[sym].get(r["entry"][:4], 0.0) >= base.SPEC["coverage_floor"]
            ]
            dropped[f"{sym} {arm}"] += len(res["rows"]) - len(kept)
            res["rows"] = kept
            results[sym][arm] = res
        nd._chains.clear()
    by_arm = {a: [r for s in results for r in results[s][a]["rows"]] for a in base.ARMS}
    tables = base.name_table(results)
    return {
        "verdict": VERDICT,
        "not_applied": ["the kit RULE", "Holm", "the half-rule", "the holdout"],
        "symbols": list(data),
        "starts": {s: d.isoformat() for s, d in starts.items()},
        "per_name": tables,
        # The design/holdout split inside is the declared date cut, shown as context only.
        "reported": base.report_beside(by_arm, tables),
        "expire_vs_half_credit": base.expire_vs_tp50(tables),
        "coverage": cover,
        "rows_below_coverage_floor": {k: v for k, v in dropped.items() if v},
        "earnings": {s: earnings_holes(nd.reports, nd.days) for s, nd in data.items()},
    }


# ------------------------------------------------------------------------------------------------
# The commands.


def _meta_path(d: Path, tag: str) -> Path:
    return checked_path(d / f"extra-{tag}-earnings_dates.json")


def cmd_plan(args) -> int:
    from cherrypick.technicals import liquidity, store
    from cherrypick.technicals import paths as tpaths

    syms = parse_symbols(args.symbols)
    tag, d = tag_of(syms), base.out_dir()
    hist = store.connect(tpaths.history_db())
    firsts = {}
    for s in syms:
        days = [date.fromisoformat(b.date) for b in store.raw_bars(hist, s) if b.close]
        if not days:
            raise SystemExit(f"no price history for {s}")
        firsts[s] = max(min(days), date.fromisoformat(base.SPEC["entry_start"]))
    tradable = liquidity.tradable_names(liquidity.load()) | liquidity.reference_tradable(
        liquidity.load_reference()
    )
    stocks = [s for s in syms if s not in base.FUNDS]
    write_json(
        _meta_path(d, tag),
        {
            "source": "Dolt earnings_calendar",
            "dates": base.fetch_reports(stocks) if stocks else {},
            "first_price_session": {s: firsts[s].isoformat() for s in syms},
            "tradable": {s: s in tradable for s in syms},
        },
    )
    reqs = [r for s in syms for r in requests_for(s, firsts[s])]
    path = write_json(
        Path(args.out) if args.out else d / f"extra-{tag}-path_requests.json", {"requests": reqs}
    )
    print(json.dumps({"written": str(path), "names": len(syms), "requests": len(reqs)}))
    return 0


def cmd_study(args) -> int:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    syms = parse_symbols(args.symbols)
    tag, d = tag_of(syms), base.out_dir()
    meta = json.loads(_meta_path(d, tag).read_text(encoding="utf-8"))
    inputs = Inputs(d / f"extra-{tag}-path_requests.json")
    conn = base.chain_store()
    hist = store.connect(tpaths.history_db())
    try:
        data = {
            s: base.load_name(
                inputs, conn, hist, s, [date.fromisoformat(x) for x in meta["dates"].get(s, [])]
            )
            for s in syms
        }
        with core_progress.Reporter(f"item101-context-{tag}") as rep:
            block = context(inputs, conn, data, rep, base.rate_reader())
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 101,
        "context": True,
        **block,
        "tradable": meta.get("tradable"),
        "inputs": read,
        "corrections": list(REPLAY_CORRECTIONS),
    }
    path = write_json(Path(args.out) if args.out else d / f"extra-{tag}.json", doc, default=str)
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--symbols", required=True, help="SYM[,SYM]: names the declared universe leaves out")
        p.add_argument("--out", default=None, help="the output file; must be named extra-*.json")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
