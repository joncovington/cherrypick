#!/usr/bin/env python3
"""Text report on what the screen rejected and why -- thin CLI over screen_metrics.py, the same
split strategy_report.py has over strategy_metrics.py, so a future console surface and this
terminal report can never disagree about the numbers.

Reads scan_log only. Touches no broker, no Dolt, and no ledger -- safe to run any time, including
mid-session.

Usage:
    python -m cherrypick.earnings.screen_report
    python -m cherrypick.earnings.screen_report --since 2026-08-01
    python -m cherrypick.earnings.screen_report --strategy iron_fly
    python -m cherrypick.earnings.screen_report --what-if avg_volume_below_minimum=1000000
"""

import argparse

from cherrypick.earnings import screen_metrics as sm
from cherrypick.earnings import strategy_metrics as _sm


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _num(value) -> str:
    """Thresholds span share counts and IV ratios, so pick a readable form per magnitude rather
    than one format that renders 1,000,000 as '1e+06'."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def print_excluded(rows: list[dict]) -> None:
    excluded = sm.excluded_summary(rows)
    if not excluded:
        return
    total = sum(e["rows"] for e in excluded)
    print("-- Not counted below " + "-" * 57)
    print(f"  {total} of {len(rows)} rows are not current screening decisions. scan_log has")
    print("  accumulated several vocabularies; pooling them would describe none of them.")
    print()
    for e in excluded:
        print(f"  {e['rows']:>5}  {e['kind']:<12} {e['note']}")
    print()


def print_funnel(rows: list[dict]) -> None:
    f = sm.funnel(rows)
    print("-- Funnel " + "-" * 68)
    print(f"  Pre-filtered out by the morning scan   {f['prefiltered_symbols']:>6} symbols")
    print(f"  Screened                               {f['screened_decisions']:>6} decisions "
          f"across {f['screened_symbols']} symbols")
    print(f"    rejected                             {f['rejected']:>6}")
    print(f"    accepted                             {f['accepted']:>6}")
    print(f"  Execution outcomes recorded            {f['execution_recorded']:>6}")
    print(f"    opened                               {f['opened']:>6}")
    print(f"    dropped after acceptance             {f['dropped']:>6}")
    if f["unexplained_accepted"]:
        print()
        print(f"  {f['unexplained_accepted']} accepted candidates have no execution row. Those")
        print("  predate stage recording (2026-08-12) -- what happened to them was never written")
        print("  down. This number should fall to zero as new scans accumulate.")
    if f["drop_reasons"]:
        print()
        print("  Why accepted candidates never opened:")
        for reason, count in f["drop_reasons"]:
            print(f"    {count:>5}  {reason}")
    print()


def print_reasons(rows: list[dict], limit: int) -> None:
    freq = sm.reason_frequency(rows)
    if not freq:
        print("-- Rejection reasons " + "-" * 57)
        print("  No rejections in range.")
        print()
        return
    print("-- Rejection reasons " + "-" * 57)
    print("  'sole' = the gate was the ONLY thing blocking the candidate. That is the only")
    print("  column a threshold change can act on; a name failing six gates still fails five.")
    print()
    print(f"  {'reason':<44}{'total':>7}{'sole':>7}{'strats':>8}")
    for r in freq[:limit]:
        print(f"  {r['reason']:<44}{r['total']:>7}{r['sole']:>7}{r['strategies']:>8}")
    shadowed = [r for r in freq if r["total"] >= 20 and r["sole"] == 0]
    if shadowed:
        print()
        print("  Fires often, never alone (shadowed by another gate -- tuning these changes")
        print("  nothing on its own):")
        for r in shadowed[:8]:
            print(f"    {r['reason']:<44} {r['total']:>6} rejections, 0 sole")
    print()


def print_distances(rows: list[dict], limit: int) -> None:
    coverage = sm.measurement_coverage(rows)
    print("-- Distance to the bar " + "-" * 55)
    print(f"  Measurements available on {coverage['with_details']} of {coverage['rejections']} "
          f"rejections ({_pct(coverage['fraction'])})"
          + (f", first on {coverage['first_detailed_scan']}" if coverage["first_detailed_scan"] else ""))
    if not coverage["with_details"]:
        print("  Rejections before 2026-08-12 carry reason names only, so there is nothing to")
        print("  measure distance against yet. This section fills in as new scans run.")
        print()
        return
    print()
    for entry in sm.reason_frequency(rows)[:limit]:
        if not entry["sole"]:
            continue
        dist = sm.threshold_distances(rows, entry["reason"])
        if not dist["measured_rows"]:
            continue
        print(f"  {dist['reason']}  (bar: {dist['comparator']} {_num(dist['threshold'])}, "
              f"{dist['measured_rows']} of {dist['rows']} sole rejections measured)")
        for b in dist["samples"][:5]:
            print(f"     {b['scan_date']}  {b['symbol']:<8} {b['strategy']:<26} "
                  f"measured {_num(b['measured'])}")
        print()
    print()


def print_cooccurrence(rows: list[dict], limit: int) -> None:
    pairs = sm.cooccurrence(rows, limit=limit)
    if not pairs:
        return
    print("-- Gates that fire together " + "-" * 50)
    print("  A pair always seen together is one finding reported twice.")
    print()
    print(f"  {'gate A':<38}{'gate B':<38}{'both':>6}{'A only':>8}{'B only':>8}")
    for p in pairs:
        print(f"  {p['a']:<38}{p['b']:<38}{p['together']:>6}{p['a_alone']:>8}{p['b_alone']:>8}")
    print()


def print_coverage_gaps(rows: list[dict]) -> None:
    gaps = sm.coverage_gaps(rows)
    if not gaps:
        return
    print("-- Where our data was the blocker " + "-" * 44)
    print("  '_unverified' means the value could never be measured -- the candidate was not")
    print("  judged against the bar at all. Reading these as failures turns a data outage into")
    print("  an apparently reasoned decision.")
    print()
    for g in gaps:
        print(f"  {g['count']:>5}  {g['reason']:<44} across {g['symbols']} symbols")
    print()


def print_what_if(rows: list[dict], specs: list[str]) -> None:
    print("-- What-if " + "-" * 67)
    print("  Counts candidates a different bar would have admitted. It cannot show P&L: a name")
    print("  that was never traded has no fill and no outcome. This sizes the opportunity only.")
    print()
    for spec in specs:
        if "=" not in spec:
            print(f"  {spec}: expected reason=threshold")
            continue
        reason, _, raw = spec.partition("=")
        try:
            threshold = float(raw)
        except ValueError:
            print(f"  {spec}: {raw!r} is not a number")
            continue
        cf = sm.counterfactual(rows, reason.strip(), threshold)
        if not cf["measurable"]:
            print(f"  {cf['reason']} at {_num(threshold)}: no measured sole rejections in range")
            continue
        print(f"  {cf['reason']} at {_num(threshold)}: admits {cf['admitted']} of "
              f"{cf['measurable']} sole rejections ({cf['symbol_nights']} symbol-nights)")
        if cf["symbols"]:
            print(f"     {', '.join(cf['symbols'][:20])}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode",
        choices=["live", "paper"],
        default="paper",
        help="'live' reads earnings_trades.db; 'paper' (default) reads paper_trades.db (both "
        "under the cherrypick data home).",
    )
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Book to report on. Defaults to 'strat_test' in paper mode, 'default' in live mode.",
    )
    parser.add_argument("--strategy", default=None, help="limit to one strategy")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD, only scans on/after this date")
    parser.add_argument("--limit", type=int, default=20, help="rows per table (default 20)")
    parser.add_argument(
        "--what-if",
        action="append",
        default=[],
        metavar="REASON=THRESHOLD",
        help="Count what a different bar would have admitted, e.g. "
        "avg_volume_below_minimum=1000000. Repeatable.",
    )
    args = parser.parse_args()

    db_path = _sm.db_path_for_mode(args.mode, args.db)
    profile = args.profile or ("strat_test" if args.mode == "paper" else "default")
    rows = sm.load_scan_rows(db_path, profile=profile, strategy=args.strategy, since=args.since)

    print("=" * 80)
    print(f"SCREEN REPORT -- profile={profile}" + (f" since={args.since}" if args.since else ""))
    print(f"MODE: {args.mode.upper()} ({db_path})")
    print("=" * 80)
    print()
    if not rows:
        print("No scan_log rows match. (A fresh book, or a --profile/--since with no scans in it.)")
        return
    print("Reads scan_log only -- no broker, no Dolt, no ledger. Rejection reasons are a moving")
    print("vocabulary: gates get renamed and retired, so a reason absent from recent scans may")
    print("have been renamed rather than stopped firing.")
    print()

    print_excluded(rows)
    print_funnel(rows)
    print_reasons(rows, args.limit)
    print_distances(rows, args.limit)
    print_cooccurrence(rows, min(args.limit, 12))
    print_coverage_gaps(rows)
    if args.what_if:
        print_what_if(rows, args.what_if)


if __name__ == "__main__":
    main()
