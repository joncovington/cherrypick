"""Signed options flow and the flow-adjusted GEX (`core.gex.signed_flow` / `flow_adjusted`, 2026-11-01).

One hand-worked fixture (`fixtures/gex_flow_cases.json`) pins this and the console's SQL mirror to the
same answers."""

import json
from pathlib import Path

import pytest

from cherrypick.core import gex

CASES = json.loads((Path(__file__).parent / "fixtures" / "gex_flow_cases.json").read_text(encoding="utf-8"))


def _strip(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def test_signed_flow_matches_the_hand_worked_fixture():
    assert gex.signed_flow(_strip(CASES["prints"])) == CASES["expected_flow"]


def test_flow_adjusted_matches_the_hand_worked_fixture():
    flow = gex.signed_flow(_strip(CASES["prints"]))["strikes"]
    got = gex.flow_adjusted(CASES["strikes"], flow, CASES["spot"], CASES["multiplier"])
    assert got == _strip(CASES["expected_adjusted"])


@pytest.mark.parametrize(
    ("price", "expected"),
    [(1.3, 1), (1.2, 1), (1.1, 0), (1.0, -1), (0.9, -1)],
)
def test_the_quote_rule_without_an_aggressor(price, expected):
    p = {"kind": "NEW", "size": 1, "price": price, "bid": 1.0, "ask": 1.2, "aggressor": None}
    assert gex.print_sign(p) == expected


def test_a_crossed_or_missing_quote_is_unclassified():
    assert gex.print_sign({"kind": "NEW", "size": 1, "price": 1.0, "bid": 1.2, "ask": 1.0}) == 0
    assert gex.print_sign({"kind": "NEW", "size": 1, "price": 1.0, "bid": None, "ask": 1.0}) == 0
