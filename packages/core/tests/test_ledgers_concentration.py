"""`ledgers.concentration` — how much of a module net rests on one arm.

Requested by the advisor on 2026-08-19, after flies published +6,748.01 for a session in which one
seven-fill book returned +7,828.42 and the other twelve came to -1,080.41. Its closing line was "no
bounded parameter can fix a presentation defect", which is the right diagnosis: this is not a rule
about which trades to take, it is a rule about which totals may be read on their own.

The live numbers in these tests are that session, and the two before it — 08-17 (-8,071.69) and
08-18 (-4,023.05) were also dominated by a single width-ladder book but did NOT change sign without
it. Discriminating between those cases is the whole job; a check that fired on all three would be
noise.
"""

from __future__ import annotations

import pytest

from cherrypick.core import ledgers


def _rec(profile, net, session="2026-08-19"):
    return {"profile": profile, "net_pnl": net, "session": session}


# --------------------------------------------------------------------------- the flagged case


def test_a_total_whose_sign_rests_on_one_arm_is_flagged():
    """flies, 2026-08-19, rounded to the arms that matter: the module reads positive and every other
    arm together reads negative."""
    out = ledgers.concentration(
        [_rec("width-10", 7828.42)] + [_rec(f"other-{i}", -1080.41 / 12) for i in range(12)]
    )

    assert out["net"] == pytest.approx(6748.01, abs=0.01)
    assert out["largest"]["profile"] == "width-10"
    assert out["net_excluding_largest"] == pytest.approx(-1080.41, abs=0.01)
    assert out["sign_flips_without_largest"] is True


def test_a_dominated_total_that_keeps_its_sign_is_not_flagged():
    """08-17 and 08-18 were each dominated by one width book and stayed negative without it. A
    concentration warning that fired on every dominated total would be ignored within a week.

    The rest is spread across twelve arms, as it was on the day — collapsing it into one row makes
    THAT the biggest mover and tests nothing about the leader."""
    out = ledgers.concentration(
        [_rec("width-3", -2615.85)] + [_rec(f"other-{i}", -5455.84 / 12) for i in range(12)]
    )

    assert out["largest"]["profile"] == "width-3"
    assert out["net"] == pytest.approx(-8071.69, abs=0.01)
    assert out["net_excluding_largest"] == pytest.approx(-5455.84, abs=0.01)
    assert out["sign_flips_without_largest"] is False


def test_the_largest_contributor_is_the_biggest_MOVER_not_the_biggest_winner():
    """An arm that lost more than everything else made is the same presentation problem wearing the
    other sign, and ranking on signed net would rank it last."""
    out = ledgers.concentration([_rec("loser", -9000.0), _rec("winner", 500.0), _rec("small", 10.0)])
    assert out["largest"]["profile"] == "loser"


# --------------------------------------------------------------------------- the two shares


def test_share_of_net_exceeds_one_when_the_other_arms_net_against_the_leader():
    """116% is the honest number for that flies session and it is *why* the total cannot be read
    alone. Clamping it would delete the signal."""
    out = ledgers.concentration([_rec("width-10", 7828.42), _rec("rest", -1080.41)])
    assert out["by_profile"][0]["share_of_net"] > 1.0


def test_share_of_net_is_none_rather_than_enormous_at_a_zero_total():
    """A ratio against ~0 is meaningless, not large. `None` says so; a big float would be read as a
    finding — the same 'None is not zero' rule the rest of this package follows."""
    out = ledgers.concentration([_rec("a", 500.0), _rec("b", -500.0)])
    assert out["net"] == 0.0
    assert all(row["share_of_net"] is None for row in out["by_profile"])
    # ...and the bounded denominator still answers "how much of what happened was this arm".
    assert out["by_profile"][0]["share_of_movement"] == pytest.approx(0.5)


def test_shares_of_movement_sum_to_one():
    out = ledgers.concentration([_rec("a", 300.0), _rec("b", -100.0), _rec("c", 600.0)])
    assert sum(r["share_of_movement"] for r in out["by_profile"]) == pytest.approx(1.0, abs=1e-4)


# --------------------------------------------------------------------------- shape and edges


def test_trades_and_sessions_are_returned_so_the_caller_can_apply_its_own_gate():
    """This function labels nothing PROVISIONAL. Whether the leader clears its module's sample and
    day bars is that module's rule, and importing qualification here would put two gates in play."""
    out = ledgers.concentration(
        [_rec("width-10", 4000.0, "2026-08-19"), _rec("width-10", 3828.42, "2026-08-20")]
    )
    assert out["largest"]["trades"] == 2
    assert out["largest"]["sessions"] == 2
    assert "provisional" not in str(out).lower()


def test_no_records_is_an_empty_answer_not_a_crash():
    out = ledgers.concentration([])
    assert out["net"] == 0.0 and out["largest"] is None
    assert out["sign_flips_without_largest"] is False


def test_a_single_arm_carries_the_whole_net_and_does_not_flip():
    """Removing the only contributor leaves 0.0, which is not a sign change."""
    out = ledgers.concentration([_rec("solo", 1234.0)])
    assert out["net_excluding_largest"] == 0.0
    assert out["sign_flips_without_largest"] is False


def test_records_with_no_profile_are_grouped_rather_than_dropped():
    out = ledgers.concentration([{"net_pnl": 100.0, "session": "s"}])
    assert out["by_profile"][0]["profile"] == "unassigned"


# --------------------------------------------------------------------------- tail to credit


def test_tail_to_credit_reports_the_ratio_the_book_table_never_showed():
    """The prompting book collected 7,852.50 against a modelled worst of -27,171.58 and settled
    positive because price stayed put. The ratio is 3.46 and nothing in the output said so."""
    assert ledgers.tail_to_credit(-27171.58, 7852.50) == 3.46


def test_tail_to_credit_is_none_without_a_credit_to_compare_against():
    """An undefined ratio and a small one are different facts."""
    assert ledgers.tail_to_credit(-100.0, 0) is None
    assert ledgers.tail_to_credit(-100.0, None) is None


def test_a_book_with_no_modelled_loss_has_a_zero_tail_not_a_negative_one():
    assert ledgers.tail_to_credit(250.0, 1000.0) == 0.0
