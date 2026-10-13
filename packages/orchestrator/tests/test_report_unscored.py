"""The report's per-module summary leaves an unrecorded result (`net_pnl: None`, n/r) out of every
figure and counts it, rather than adding it as a flat trade or failing on it (core.ledgers reports a
closed row the ledger left without its money as None, not 0)."""

from __future__ import annotations

from cherrypick.orchestrator import report


def test_an_unrecorded_net_is_counted_not_scored():
    out = report._summarize(
        [
            {"gross_pnl": 12.0, "cost": 2.0, "net_pnl": 10.0, "slippage": 0.5},
            {"gross_pnl": -1.0, "cost": 3.0, "net_pnl": -4.0, "slippage": 0.5},
            {"gross_pnl": None, "cost": None, "net_pnl": None, "slippage": None},
        ]
    )
    assert out["trades"] == 2
    assert out["unscored"] == 1
    assert (out["gross_pnl"], out["cost"], out["net_pnl"]) == (11.0, 5.0, 6.0)
    assert (out["wins"], out["losses"]) == (1, 1)
