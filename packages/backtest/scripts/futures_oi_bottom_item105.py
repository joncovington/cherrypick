"""Item 105: in an S&P drawdown, does falling E-mini open interest mean the low is in?

The declaration is research/edge-hypotheses-plan.md section 105; this script runs it. The data is
public: the CFTC's weekly Traders in Financial Futures report for the E-mini S&P 500 (fetched by the
`fetch` step) and Cboe's daily SPX closes, which `scripts/fetch_market_files.py` already keeps. Both
are personal use; the fetched files and the results stay under the backtest results directory, never
the repo.

A forecast study, not a trade. Each weekly report taken while the S&P is in a drawdown is a row. A
row is a signal week when open interest (or the leveraged funds' net short share of it) has fallen
far enough from its peak in the same drawdown. The outcome is whether the drawdown's lowest close so
far still stands 63 sessions later. The statistic is the signal weeks' mean of that outcome less a
baseline from the drawdown's other weeks, matched on how long the drawdown has run and how far price
has already bounced. The pass rule is `FORECAST_RULE`.

- `fetch`: the CFTC's yearly files into `{results}/cot/`, the E-mini rows only.
- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (one word per signal).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import sys
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import progress as core_progress

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run.
FORECAST_RULE = {
    "kind": "forecast",
    "market_code": "13874A",
    "from": "2006-06-13",
    "through": "2026-11-17",
    "high_window": 252,
    "drawdown_enter": 0.07,
    "drawdown_exit": 0.03,
    "swell_days": 13,
    "cycle_reset": True,
    "signals": {"oi_fall": 0.03, "lev_short_fall": 0.03},
    "horizon": 63,
    "age_split": 5,
    "bounce_cuts": [0.02, 0.05],
    "statistic": "excess_held",
    "t_bar": 2.24,
    "min_effect": 0.10,
    "min_n": 15,
    "min_episodes": 4,
    "holdout_min_n": 8,
    "bootstrap": {"block": 13, "resamples": 10000, "seed": 105},
    "splits": ["2019-01-01"],
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
CHAIN = (
    {"step": "fetch", "kind": "script", "args": ["fetch"], "output": "{results}/cot/manifest.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
SIGNALS = ("oi_fall", "lev_short_fall")
#: The thresholds reported beside each declared one, never judged.
REPORTED_THRESHOLDS = {"oi_fall": (0.02, 0.05), "lev_short_fall": (0.02, 0.05)}

CFTC_BASE = "https://www.cftc.gov/files/dea/history/"
CFTC_FIRST = "fin_fut_txt_2006_2016.zip"  # 2006-06 to 2016 in one file; yearly files from 2017
CFTC_YEARLY = "fut_fin_txt_{year}.zip"
UA = "cherrypick-cot/1.0"
COLUMNS = (
    "As_of_Date_In_Form_YYMMDD",
    "CFTC_Contract_Market_Code",
    "Open_Interest_All",
    "Lev_Money_Positions_Long_All",
    "Lev_Money_Positions_Short_All",
)


# --------------------------------------------------------------------------- pure pieces


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def expiry_on_or_after(d: date) -> date:
    """The quarterly E-mini expiry (third Friday of March, June, September, December) on or after `d`."""
    for k in range(5):
        y, m = d.year + (d.month - 1 + 3 * k) // 12, (d.month - 1 + 3 * k) % 12 + 1
        q = ((m - 1) // 3 + 1) * 3
        e = third_friday(y, q)
        if e >= d:
            return e
    raise ValueError(d)


def is_swell(d: date, swell_days: int) -> bool:
    """A report taken in the roll window: within `swell_days` before its quarter's expiry, when both
    contracts are open and the aggregate open interest swells with roll spreads."""
    return (expiry_on_or_after(d) - d).days <= swell_days


def count(field: str) -> int:
    """A contract count; the 2006-2016 file writes them as decimals (`2754179.000000`)."""
    return round(float(field))


def parse_cot(text: str, code: str) -> list[dict]:
    """The E-mini rows of one CFTC text file: as-of date, open interest, leveraged funds long and short."""
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader)]
    idx = {c: header.index(c) for c in COLUMNS}
    out = []
    for row in reader:
        if len(row) <= max(idx.values()) or row[idx["CFTC_Contract_Market_Code"]].strip() != code:
            continue
        ymd = row[idx["As_of_Date_In_Form_YYMMDD"]].strip()
        out.append(
            {
                "d": date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])).isoformat(),
                "oi": count(row[idx["Open_Interest_All"]]),
                "lev_long": count(row[idx["Lev_Money_Positions_Long_All"]]),
                "lev_short": count(row[idx["Lev_Money_Positions_Short_All"]]),
            }
        )
    return out


def merge_reports(parts: list[list[dict]]) -> list[dict]:
    """One row per as-of date, in date order; a date in two files keeps the later file's row."""
    by_date: dict[str, dict] = {}
    for rows in parts:
        for r in rows:
            by_date[r["d"]] = r
    return [by_date[d] for d in sorted(by_date)]


