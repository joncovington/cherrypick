"""Command-line surface for cherrypick-pmcc.

Subcommands (all read-only over the module's own ledger):
    status     open positions and the current expiration plan
    headline   per-book, per-symbol results through the analytics layer
    worksheet  the live per-position worksheet (the user's spreadsheet, from the ledger)
    exposure   the early-assignment-exposure telemetry
    ladder     the ITM call ladder as a selector would see it (the calibration read)

The paper loop's own argv (`python -m cherrypick.pmcc.paper_loop --once|--interval|--settle|
--status`) is what the orchestrator drives; this CLI is the human read side.
"""

from __future__ import annotations

import argparse
import json
import pathlib

# Package root (holds config.example.json): src/cherrypick/pmcc/cli.py -> parents[3] — the package root.
_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    """This module's config, by the suite's precedence — see
    `cherrypick.core.home.load_module_config`, which three modules had written out identically."""
    return _core_home.load_module_config("pmcc", _PKG_ROOT, path)


def cmd_status(args) -> int:
    from cherrypick.pmcc import clock, db

    config = load_config(args.config)
    conn = db.connect(args.db)
    print(
        json.dumps(
            {
                "ok": True,
                "expiration_plan": clock.expiration_plan(clock.now_et().date(), config.get("defaults") or {}),
                "open_positions": db.open_positions(conn),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_headline(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    era = getattr(args, "era", None) or analytics.CURRENT_ERA
    print(
        json.dumps(
            {"ok": True, "era": era, "headline": analytics.headline(conn, era=era)}, indent=2, default=str
        )
    )
    return 0


def cmd_worksheet(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "worksheet": analytics.worksheet(conn)}, indent=2, default=str))
    return 0


def cmd_exposure(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "exposure": analytics.exposure(conn)}, indent=2, default=str))
    return 0


# --------------------------------------------------------------------------- ladder
_DELTA_MARKS = (0.95, 0.90, 0.85, 0.80, 0.75, 0.70)


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _nearest_delta_row(rows: list[dict], target: float) -> dict | None:
    """The row a fixed-delta selector would take at `target` — nearest delta, ties to the deeper
    strike, which is the selector's own tie-break."""
    greeked = [r for r in rows if r.get("delta") is not None]
    if not greeked:
        return None
    return min(greeked, key=lambda r: (abs(r["delta"] - target), r["strike"]))


def _atm_pick(rows: list[dict], spot: float):
    """What the CURRENT ATM short rule (2026-08-23 redesign) would pick off this ladder: the strike
    nearest spot, whichever side it lands on. Shown beside the delta marks so the confound the
    fixed-delta control removes — the rule's strike walking with the vol regime — is visible on one
    screen."""
    usable = [r for r in rows if r.get("mid") is not None]
    if not usable:
        return None
    return min(usable, key=lambda r: abs(r["strike"] - spot))


def _summarize(snap: dict, defaults: dict) -> dict:
    """The calibration summary: the delta->moneyness map, liquidity per delta bucket, and what the
    yield rule would have taken. Every number here is measured off `snap`; nothing is modelled."""
    rows, spot = snap["rows"], snap["spot"]
    buckets: dict[str, dict] = {}
    for r in rows:
        d = r.get("delta")
        if d is None:
            continue
        key = f"{(int(d * 20) / 20):.2f}"
        b = buckets.setdefault(key, {"spreads": [], "ois": [], "strikes": 0})
        b["strikes"] += 1
        b["spreads"].append(r.get("spread_pct_of_mid"))
        b["ois"].append(r.get("open_interest"))

    delta_map = []
    for t in _DELTA_MARKS:
        r = _nearest_delta_row(rows, t)
        delta_map.append(
            {
                "target_delta": t,
                "strike": r and r["strike"],
                "actual_delta": r and r["delta"],
                "moneyness_pct": r and r["moneyness_pct"],
                "extrinsic": r and r["extrinsic"],
                "spread_pct_of_mid": r and r["spread_pct_of_mid"],
                "open_interest": r and r["open_interest"],
                "usable": r and r["usable"],
            }
        )

    # The long the entry would have bought, judged by the delta band now (85-90), falling back to
    # the extrinsic bound only when no candidate has a delta on file.
    delta_min = defaults.get("long_delta_min", 0.85)
    delta_max = defaults.get("long_delta_max", 0.90)
    banded = [r for r in rows if r.get("delta") is not None and delta_min <= r["delta"] <= delta_max]
    if banded:
        mid_target = (delta_min + delta_max) / 2.0
        long_row = min(banded, key=lambda r: abs(r["delta"] - mid_target))
    else:
        max_ext = defaults.get("max_long_extrinsic", 0.15)
        longs = [
            r for r in rows if r["extrinsic"] is not None and 0 <= r["extrinsic"] <= max_ext and r["usable"]
        ]
        long_row = max(longs, key=lambda r: r["strike"]) if longs else None
    atm_pick = _atm_pick(rows, spot)
    return {
        "delta_map": delta_map,
        "liquidity_by_delta_bucket": {
            k: {
                "strikes": v["strikes"],
                "median_spread_pct_of_mid": _median(v["spreads"]),
                "median_open_interest": _median(v["ois"]),
            }
            for k, v in sorted(buckets.items(), reverse=True)
        },
        "long_leg_would_be": long_row and {k: long_row[k] for k in ("strike", "delta", "mid", "extrinsic")},
        "atm_short_would_pick": atm_pick
        and {
            k: atm_pick[k]
            for k in (
                "strike",
                "delta",
                "moneyness_pct",
                "spread_pct_of_mid",
                "open_interest",
            )
        },
    }


