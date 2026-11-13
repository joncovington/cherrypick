"""Refuse a module configured to trade the SAME index through two vehicles.

meic, earnings and the orchestrator's own instruction files all carry the same sentence:
"Correlation risk is not currently guarded -- trading two highly correlated symbols simultaneously
(e.g. SPX and XSP move together) can silently double directional exposure without either symbol's
individual checks catching it." Three files stating a gap is not a guard. This is the guard for the
sharp half of it.

**Scope, deliberately narrow.** This fails only for two vehicles on ONE index -- SPX and XSP are
the same 500 companies at a tenth the notional, so a per-symbol position cap counts them as two
independent risks when they are one. Broad equity correlation (SPY against QQQ, ~0.9 in practice)
is real but is a portfolio-construction judgement, not a configuration error, and is reported
rather than failed. Flagging both the same way would make the honest signal easy to ignore.
"""

from __future__ import annotations

import json
import os
from itertools import combinations
from pathlib import Path

import pytest

# Vehicles on the same underlying index. Same index => the same directional bet, whatever the
# notional or the leverage multiple.
INDEX_FAMILIES: dict[str, set[str]] = {
    "sp500": {"SPX", "XSP", "SPY", "ES", "MES", "UPRO", "SPXL", "SSO", "SPXU", "SDS"},
    "nasdaq100": {"NDX", "QQQ", "MNQ", "NQ", "TQQQ", "QLD", "SQQQ"},
    "russell2000": {"RUT", "IWM", "TNA", "TZA", "RTY", "M2K"},
    "dow": {"DJI", "DIA", "UDOW", "SDOW", "YM", "MYM"},
}

# Leveraged US-equity ETFs: correlated with each other across indices, but not the same bet.
LEVERAGED_EQUITY = {"TQQQ", "UPRO", "TNA", "SPXL", "QLD", "SSO", "UDOW"}

REQUEST_DIR = Path(os.path.expanduser("~")) / ".cherrypick" / "state" / "stream_requests"


def families(symbols: list[str]) -> dict[str, list[str]]:
    """Which index family each configured symbol belongs to, keyed by family."""
    out: dict[str, list[str]] = {}
    for s in symbols:
        for family, members in INDEX_FAMILIES.items():
            if s.strip().upper() in members:
                out.setdefault(family, []).append(s.strip().upper())
    return out


def same_index_pairs(symbols: list[str]) -> list[tuple[str, str]]:
    return [
        (a, b) for members in families(symbols).values() for a, b in combinations(sorted(set(members)), 2)
    ]


# --------------------------------------------------------------------------- the rule


@pytest.mark.parametrize(
    "symbols, expected",
    [
        (["SPX"], []),
        (["SPX", "QQQ"], []),  # different indices: allowed
        (["TNA", "TQQQ", "UPRO"], []),  # three DIFFERENT indices, leveraged
        (["SPX", "XSP"], [("SPX", "XSP")]),  # the case the docs name
        (["SPY", "UPRO"], [("SPY", "UPRO")]),  # same index, leveraged vehicle
        (["QQQ", "TQQQ"], [("QQQ", "TQQQ")]),
        (["RUT", "IWM"], [("IWM", "RUT")]),
    ],
)
def test_same_index_pairs_are_detected(symbols, expected):
    assert same_index_pairs(symbols) == expected


def test_the_lint_can_fail():
    """A guard that cannot fire guards nothing."""
    assert same_index_pairs(["SPX", "XSP"])


# --------------------------------------------------------------------------- the live config