def parse_spx(text: str) -> list[tuple[str, float]]:
    """Cboe's SPX history file (`DATE,SPX`, M/D/YYYY): (iso date, close) in date order."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2 or not parts[1]:
            continue
        c = float(parts[1])
        if c > 0:
            out.append((datetime.strptime(parts[0], "%m/%d/%Y").date().isoformat(), c))
    out.sort()
    return out


def bounce_bucket(bounce: float, cuts: list[float]) -> int:
    return sum(1 for c in cuts if bounce >= c)


def build_rows(reports: list[dict], spx: list[tuple[str, float]], rule: dict) -> tuple[list[dict], dict]:
    """One row per report taken in a drawdown, with its signals and outcome, and the counts dropped.

    A report reads the last SPX close on or before its as-of date. A drawdown begins at the first
    report at least `drawdown_enter` under the highest close of the prior `high_window` sessions and
    ends at the first report less than `drawdown_exit` under it. Price is read at the reference
    session, the first session after the Friday release (as-of + 3 days), so nothing is used before it
    was published. The drawdown's low so far is the lowest close from its high through the reference
    session; `held` is whether no close in the next `horizon` sessions goes under it.

    Signals, on reports outside the roll window only: `oi_fall` when open interest is at least the
    declared fraction under its highest reading in the same drawdown and the same quarterly cycle;
    `lev_short_fall` when the leveraged funds' net short share of open interest is at least the
    declared amount (in points of share) under its highest reading in the same drawdown, that peak
    being a net short."""
    days = [d for d, _ in spx]
    closes = [c for _, c in spx]
    w, h = rule["high_window"], rule["horizon"]
    sig = rule["signals"]
    rows: list[dict] = []
    dropped = {"history": 0, "horizon": 0, "outside": 0, "swell": 0}
    episode = None  # {"id", "hi_idx", "age", "oi_peak": {cycle: oi}, "ns_peak"}
    n_episode = 0
    j = 0
    for rpt in reports:
        d = rpt["d"]
        if d < rule["from"] or d > rule["through"]:
            continue
        while j + 1 < len(days) and days[j + 1] <= d:
            j += 1
        if days[j] > d or j < w:
            dropped["history"] += 1
            continue
        window = closes[j - w : j + 1]
        hi = max(window)
        depth = 1 - closes[j] / hi
        if episode is None and depth >= rule["drawdown_enter"]:
            n_episode += 1
            hi_idx = j - w + window.index(hi)
            episode = {"id": n_episode, "hi_idx": hi_idx, "age": 0, "oi_peak": {}, "ns_peak": None}
        elif episode is not None and depth < rule["drawdown_exit"]:
            episode = None
        if episode is None:
            dropped["outside"] += 1
            continue
        episode["age"] += 1
        dd = date.fromisoformat(d)
        swell = is_swell(dd, rule["swell_days"])
        cycle = expiry_on_or_after(dd).isoformat() if rule["cycle_reset"] else "episode"
        ns = (rpt["lev_short"] - rpt["lev_long"]) / rpt["oi"]
        sig_oi = sig_ns = False
        if not swell:
            peak = max(episode["oi_peak"].get(cycle, 0), rpt["oi"])
            episode["oi_peak"][cycle] = peak
            sig_oi = rpt["oi"] <= (1 - sig["oi_fall"]) * peak
            ns_peak = ns if episode["ns_peak"] is None else max(episode["ns_peak"], ns)
            episode["ns_peak"] = ns_peak
            sig_ns = ns_peak > 0 and ns <= ns_peak - sig["lev_short_fall"]
        release = (dd + timedelta(days=3)).isoformat()
        r = next((k for k in range(j, len(days)) if days[k] > release), None)
        if r is None or r + max(h, 20) >= len(days):
            dropped["horizon"] += 1
            continue
        if swell:
            dropped["swell"] += 1
            continue
        low = min(closes[episode["hi_idx"] : r + 1])
        ahead = closes[r + 1 : r + h + 1]
        rows.append(
            {
                "date": d,
                "ref": days[r],
                "episode": episode["id"],
                "age": episode["age"],
                "depth": depth,
                "bounce": closes[r] / low - 1,
                "held": min(ahead) >= low,
                "held_ref": min(ahead) >= closes[r],
                "ret_20": closes[r + 20] / closes[r] - 1,
                "ret_63": closes[r + h] / closes[r] - 1,
                "oi": rpt["oi"],
                "ns": ns,
                "oi_fall": sig_oi,
                "lev_short_fall": sig_ns,
            }
        )
    return rows, dropped


def cell(row: dict, rule: dict) -> tuple[bool, int]:
    return row["age"] > rule["age_split"], bounce_bucket(row["bounce"], rule["bounce_cuts"])


def excess(rows: list[dict], signal: str, rule: dict, outcome: str = "held") -> dict:
    """The signal weeks' mean outcome less each one's baseline: the share of non-signal drawdown weeks
    in its cell (age past `age_split` or not, bounce bucket) whose outcome held. A signal week whose
    cell has no baseline week is left out."""
    base: dict[tuple, list[int]] = {}
    for r in rows:
        if not r[signal]:
            base.setdefault(cell(r, rule), []).append(1 if r[outcome] else 0)
    diffs, hits, ps, episodes = [], [], [], set()
    for r in rows:
        if not r[signal]:
            continue
        b = base.get(cell(r, rule))
        if not b:
            continue
        p = sum(b) / len(b)
        diffs.append((1.0 if r[outcome] else 0.0) - p)
        hits.append(1.0 if r[outcome] else 0.0)
        ps.append(p)
        episodes.add(r["episode"])
    n = len(diffs)
    return {
        "n": n,
        "episodes": len(episodes),
        "controls": sum(len(v) for v in base.values()),
        "excess": sum(diffs) / n if n else None,
        "held_rate": sum(hits) / n if n else None,
        "baseline": sum(ps) / n if n else None,
    }


def stdev(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis and Romano): runs of mean length `block`, wrapping."""
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se(rows: list[dict], signal: str, rule: dict, *, resamples: int, rep=None) -> float | None:
    """The bootstrap standard error of the excess: drawdown weeks drawn in blocks, signal and baseline
    weeks alike, so the baseline's own error and the overlap of 63-session outcomes are in the draw."""
    bs = rule["bootstrap"]
    rng = random.Random(bs["seed"])
    draws = []
    for k in range(resamples):
        x = excess([rows[i] for i in stationary_indices(len(rows), bs["block"], rng)], signal, rule)["excess"]
        if x is not None:
            draws.append(x)
        if rep is not None:
            rep.advance(f"bootstrap {signal}", current=k + 1)
    return stdev(draws) if len(draws) >= 2 else None