def cmd_ladder(args) -> int:
    """The ITM call ladder for the module's expirations, as the selectors would see it.

    Read-only over the shared stream cache. This is how `short_delta_target` gets set from an
    observed ladder instead of from theory, and it stays the standing monitor for the deep-ITM
    spread risk afterwards — a recalibration is a measurement break, and a break needs evidence.
    """
    from cherrypick.pmcc import clock, paper_loop, provider

    config = load_config(args.config)
    defaults = config.get("defaults") or {}
    cache_path = paper_loop.stream_cache_path(config)
    symbols = (
        [args.symbol.strip().upper()]
        if args.symbol
        else [s.strip().upper() for s in (config.get("symbols") or [])]
    )
    plan = clock.expiration_plan(clock.now_et().date(), defaults) or {}
    if args.expiration:
        wanted = [{"expiration": args.expiration, "dte": None, "leg": "requested"}]
    else:
        wanted = [
            {"expiration": plan.get("short_expiration"), "dte": plan.get("short_dte"), "leg": "short"},
            {"expiration": plan.get("long_expiration"), "dte": plan.get("long_dte"), "leg": "long"},
        ]

    out = []
    for symbol in symbols:
        root = (config.get("occ_roots") or {}).get(symbol, symbol)
        for w in wanted:
            if not w["expiration"]:
                out.append({"ok": False, "symbol": symbol, "reason": "no_expiration_plan", **w})
                continue
            snap = provider.ladder_snapshot(
                cache_path,
                symbol,
                w["expiration"],
                root=root,
                deep_window_pct=provider.deep_window_pct_for(config, symbol),
                **provider.snapshot_kwargs(config),
            )
            if snap.get("ok"):
                snap["dte"] = w["dte"]
                snap["leg"] = w["leg"]
                snap["summary"] = _summarize(snap, defaults)
                if not args.json:
                    snap.pop("rows", None)
            out.append(snap)

    print(json.dumps({"ok": True, "ladders": out}, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pmcc", description="PMCC-99 deep-ITM covered-call paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions and the expiration plan").set_defaults(func=cmd_status)
    p_headline = sub.add_parser("headline", help="per-book results through the analytics layer")
    p_headline.add_argument(
        "--era",
        default=None,
        help="scope to one era; 'ALL' pools every era. Defaults to the module's current era.",
    )
    p_headline.set_defaults(func=cmd_headline)
    sub.add_parser("worksheet", help="the live per-position worksheet").set_defaults(func=cmd_worksheet)
    sub.add_parser("exposure", help="early-assignment-exposure telemetry").set_defaults(func=cmd_exposure)
    p_ladder = sub.add_parser("ladder", help="the ITM call ladder as a selector sees it")
    p_ladder.add_argument("--symbol", help="one symbol (default: every configured symbol)")
    p_ladder.add_argument("--expiration", help="one date (default: the plan's short and long)")
    p_ladder.add_argument("--json", action="store_true", help="include the full per-strike rows")
    p_ladder.set_defaults(func=cmd_ladder)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
