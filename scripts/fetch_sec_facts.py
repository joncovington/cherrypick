"""Fetch SEC EDGAR's filing index and XBRL facts for the fair-value names (docs/fair-value-model-plan.md).

**Why.** The fair-value model read Dolt's annual statements, which lag (no fiscal year ending after
mid-2026 by 2026-10-28) and lump amortisation of content and leased vehicles into D&A (NFLX's $16.8B
against the $0.33B its filing reports). A company's own filings carry each line as reported, with the
date it was filed: current, and point in time for the history study.

**What.** Three public SEC files, no credential:
- `https://www.sec.gov/files/company_tickers.json`: ticker to CIK, refreshed once a day;
- `https://data.sec.gov/submissions/CIK##########.json` per name, every run: the filing index, the
  SIC code, the fiscal year end and the newest 10-K/10-Q/20-F/40-F;
- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` per name, only when the newest
  report's accession differs from the one last fetched: every fact the company has filed.

Written under `paths.sec_dir()` (`tickers.json`, `submissions/<CIK>.json`, `facts/<CIK>.json.gz`,
`fetched.json`); `fundamentals land sec` lands them into the package's store. SEC asks every client
to name itself with a contact and to stay under ten requests a second: the header is
`SEC_USER_AGENT`, read from the environment or this checkout's untracked `.env`, and requests are
paced at `PAUSE_S`. A 403 or 429 stops the run; what was read is kept.

The names are the fair-value build's (the security list's liquid optionable names the liquidity
verdict calls tradable), or those given. A script, not package code: it reaches the network.

    python scripts/fetch_sec_facts.py [--names AAPL,MSFT] [--force]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.fundamentals import paths

PAUSE_S = 0.25  # four requests a second, under SEC's ten
REPORT_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json"


class Stop(RuntimeError):
    """SEC refused or throttled: stop the run, keep what was read."""


def user_agent() -> str:
    """`SEC_USER_AGENT` from the environment, else from the checkout's `.env` (never the repo)."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    if not ua and env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SEC_USER_AGENT":
                ua = v.strip().strip('"').strip("'")
    if not ua:
        raise SystemExit("SEC_USER_AGENT is not set (environment or .env): SEC asks for a name and a contact")
    return ua


def get(url: str, ua: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise Stop(f"SEC answered {exc.code} for {url}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # No network (a name that will not resolve just after a restart, 2026-10-29): stop and keep
        # what was read; the next run resumes from the saved state.
        raise Stop(f"no connection to SEC ({exc}) for {url}") from exc
    finally:
        time.sleep(PAUSE_S)
    return body


def newest_report(sub: dict) -> dict | None:
    """The newest periodic report in a filing index: its form, accession, period and filing date."""
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    for i, form in enumerate(forms):
        if form in REPORT_FORMS:
            return {
                "form": form,
                "accession": recent["accessionNumber"][i],
                "period": recent["reportDate"][i],
                "filed": recent["filingDate"][i],
            }
    return None


def default_names() -> list[str]:
    from cherrypick.technicals import liquidity, securities

    listed, _ = securities.liquid_optionable(date.today().isoformat())
    allowed = liquidity.tradable_names() | liquidity.reference_tradable()
    return [s for s in listed if s in allowed]


def tickers(ua: str, root: Path) -> dict[str, int]:
    """Ticker to CIK, the suite's spelling (BRK.B): refreshed when the file is not from today."""
    path = root / "tickers.json"
    if not path.exists() or date.fromtimestamp(path.stat().st_mtime) != date.today():
        path.write_bytes(get(TICKERS_URL, ua))
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(v["ticker"]).replace("-", "."): int(v["cik_str"]) for v in raw.values()}


def run(names: list[str], force: bool = False, log=print) -> dict:
    ua = user_agent()
    root = paths.sec_dir()
    (root / "submissions").mkdir(parents=True, exist_ok=True)
    (root / "facts").mkdir(parents=True, exist_ok=True)
    state_path = root / "fetched.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    counts = {"names": len(names), "no_cik": 0, "no_report": 0, "facts_fetched": 0, "unchanged": 0}
    stopped = None
    try:
        ciks = tickers(ua, root)
        for sym in names:
            cik = ciks.get(sym)
            if cik is None:
                counts["no_cik"] += 1
                continue
            sub_body = get(SUBMISSIONS_URL.format(cik), ua)
            (root / "submissions" / f"{cik}.json").write_bytes(sub_body)
            report = newest_report(json.loads(sub_body))
            if report is None:
                counts["no_report"] += 1
                continue
            seen = state.get(str(cik), {})
            # One company, several tickers (GOOG and GOOGL): the facts are fetched once, landed for each.
            symbols = sorted({*seen.get("symbols", []), sym})
            facts = root / "facts" / f"{cik}.json.gz"
            if not force and facts.exists() and seen.get("accession") == report["accession"]:
                state[str(cik)] = {**seen, "symbols": symbols}
                counts["unchanged"] += 1
                continue
            facts.write_bytes(gzip.compress(get(FACTS_URL.format(cik), ua)))
            state[str(cik)] = {**report, "symbols": symbols, "fetched_at": datetime.now(UTC).isoformat()}
            # Saved after every company, not once at the end: a run cut off by a restart (2026-10-29,
            # 331 fetched, 143 recorded) resumes from where it stopped instead of fetching them again.
            _save(state_path, state)
            counts["facts_fetched"] += 1
            log(f"{sym}: {report['form']} for {report['period']}, filed {report['filed']}")
    except Stop as exc:
        stopped = str(exc)
        log(stopped)
    finally:
        _save(state_path, state)
    return {"ok": stopped is None, "stopped": stopped, **counts}


def _save(path: Path, state: dict) -> None:
    """Write the state whole or not at all: a kill mid-write must never leave half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--names", default=None, help="comma-separated symbols (default: the fair-value names)")
    ap.add_argument("--force", action="store_true", help="fetch the facts even when no new report was filed")
    args = ap.parse_args(argv)
    names = args.names.split(",") if args.names else default_names()
    out = run(names, force=args.force, log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
