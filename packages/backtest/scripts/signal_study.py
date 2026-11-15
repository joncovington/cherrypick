"""Run a signal study on the underlying alone (#1179): read the bars, run `signalstudy.study`, write
the result.

    python packages/backtest/scripts/signal_study.py run --request FILE

The request is the JSON object `signalstudy.validate` reads. The bars come from its `source`:

- `history`: technicals' daily bars (`history.db`, 2011 on), split- and dividend-adjusted by
  technicals' own `adjusted_bars`.
- `recorder`: the gex recorder's spot minutes (`gex_history.db`), cut into `bar_minutes` bars.
- `vendor`: the licensed minute store (`SPX-60s.db`), the same way. Its result is licensed.

The expected move reads the vol index's daily closes from the Cboe files on disk
(`market-files/cboe/<INDEX>.csv`); a symbol without an index, or an index without its file, has none.

The result goes to `paths.signals_dir(licensed)/<name>.json`: beside `results/` for public bars and
our own recording, under it for the vendor's. A vendor result also says whether its sessions reach
the configured holdout window (`reads_holdout`), since a descriptive look is not a spend.

Queued as a script row: `run.py queue-add --study signals --step <name> --task script
--task-args '{"path": "packages/backtest/scripts/signal_study.py", "args": ["run", "--request", FILE]}'`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home, jsonio
from cherrypick.core import signals as sg

from cherrypick.backtest import paths, signalstudy
from cherrypick.backtest import settings as _settings

ET = ZoneInfo("America/New_York")
VENDOR_FILE = "SPX-60s.db"


def _ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"no such store: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def history_bars(symbol: str) -> list[sg.Bar]:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    conn = _ro(tpaths.history_db())
    conn.row_factory = sqlite3.Row
    rows = store.adjusted_bars(conn, symbol)
    return sg.daily(
        {"session": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in rows
    )


def minute_bars(db: Path, symbol: str, width: int) -> list[sg.Bar]:
    """Every stored session's spot trail as `width`-minute bars, oldest first."""
    conn = _ro(db)
    trails: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for day, ts, spot in conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history "
        "WHERE symbol = ? AND spot > 0 ORDER BY trade_date, ts",
        (symbol,),
    ):
        t = datetime.fromtimestamp(float(ts), ET)
        trails[str(day)[:10]].append((t.hour * 60 + t.minute, float(spot)))
    out: list[sg.Bar] = []
    for day in sorted(trails):
        out.extend(sg.from_trail(day, trails[day], width))
    return out


def vol_closes(index: str | None) -> dict[str, float] | None:
    """The Cboe index's closes by ISO session, or None when there is no index or no file."""
    if index is None:
        return None
    path = home.data_dir("market-files") / "cboe" / f"{index}.csv"
    if not path.exists():
        return None
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                day = datetime.strptime(row["DATE"], "%m/%d/%Y").date().isoformat()
                out[day] = float(row["CLOSE"])
            except (KeyError, ValueError):
                continue
    return out


def load(request: dict) -> tuple[list[sg.Bar], dict]:
    source = request["source"]
    symbol = request["symbol"]
    if source == "history":
        return history_bars(symbol), {"store": "technicals/history.db"}
    if source == "recorder":
        return minute_bars(home.data_dir("gex") / "gex_history.db", symbol, request["bar_minutes"]), {
            "store": "gex/gex_history.db"
        }
    cfg = _settings.load()
    bars = minute_bars(paths.vendor_dir(cfg) / VENDOR_FILE, symbol, request["bar_minutes"])
    holdout = (cfg.get("windows") or {}).get("holdout")
    span = [b.session for b in bars if signalstudy.in_span(request, b.session)]
    reads = bool(holdout and span and span[0] <= holdout[1] and span[-1] >= holdout[0])
    return bars, {"store": f"backtest/vendor/{VENDOR_FILE}", "reads_holdout": reads}


def cmd_run(args) -> int:
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    try:
        signalstudy.validate(request)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    bars, source = load(request)
    if not bars:
        print(json.dumps({"ok": False, "error": f"no bars for {request['symbol']} in {request['source']}"}))
        return 1
    index = signalstudy.EM_INDEX.get(request["symbol"])
    vol = vol_closes(index)
    result = signalstudy.study(request, bars, vol=vol)
    result["source"] = {**source, "em_file": f"market-files/cboe/{index}.csv" if vol else None}
    result["generated_at"] = datetime.now(ET).isoformat(timespec="seconds")
    out = paths.signals_dir(result["licensed"]) / f"{request['name']}.json"
    jsonio.write_json_atomic(out, result)
    print(json.dumps({"ok": True, "path": str(out), "firings": result["firings_n"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="run one request and write its result")
    rn.add_argument("--request", required=True, help="the request's JSON file")
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