def _declared(request_dir: Path = REQUEST_DIR) -> dict[str, list[str]]:
    """Each module's trading roster as its own request file declares it: `entry_symbols` where the
    module states it (what it still ENTERS), else every subscribed symbol. A symbol held only to run
    off open positions -- pmcc's TQQQ beside QQQ from 2026-10-05 -- is not a roster that trades one
    index twice, and must not trip the guard; a module that declares nothing is judged on all of
    them, exactly as before `entry_symbols` existed.

    A file that declares `arm_rosters` (2026-10-15, MEIC) is judged ARM BY ARM, as `<module>:<arm>`:
    per-symbol caps are per arm, so control on SPX beside live-shadow on XSP is two books on two
    vehicles, while any one arm on both is still the doubled bet this guard exists for."""
    if not request_dir.exists():
        return {}
    out: dict[str, list[str]] = {}
    # Every producer instance's folder too (`stream_requests/<login>/`): a module declaring SPX to one
    # producer and XSP to another is still one book on one index, so a module's rosters are merged
    # across instances before they are judged.
    files = sorted(request_dir.glob("*.json")) + sorted(request_dir.glob("*/*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        arms = data.get("arm_rosters")
        if isinstance(arms, dict) and arms:
            for arm, symbols in arms.items():
                key = f"{f.stem}:{arm}"
                out[key] = sorted({*out.get(key, []), *(symbols or [])})
            continue
        roster = data.get("entry_symbols")
        mine = (roster if isinstance(roster, list) else data.get("symbols")) or []
        out[f.stem] = sorted({*out.get(f.stem, []), *mine})
    return out


def test_arm_rosters_are_judged_arm_by_arm(tmp_path):
    """Two arms on one index through two vehicles pass; one arm on both still fails."""
    (tmp_path / "meic.json").write_text(
        json.dumps(
            {
                "symbols": ["SPX", "XSP"],
                "arm_rosters": {"control": ["SPX"], "live-shadow": ["XSP"], "both": ["SPX", "XSP"]},
            }
        ),
        encoding="utf-8",
    )
    declared = _declared(tmp_path)
    assert declared == {"meic:control": ["SPX"], "meic:live-shadow": ["XSP"], "meic:both": ["SPX", "XSP"]}
    offenders = {m for m, syms in declared.items() if same_index_pairs(syms)}
    assert offenders == {"meic:both"}


def test_the_roster_is_what_a_module_enters_not_what_it_runs_off(tmp_path):
    (tmp_path / "pmcc.json").write_text(
        json.dumps({"symbols": ["QQQ", "TQQQ", "XSP"], "entry_symbols": ["QQQ", "XSP"]}), encoding="utf-8"
    )
    (tmp_path / "flies.json").write_text(json.dumps({"symbols": ["SPX", "XSP"]}), encoding="utf-8")
    declared = _declared(tmp_path)
    assert declared == {"flies": ["SPX", "XSP"], "pmcc": ["QQQ", "XSP"]}
    # The guard still fires on a module that declares no roster, and on a roster that is the bet.
    assert same_index_pairs(declared["flies"]) == [("SPX", "XSP")]
    assert same_index_pairs(["QQQ", "TQQQ"])


@pytest.mark.skipif(not REQUEST_DIR.exists(), reason="no deployed stream requests on this machine")
def test_a_module_split_across_producer_instances_is_still_judged_as_one(tmp_path):
    """SPX declared to main's producer and XSP to another login's is the same doubled bet."""
    (tmp_path / "mod.json").write_text(json.dumps({"symbols": ["SPX"]}), encoding="utf-8")
    (tmp_path / "margin").mkdir()
    (tmp_path / "margin" / "mod.json").write_text(json.dumps({"symbols": ["XSP"]}), encoding="utf-8")
    declared = _declared(tmp_path)
    assert declared == {"mod": ["SPX", "XSP"]}
    assert same_index_pairs(declared["mod"])


# Same-index pairs a module holds on purpose, each with its reason. One entry per (module, pair):
# the module's other pairs, and the same pair in any other module, still fail.
#
# weeklyput, SPX + SPY: item 57 judged the 10-delta weekly put on its equity-index group (SPX,
# SPY, QQQ, IWM) as one rule, and item 69 measures that rule in paper. It sells one contract per
# name with no per-name position cap, so the doubled S&P exposure is part of the rule being
# measured, not hidden from a cap that counts it twice.
DELIBERATE_PAIRS: dict[str, set[tuple[str, str]]] = {"weeklyput": {("SPX", "SPY")}}


def offending_pairs(declared: dict[str, list[str]]) -> dict[str, list[tuple[str, str]]]:
    """Each module's same-index pairs, less the ones it declares on purpose."""
    out = {}
    for module, symbols in declared.items():
        allowed = DELIBERATE_PAIRS.get(module, set())
        pairs = [p for p in same_index_pairs(symbols) if p not in allowed]
        if pairs:
            out[module] = pairs
    return out


def test_a_deliberate_pair_excuses_only_itself():
    """Verified to fail: keying the allowance by pair alone lets `other` through."""
    declared = {
        "weeklyput": ["IWM", "QQQ", "SPX", "SPY"],
        "other": ["SPX", "SPY"],
        "weeklyput:extra": ["SPX", "SPY"],
    }
    assert offending_pairs(declared) == {"other": [("SPX", "SPY")], "weeklyput:extra": [("SPX", "SPY")]}
    assert offending_pairs({"weeklyput": ["SPX", "SPY", "XSP"]}) == {
        "weeklyput": [("SPX", "XSP"), ("SPY", "XSP")]
    }


def test_no_module_trades_one_index_through_two_vehicles():
    offenders = offending_pairs(_declared())
    assert not offenders, (
        f"a module is configured on the same index twice: {offenders}. Per-symbol position caps "
        "count these as independent risks when they are one directional bet."
    )


@pytest.mark.skipif(not REQUEST_DIR.exists(), reason="no deployed stream requests on this machine")
def test_report_leveraged_equity_stacking(capsys):
    """Reported, never failed — this is a portfolio judgement, not a config error.

    Three 3x US-equity ETFs are ~0.9 correlated in practice even across different indices, so a
    drawdown tends to arrive on all of them at once. That is a thing to know while reading a
    module's risk numbers, not a thing to refuse at startup.
    """
    for module, symbols in _declared().items():
        stacked = sorted({s.upper() for s in symbols} & LEVERAGED_EQUITY)
        if len(stacked) > 1:
            with capsys.disabled():
                print(f"\n  note: {module} trades {len(stacked)} leveraged equity ETFs: {stacked}")