def judge_one(rows: list[dict], signal: str, rule: dict, *, resamples: int | None = None, rep=None) -> dict:
    """One signal's verdict: the design part (effect, bootstrap t), then the holdout, read only after a
    design pass."""
    split = rule["splits"][0]
    design = [r for r in rows if r["date"] < split]
    holdout = [r for r in rows if r["date"] >= split]
    d = excess(design, signal, rule)
    out: dict = {"design": dict(d), "holdout": {"n": sum(1 for r in holdout if r[signal])}}
    if d["n"] < rule["min_n"] or d["episodes"] < rule["min_episodes"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        out["verdict"] = NO_VERDICT
        return out
    n_draws = resamples or rule["bootstrap"]["resamples"]
    if rep is not None:
        rep.phase(f"bootstrap {signal}", total=n_draws, unit="draws")
    se = boot_se(design, signal, rule, resamples=n_draws, rep=rep)
    if se is None:
        t = None
    elif se > 0:
        t = d["excess"] / se
    else:  # every draw gave the same excess: no spread to divide by
        t = math.inf if d["excess"] > 0 else None
    checks = {"effect": d["excess"] >= rule["min_effect"], "t": t is not None and t >= rule["t_bar"]}
    out["design"].update({"se": se, "t": t, "checks": checks})
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        out["verdict"] = FAIL
        return out
    out["design"]["verdict"] = PASS
    h = excess(holdout, signal, rule)
    word = PASS if h["excess"] is not None and h["excess"] > 0 else FAIL
    provisional = word == PASS and h["n"] < rule["holdout_min_n"]
    out["holdout"].update({**h, "verdict": word, "provisional": provisional})
    out["verdict"] = word
    out["provisional"] = provisional
    return out


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None, rep=None) -> dict:
    parts = {s: judge_one(rows, s, rule, resamples=resamples, rep=rep) for s in SIGNALS}
    return {"signals": parts, "verdict": {s: parts[s]["verdict"] for s in SIGNALS}}


