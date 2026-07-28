#!/usr/bin/env python3
"""User-supervised DRY-RUN smoke of the core.broker write path — the phase-5 gate.

Builds a real 0DTE iron-condor order from live chains and submits it through
`tt.py execute_trade` **without** `--live`: tastytrade's dry-run endpoint runs the real
preflight against the real (designated) account — auth, order validation, margin /
buying-power effect, and the account deploy-limit governor verdict — but places nothing.
This is the one step of live enablement that can't be simulated: its whole point is
verifying our order construction and the broker's real responses agree BEFORE any live
loop is built on those assumptions.

What this harness will never do:
  - pass `--live` (there is no code path here that can);
  - require or touch `enable_live_trading` (it should stay false throughout);
  - write to any DB, config, or state.

Run it during regular hours on a trading day (0DTE chains + live quotes), with a human
watching: the exact order JSON is printed and must be confirmed by typing DRY-RUN before
anything is sent. Afterwards, verify in the tastytrade UI that no working order exists —
the one check only a human on the broker side can make.

Usage:
    python src/live_smoke.py                 # XSP, 1 contract, interactive confirm
    python src/live_smoke.py --symbol SPX --wing_width 10
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
for _p in (str(_SRC), str(_SRC / "_core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TT = [sys.executable, str(_SRC / "tt.py")]


def _run_tt(args: list[str], timeout: int = 90) -> dict:
    """Run a tt.py command and parse its JSON. {"ok": False, "error"} on any failure."""
    try:
        r = subprocess.run([*_TT, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"tt.py {args[0]} failed to run: {exc}"}
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"ok": False, "error": (r.stderr or "no JSON output").strip()[:300]}


def spec_from_strategy(strategy: dict, quantity: int = 1) -> dict:
    """An execute_trade order spec from a get_strategies iron-condor result. Pure.

    Shorts sell-to-open, longs buy-to-open, priced as a Day limit at the scan's natural
    mid credit rounded DOWN to a nickel — conservative (a lower credit asks the broker for
    less), and index options tick in 0.05s so the preflight can't reject the increment.
    """
    legs_in = strategy["legs"]

    def leg(name: str, action: str) -> dict:
        li = legs_in[name]
        return {
            "instrument_type": li.get("instrument_type") or li.get("instrument-type") or "Equity Option",
            "symbol": li["symbol"],
            "action": action,
            "quantity": quantity,
        }

    credit = strategy.get("net_credit")
    if credit is None:
        raise ValueError("strategy has no net_credit (quotes incomplete)")
    price = int(round(credit * 100)) // 5 * 5 / 100.0  # floor to a nickel
    if price <= 0:
        raise ValueError(f"non-positive credit {credit!r} — not a submittable IC")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "credit",
        "legs": [
            leg("short_put", "sell to open"),
            leg("long_put", "buy to open"),
            leg("short_call", "sell to open"),
            leg("long_call", "buy to open"),
        ],
    }


def evaluate(result: dict, expect_account: str | None) -> list[dict]:
    """PASS/FAIL checks over the execute_trade dry-run response. Pure.

    The checks encode what the smoke exists to prove: the submission stayed a dry run, the
    preflight priced a real buying-power effect, it landed on the designated account, and
    the deploy governor produced a verdict (or is visibly off — a note, not a failure).
    """
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("broker accepted the order (ok)", result.get("ok") is True,
        result.get("error") or "preflight returned ok")
    add("submission stayed a DRY RUN", result.get("dry_run") is True,
        f"dry_run={result.get('dry_run')!r} — must be True")
    bp = result.get("buying_power") or {}
    has_bp = any(v is not None for v in bp.values()) if isinstance(bp, dict) else False
    add("preflight priced a buying-power effect", has_bp,
        json.dumps(bp) if bp else "no buying_power fields in response")
    acct = result.get("account_number")
    if expect_account:
        add("ran against the designated account", acct == expect_account,
            f"response account ...{str(acct)[-4:] if acct else '?'} vs designated ...{expect_account[-4:]}")
    else:
        add("account designated", False,
            "no designated account — run `cherrypick account --module meic --set <last4>` first")
    gov = result.get("governor")
    if gov is None:
        add("deploy governor verdict (informational)", True,
            "governor OFF (account_deploy_limit_pct=0) — set a positive % to exercise it")
    else:
        add("deploy governor verdict (informational)", True, json.dumps(gov))
    return checks


def _designated_account() -> str | None:
    try:
        from cherrypick.core.auth import ACCOUNT_NUMBER, CredentialError

        from credentials import store
        try:
            return store.get_secret(ACCOUNT_NUMBER)
        except CredentialError:
            return None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XSP", help="0DTE index/ETF symbol (default XSP)")
    ap.add_argument("--quantity", type=int, default=1)
    ap.add_argument("--wing_width", type=float, default=None)
    ap.add_argument("--yes", action="store_true",
                    help="Skip the interactive confirmation (still a dry run, always)")
    args = ap.parse_args()

    print("== core.broker dry-run smoke (nothing will be placed) ==\n")

    print("[1/4] broker connection…")
    conn = _run_tt(["get_connection_status"])
    if not conn.get("ok"):
        print(f"FAIL: {conn.get('error', 'connection check failed')}")
        return 1
    print("      connected.")

    designated = _designated_account()
    print(f"      designated account: "
          f"{'****' + designated[-4:] if designated else 'NONE (evaluate() will flag this)'}")

    print(f"[2/4] scanning a 0DTE iron condor on {args.symbol}…")
    scan_args = ["get_strategies", "--symbol", args.symbol, "--target_dte", "0"]
    if args.wing_width:
        scan_args += ["--wing_width", str(args.wing_width)]
    strategy = _run_tt(scan_args, timeout=180)
    if not strategy.get("ok"):
        print(f"FAIL: {strategy.get('error', 'scan failed')}")
        return 1
    if strategy.get("dte") != 0:
        print(f"FAIL: nearest expiration is dte={strategy.get('dte')} — not a 0DTE session/symbol")
        return 1
    if not strategy.get("quotes_complete"):
        print("FAIL: quotes incomplete — start the streamer / retry during regular hours")
        return 1

    try:
        spec = spec_from_strategy(strategy, quantity=args.quantity)
    except (KeyError, ValueError) as exc:
        print(f"FAIL: could not build order spec: {exc}")
        return 1

    print(f"      credit ~${strategy.get('net_credit')}, POP ~{strategy.get('estimated_pop')}")
    print("\n[3/4] the EXACT order that will be preflighted (dry run):")
    print(json.dumps(spec, indent=2))
    if not args.yes:
        answer = input("\nType DRY-RUN to send this to the broker's dry-run preflight: ").strip()
        if answer != "DRY-RUN":
            print("aborted — nothing was sent.")
            return 1

    print("\n[4/4] preflighting (execute_trade WITHOUT --live)…")
    exec_args = ["execute_trade", "--order", json.dumps(spec)]
    if designated:
        exec_args += ["--account_number", designated]
    result = _run_tt(exec_args, timeout=120)

    checks = evaluate(result, designated)
    print()
    failed = 0
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        failed += 0 if c["ok"] else 1
        print(f"  [{mark}] {c['check']}: {c['detail']}")

    print(
        "\nManual verification (only a human on the broker side can do these):\n"
        "  1. tastytrade UI -> confirm NO working order and NO new position exists.\n"
        "  2. config.json -> confirm enable_live_trading is still false.\n"
        "  3. If the governor read 'OFF', consider setting account_deploy_limit_pct (e.g. 50)\n"
        "     and re-running so the live loop is built against an exercised governor."
    )
    print(f"\n{'SMOKE PASSED' if failed == 0 else f'SMOKE FAILED ({failed} check(s))'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
