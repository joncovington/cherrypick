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


def _declared() -> dict[str, list[str]]:
    if not REQUEST_DIR.exists():
        return {}
    out: dict[str, list[str]] = {}
    for f in sorted(REQUEST_DIR.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8")).get("symbols") or []
        except (OSError, ValueError):
            continue
    return out


@pytest.mark.skipif(not REQUEST_DIR.exists(), reason="no deployed stream requests on this machine")
def test_no_module_trades_one_index_through_two_vehicles():
    offenders = {
        module: pairs for module, symbols in _declared().items() if (pairs := same_index_pairs(symbols))
    }
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