def mean_of(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def reported(reports: list[dict], spx: list, rows: list[dict], rule: dict) -> dict:
    """The declared cuts, reported and never judged (the design part, like the statistic)."""
    split = rule["splits"][0]

    def design_of(rs: list[dict]) -> list[dict]:
        return [r for r in rs if r["date"] < split]

    design = design_of(rows)
    out: dict = {}
    for s in SIGNALS:
        cut: dict = {}
        for x in REPORTED_THRESHOLDS[s]:
            alt = {**rule, "signals": {**rule["signals"], s: x}}
            cut[f"threshold_{x}"] = excess(design_of(build_rows(reports, spx, alt)[0]), s, alt)
        for e in (0.05, 0.10):
            alt = {**rule, "drawdown_enter": e}
            cut[f"drawdown_{e}"] = excess(design_of(build_rows(reports, spx, alt)[0]), s, alt)
        for hz in (20, 126):
            alt = {**rule, "horizon": hz}
            cut[f"horizon_{hz}"] = excess(design_of(build_rows(reports, spx, alt)[0]), s, alt)
        cut["no_lower_close_than_reference"] = excess(design, s, rule, outcome="held_ref")
        sig_rows = [r for r in design if r[s]]
        rest = [r for r in design if not r[s]]
        cut["forward_returns"] = {
            "signal": {
                "ret_20": mean_of([r["ret_20"] for r in sig_rows]),
                "ret_63": mean_of([r["ret_63"] for r in sig_rows]),
            },
            "other_drawdown_weeks": {
                "ret_20": mean_of([r["ret_20"] for r in rest]),
                "ret_63": mean_of([r["ret_63"] for r in rest]),
            },
        }
        cut["first_signal_per_episode"] = [
            {"episode": ep, "date": min(r["date"] for r in rows if r["episode"] == ep and r[s])}
            for ep in sorted({r["episode"] for r in rows if r[s]})
        ]
        out[s] = cut
    naive = {**rule, "swell_days": -1, "cycle_reset": False}
    naive_rows = build_rows(reports, spx, naive)[0]
    out["oi_fall_without_roll_handling"] = excess(design_of(naive_rows), "oi_fall", naive)
    out["drawdown_weeks_by_episode"] = {
        str(ep): {
            "from": min(r["date"] for r in rows if r["episode"] == ep),
            "weeks": sum(1 for r in rows if r["episode"] == ep),
            **{s: sum(1 for r in rows if r["episode"] == ep and r[s]) for s in SIGNALS},
        }
        for ep in sorted({r["episode"] for r in rows})
    }
    return out


def fingerprint(reports: list[dict], spx: list) -> dict:
    """What the run read, as counts and a hash of the dates: never a figure."""
    lines = "\n".join([f"cot:{r['d']}" for r in reports] + [f"spx:{d}" for d, _ in spx])
    return {
        "reports": len(reports),
        "sessions": len(spx),
        "reports_span": [reports[0]["d"], reports[-1]["d"]] if reports else None,
        "sessions_span": [spx[0][0], spx[-1][0]] if spx else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- files


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item105"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cot_dir() -> Path:
    d = out_dir() / "cot"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spx_path() -> Path:
    from cherrypick.core import home

    return home.data_dir() / "market-files" / "cboe" / "SPX.csv"


def cftc_files(today: date) -> list[str]:
    return [CFTC_FIRST] + [CFTC_YEARLY.format(year=y) for y in range(2017, today.year + 1)]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def zip_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        return z.read(name).decode("latin-1")


def cmd_fetch(args) -> int:
    """Each CFTC file once (the current year's always again, as it grows), the E-mini rows kept as
    JSON beside a manifest. A file that fails is counted and named; the step fails if any did."""
    rule = FORECAST_RULE
    d = cot_dir()
    names = cftc_files(date.today())
    manifest: dict = {"source": CFTC_BASE, "files": {}, "failed": []}
    with core_progress.Reporter("futures_oi_bottom_item105-fetch") as rep:
        rep.split("files", names, unit="files")
        for i, name in enumerate(names):
            target = d / (name.removesuffix(".zip") + ".json")
            current_year = name == CFTC_YEARLY.format(year=date.today().year)
            if target.exists() and not current_year and not args.refresh:
                rows = json.loads(target.read_text(encoding="utf-8"))
            else:
                try:
                    rows = parse_cot(zip_text(_get(CFTC_BASE + name)), rule["market_code"])
                except Exception as exc:  # noqa: BLE001  one file failing is named, not fatal mid-run
                    manifest["failed"].append({"file": name, "error": str(exc)})
                    rep.advance("files", key=name, current=name)
                    continue
                target.write_text(json.dumps(rows), encoding="utf-8")
                if i + 1 < len(names):
                    time.sleep(2.0)
            dates = [r["d"] for r in rows]
            manifest["files"][name] = {
                "rows": len(rows),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
            }
            rep.advance("files", key=name, current=name)
    manifest["data"] = "public CFTC data, personal use: never commit or publish"
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(d / "manifest.json"), "failed": manifest["failed"]}))
    return 1 if manifest["failed"] else 0


