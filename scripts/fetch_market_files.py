"""Fetch the daily market files the morning pack reads: Cboe's index histories (SKEW, VIX, VVIX,
VXN), Treasury's par yield curve, and the release calendars (BEA always, FRED when a key is stored).

Why a script: these are network fetches, and `packages/overview` is network-free by rule. It writes
only `~/.cherrypick/data/market-files/`, and a failure leaves every file already there untouched.
The parsers are overview's own (`cherrypick.overview.files`), so a file is checked on arrival by the
code that will read it, and a download that parses to less than the file on disk (a truncated or
wrong file) is refused rather than written over good history.

Why these sources (docs/market-report-plan.md, "Sources, verified"): Cboe's daily SKEW file replaces
the stream's SKEW history, which delivered five scattered rows in 270 days; Treasury posts its curve
by about 18:00 ET, a day ahead of FRED; BEA's release-dates file is keyless. BLS refuses scripts,
so CPI, jobs and PPI dates come only through FRED's release-dates API, which needs a free key
(`fred-key` stores it in the OS keyring). FRED's keyless CSV download hangs from here, so FRED is
reached only through its API.

A handful of requests per run, a few seconds apart.

    python scripts/fetch_market_files.py [fetch]     # everything
    python scripts/fetch_market_files.py fred-key    # store a FRED API key once
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cherrypick.overview import files

UA = "cherrypick-marketfiles/1.0"
PAUSE_RANGE_S = (2.0, 5.0)
TIMEOUT_S = 60

FRED_SERVICE = "cherrypick-fred"
FRED_KEY = "api_key"
FRED_RELEASES_URL = "https://api.stlouisfed.org/fred/releases/dates"
# The releases worth a line in a pre-open pack, by FRED release id. Declared, not inferred: FRED
# lists ~300 releases a month and most move nothing.
FRED_RELEASES = {
    10: "Consumer Price Index",
    46: "Producer Price Index",
    50: "Employment Situation",
    180: "Unemployment Insurance Weekly Claims",
    9: "Advance Monthly Sales for Retail and Food Services",
    192: "Job Openings and Labor Turnover Survey",
    13: "Industrial Production and Capacity Utilization",
    27: "New Residential Construction",
    91: "Surveys of Consumers (University of Michigan)",
}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def _pause() -> None:
    time.sleep(random.uniform(*PAUSE_RANGE_S))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a file


def _replace_history(path: Path, text: str, parse) -> str | None:
    """Write `text` to `path` if it parses and holds at least what is already there. Returns why it
    was refused, or None."""
    new = parse(text)
    old = parse(path.read_text(encoding="utf-8")) if path.exists() else []
    problems = files.history_problems(old, new)
    if problems:
        return "; ".join(problems)
    _write(path, text)
    return None


def fetch_cboe(report: dict) -> None:
    for i, symbol in enumerate(files.CBOE_INDEXES):
        if i:
            _pause()
        try:
            text = _get(files.CBOE_URL.format(symbol=symbol)).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"Cboe {symbol}: {exc}")
            continue
        refused = _replace_history(files.cboe_path(symbol), text, files.parse_cboe)
        if refused:
            report["problems"].append(f"Cboe {symbol} not replaced: {refused}")
        else:
            rows = files.parse_cboe(text)
            report["cboe"][symbol] = {"rows": len(rows), "last": rows[-1][0].isoformat()}


def fetch_treasury(report: dict, today: date) -> None:
    # The first sessions of a year need last year's file too; fetch it only if it is missing.
    years = [today.year] + ([today.year - 1] if not files.treasury_path(today.year - 1).exists() else [])
    for i, year in enumerate(years):
        if i:
            _pause()
        try:
            text = _get(files.TREASURY_URL.format(year=year)).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"Treasury {year}: {exc}")
            continue
        refused = _replace_history(files.treasury_path(year), text, files.parse_treasury)
        if refused:
            report["problems"].append(f"Treasury {year} not replaced: {refused}")
        else:
            rows = files.parse_treasury(text)
            report["treasury"][str(year)] = {"rows": len(rows), "last": rows[-1][0].isoformat()}


def fetch_bea(report: dict) -> None:
    try:
        text = _get(files.BEA_URL).decode("utf-8-sig", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        report["problems"].append(f"BEA: {exc}")
        return
    rows = files.parse_bea(text)
    if not rows:
        report["problems"].append("BEA: the release-dates file parsed to nothing; kept the old one")
        return
    _write(files.bea_path(), text)
    report["calendar"]["bea_releases"] = len(rows)


def fred_releases(body: dict) -> list[dict]:
    """FRED's release-dates reply, narrowed to the declared releases. FRED gives dates, not times."""
    out = []
    for row in body.get("release_dates") or []:
        try:
            rid = int(row.get("release_id"))
        except (TypeError, ValueError):
            continue
        if rid in FRED_RELEASES and row.get("date"):
            out.append({"name": FRED_RELEASES[rid], "at": str(row["date"]), "source": "FRED"})
    return sorted(out, key=lambda r: (r["at"], r["name"]))


def fetch_fred(report: dict, today: date) -> None:
    try:
        from cherrypick.core.auth.credentials import CredentialStore

        key = CredentialStore(FRED_SERVICE).get_secret(FRED_KEY)
    except Exception:  # noqa: BLE001 -- no keyring or no key: FRED is optional
        key = None
    if not key:
        report["calendar"]["fred"] = "no key stored (run `fred-key`); CPI, jobs and PPI dates are absent"
        return
    params = {
        "api_key": key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": (today + timedelta(days=45)).isoformat(),
        "include_release_dates_with_no_data": "true",
        "limit": "1000",
    }
    try:
        body = json.loads(_get(FRED_RELEASES_URL + "?" + urllib.parse.urlencode(params)))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # The key rides in the URL; never let it reach a report or a log line.
        report["problems"].append(f"FRED: {str(exc).replace(key, '****')}")
        return
    rows = fred_releases(body)
    _write(
        files.fred_releases_path(),
        json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "releases": rows}),
    )
    report["calendar"]["fred_releases"] = len(rows)


def _warn(title: str, message: str) -> None:
    print(f"WARNING: {title}\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify("WARNING", "market_files", title, message)
    except Exception:  # noqa: BLE001
        pass


def cmd_fetch(_args) -> int:
    today = datetime.now(files.ET).date()
    report: dict = {"cboe": {}, "treasury": {}, "calendar": {}, "problems": []}
    fetch_cboe(report)
    _pause()
    fetch_treasury(report, today)
    _pause()
    fetch_bea(report)
    fetch_fred(report, today)
    report["ok"] = not report["problems"]
    print(json.dumps(report, indent=1))
    if report["problems"]:
        _warn("Market files incomplete", "\n".join(report["problems"]))
    return 0 if report["ok"] else 1


def cmd_fred_key(_args) -> int:
    from getpass import getpass

    from cherrypick.core.auth.credentials import CredentialStore, prompt_and_store

    written = prompt_and_store(
        CredentialStore(FRED_SERVICE), (FRED_KEY,), prompt_fn=lambda label: getpass(f"FRED {label}: ")
    )
    print(json.dumps({"ok": bool(written), "stored": list(written or [])}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("fetch").set_defaults(fn=cmd_fetch)
    sub.add_parser("fred-key").set_defaults(fn=cmd_fred_key)
    args = ap.parse_args(argv)
    return (getattr(args, "fn", None) or cmd_fetch)(args)


if __name__ == "__main__":
    sys.exit(main())
