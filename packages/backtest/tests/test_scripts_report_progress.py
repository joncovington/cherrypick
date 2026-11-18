"""Every fetch and study script reports its progress through `core.progress`, overall and per
symbol (the user's rule, 2026-11-17; packages/backtest/CLAUDE.md). Read from the source, never run.

A script passes when it imports `core.progress` and counts by symbol somewhere: a `track(...)` or an
`advance(...)` with `key=`, or a `Reporter.split(...)`. A script that is exempt says why below.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKTEST_SCRIPTS = REPO / "packages" / "backtest" / "scripts"
ROOT_SCRIPTS = REPO / "scripts"

IMPORTS = re.compile(r"from cherrypick\.core import (?:[\w, ]*\b)?progress\b|cherrypick\.core\.progress")
PER_SYMBOL = re.compile(
    r"track\((?:.|\n){0,600}?key=|advance\((?:.|\n){0,300}?key=|\.split\((?:.|\n){0,400}?unit="
)

#: Scripts that are no fetch, study or backtest, or that replay a retired module, each with why.
EXEMPT: dict[str, str] = {
    # packages/backtest/scripts: the work is one `runner.run`, which reports per symbol itself
    "flies_entry_limit_item21.py": "one runner.run, which reports per symbol",
    "flies_item40.py": "one runner.run, which reports per symbol",
    "meic_1400_stack_item73.py": "one runner.run, which reports per symbol",
    "meic_delta_grid_item23.py": "one runner.run, which reports per symbol",
    "meic_entry_slot_item24.py": "one runner.run, which reports per symbol",
    "meic_gate_oracle.py": "one runner.run, which reports per symbol",
    "meic_stop_grid_item48.py": "one runner.run, which reports per symbol",
    "meic_wing_width_gated_item50.py": "runner.run, which reports per symbol",
    "meic_wing_width_item43.py": "runner.run, which reports per symbol",
    "meic_wing_width_item44.py": "runner.run, which reports per symbol; its coverage is item 42's",
    # packages/backtest/scripts: seconds of in-memory work over stored results
    "meic_wide_first_hour_item82.py": "two stored runs and permutation draws",
    "meic_wing_fallback_item56.py": "recombines stored run rows",
    "open_vs_prior_value_item80.py": "one store load, then pure scoring",
    "signal_study.py": "one signalstudy.study call",
    "store_contract.py": "one storecontract.build call",
    "event_history_item75.py": "builds a calendar file from small local inputs",
    # packages/backtest/scripts: they hand a Reporter to another study's loop, which splits it
    "bullish_structures_bars_item79.py": "reports through item 64's trades()",
    "earnings_paper_screen_item67.py": "reports through item 31's run_study()",
    "fetch_addon_weeklies.py": "its fetch is addonfetch.fetch_spans, which reports per symbol",
    # repo-root scripts
    "_common.py": "a helper module, no job",
    "fetch_damodaran.py": "two downloads, no loop",
    "fetch_headlines.py": "reports overall; its feeds carry no symbol",
    "advisor_checkpoint.py": "the advisor's scheduled call, no loop over data",
    "arm_column_migrate.py": "a one-off migration",
    "arm_column_preflight.py": "a one-off migration check",
    "boundary_2026_10_19.py": "a one-off boundary record",
    "boundary_2026_11_03.py": "a one-off boundary record",
    "ci_changed_packages.py": "CI tooling",
    "demo_console.py": "a demo home",
    "demo_home.py": "a demo home",
    "earnings_backfill_exit_slippage.py": "a one-off ledger backfill",
    "earnings_clamp_exit_debits.py": "a one-off ledger fix",
    "eod_narrative.py": "a report, no long loop",
    "morning_narrative.py": "a report, no long loop",
    "guard_mutants.py": "test tooling",
    "guard_mutants_plugin.py": "test tooling",
    "live_order_smoke.py": "a live smoke check",
    "make_marketprofile_fixture.py": "test fixture tooling",
    "meic_live_move_paper_row.py": "a one-off ledger move",
    "probe_candles.py": "a one-off probe",
    "probe_futures_option_chain.py": "a one-off probe",
    "probe_time_and_sale.py": "a one-off probe",
    "reshoot.py": "screenshot tooling",
    "sync_stage_watchlists.py": "a watchlist sync, no data loop",
}
#: Replays of retired modules (flies, bwb, pmcc, curve): kept for the record, not run again.
RETIRED = ("flies_", "bwb_", "pmcc_", "curve_")


def _scripts() -> list[Path]:
    root = [p for p in sorted(ROOT_SCRIPTS.glob("*.py")) if not p.name.startswith(RETIRED)]
    return sorted(BACKTEST_SCRIPTS.glob("*.py")) + root


def test_every_fetch_and_study_script_reports_progress_overall_and_per_symbol():
    missing = []
    for path in _scripts():
        if path.name in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if not (IMPORTS.search(text) and PER_SYMBOL.search(text)):
            missing.append(str(path.relative_to(REPO)))
    assert not missing, "report progress overall and per symbol (core.progress): " + ", ".join(missing)


def test_every_exemption_names_a_script_that_exists():
    names = {p.name for p in _scripts()}
    assert not set(EXEMPT) - names