def load_reports() -> list[dict]:
    parts = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(cot_dir().glob("*.json"))
        if p.name != "manifest.json"
    ]
    return merge_reports(parts)


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    with core_progress.Reporter("futures_oi_bottom_item105-study") as rep:
        rep.split("inputs", ["cot", "spx"], unit="files")
        reports = load_reports()
        rep.advance("inputs", key="cot", current="cot")
        spx = parse_spx(spx_path().read_text(encoding="utf-8"))
        rep.advance("inputs", key="spx", current="spx")
        if not reports or not spx:
            print("no reports or no SPX closes: run the fetch step and fetch_market_files", file=sys.stderr)
            return 1
        rows, dropped = build_rows(reports, spx, rule)
        block = judge(rows, rule, resamples=args.resamples, rep=rep)
        rep.at("inputs", "reported cuts")
        doc = {
            "data": "public CFTC and Cboe data, personal use: never commit or publish",
            "item": 105,
            "rule": rule,
            "window": {
                "from": rows[0]["date"] if rows else None,
                "through": rule["through"],
                "splits": rule["splits"],
            },
            "weeks": {"drawdown": len(rows), **{s: sum(1 for r in rows if r[s]) for s in SIGNALS}},
            "dropped": dropped,
            **block,
            "reported": reported(reports, spx, rows, rule),
            "inputs": fingerprint(reports, spx),
        }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--refresh", action="store_true", help="fetch every file again")
    f.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--resamples", type=int, default=None, help="bootstrap draws (default: the rule's)")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
