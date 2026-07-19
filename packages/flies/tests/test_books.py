"""Replay the three real tastytrade order chains through this module's accounting.

These are regression tests against reality rather than against our own model: every expected number
was transcribed from an actual order chain, so if the accounting drifts, these fail before any live
money is at stake. They are also the tests that pin the sign convention — get `positive = credit`
backwards and every one of them flips.
"""

import json
from pathlib import Path

import pytest

import fly

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "books.json").read_text(encoding="utf-8"))


def chain_pnl(net: float, mark: float, quantity: int = 1) -> float:
    """tastytrade's chain P/L: (net + mark) * 100. Verified against `Avg Trd Pr -0.65`, `Mark 3.97`,
    `Total P/L 332.00`. This is the same arithmetic `fly.position_pnl` does with `mark` replaced by
    the expiry payoff."""
    return round((net + mark) * fly.CONTRACT_MULTIPLIER * quantity, 2)


# --------------------------------------------------------------------------- Book C (the decisive one)
@pytest.mark.parametrize("chain", FIXTURES["book_c"]["chains"], ids=lambda c: c["label"])
def test_book_c_chain_nets_and_pnl(chain):
    """Each chain's orders sum to its recorded net, and that net plus the mark gives the recorded P/L."""
    assert round(sum(o["net"] for o in chain["orders"]), 4) == chain["expected_net"]
    assert chain_pnl(chain["expected_net"], chain["mark"], chain["quantity"]) == chain["expected_pnl"]


def test_book_c_totals_tie_out():
    """The three chains sum to the book's Avg Trd Pr (1.20) and Total P/L (126.00)."""
    chains = FIXTURES["book_c"]["chains"]
    assert round(sum(c["expected_net"] for c in chains), 4) == FIXTURES["book_c"]["expected_avg_trd_pr"]
    assert round(sum(c["expected_pnl"] for c in chains), 2) == FIXTURES["book_c"]["expected_total_pnl"]


def test_book_c_legged_flies_are_individually_risk_free():
    """The whole thesis, stated as a test: a legged fly's worst case at expiry is a profit.

    Checked at every price on a wide grid, not just at the strikes, and with a realistic SPX fee
    stack charged for both legs of the leg-in.
    """
    for chain in FIXTURES["book_c"]["chains"]:
        if chain["entry_mode"] != "legged":
            continue
        fees = fly.vertical_open_fee("SPX", 1) * 2
        position = {
            "kind": "fly", "side": chain["side"], "center": chain["center"],
            "wing_width": chain["wing_width"], "net": chain["expected_net"],
            "quantity": chain["quantity"], "fees": fees,
        }
        assert fly.is_risk_free(position), f"{chain['label']} floor did not survive fees"
        floor = fly.position_floor(position)
        for offset in range(-50, 51, 1):
            assert fly.position_pnl(position, chain["center"] + offset) >= floor - 1e-6


def test_book_c_outright_fly_is_not_risk_free():
    """The counterweight: the fly bought outright for a debit CAN lose, and the module must say so
    rather than lumping it in with the legged ones."""
    chain = next(c for c in FIXTURES["book_c"]["chains"] if c["entry_mode"] == "outright")
    position = {
        "kind": "fly", "side": chain["side"], "center": chain["center"],
        "wing_width": chain["wing_width"], "net": chain["expected_net"],
        "quantity": chain["quantity"], "fees": fly.fly_open_fee("SPX", 1),
    }
    assert not fly.is_risk_free(position)
    assert fly.position_floor(position) < 0


def test_book_c_completing_direction_inverts_by_side():
    """The put fly completed as price rose; the call fly as price fell. Both real, both in one book."""
    put_chain = next(c for c in FIXTURES["book_c"]["chains"]
                     if c["side"] == "put" and c["entry_mode"] == "legged")
    call_chain = next(c for c in FIXTURES["book_c"]["chains"] if c["side"] == "call")
    assert fly.completing_side_direction(put_chain["side"]) == "up"
    assert fly.completing_side_direction(call_chain["side"]) == "down"


# --------------------------------------------------------------------------- Book A (funded mode)
def test_book_a_condor_funds_the_flies():
    chains = FIXTURES["book_a"]["chains"]
    assert round(sum(c["net"] for c in chains), 4) == FIXTURES["book_a"]["expected_net"]


def test_book_a_chain_check_pins_the_sign_convention():
    """(Mark 3.97 + net -0.65) * 100 = 332.00. If this passes with the signs flipped, it is wrong."""
    check = FIXTURES["book_a"]["chain_check"]
    assert chain_pnl(check["net"], check["mark"]) == check["expected_pnl"]


def test_book_a_funded_flies_are_debits_not_credits():
    """Every fly in the funded book was BOUGHT. Their floors come from the condor, not from themselves."""
    flies = [c for c in FIXTURES["book_a"]["chains"] if c["kind"] == "fly"]
    assert flies and all(c["net"] < 0 for c in flies)


# --------------------------------------------------------------------------- Book B (the losing branch)
def test_book_b_fly_expired_worthless():
    """The expected common case: the fly missed, and lost exactly its debit."""
    chain = FIXTURES["book_b"]["chains"][0]
    assert chain_pnl(chain["net"], chain["mark"]) == chain["expected_pnl"]
    position = {"kind": "fly", "side": "put", "center": 6800, "wing_width": 5,
                "net": chain["net"], "quantity": 1, "fees": 0.0}
    # A fly bought for a debit and finishing outside its wings returns the debit as the loss.
    assert fly.position_pnl(position, 6700) == -10.0


def test_book_b_book_floor_is_not_unconditional():
    """Book B held open losses while its risk graph looked green in the middle. A book carrying short
    verticals is only 'risk-free' inside their wings, and `book_floor` has to report that rather than
    letting the green middle speak for the whole curve."""
    positions = [
        {"kind": "fly", "side": "put", "center": 6800, "wing_width": 5, "net": -0.10,
         "quantity": 1, "fees": 0.0},
        {"kind": "short_vertical", "side": "put", "center": 6790, "wing_width": 5, "net": 3.35,
         "quantity": 1, "fees": 0.0},
    ]
    result = fly.book_floor(positions)
    assert result["unbounded_below"] is True
    assert result["floor_holds"] is False
    assert result["band"] is not None  # green in the middle, and bounded — both true at once
