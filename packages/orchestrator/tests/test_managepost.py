"""The position evaluation's post: which trades it lists, in what order, and how it states money."""

from __future__ import annotations

from cherrypick.orchestrator import managepost


def _trade(tid, action, conf, pnl, reason="why", shape="butterfly"):
    return {
        "trade_id": tid,
        "shape": shape,
        "pnl_gross": pnl,
        "recommendation": {"action": action, "confidence": conf, "rule": "r", "reason": reason},
    }


def _doc(*trades, accounts=None):
    return {
        "session": "2026-10-19",
        "accounts": accounts or [{"account": "****1234", "trades": list(trades)}],
    }


def test_lists_only_acting_trades_most_urgent_first():
    doc = _doc(
        _trade("SPY#1", "REVIEW", 60, 39.5),
        _trade("GOOGL", "HOLD", 60, 212.5),
        _trade("X", "SETTLES", 90, 1.0),
        _trade("META", "REVIEW", 70, 54.0),
        _trade("CLF", "CLOSE", 65, 22.0, reason="the report is out"),
    )
    title, message, embed = managepost.build(doc, "16:35")
    lines = message.splitlines()
    assert [ln.split("**")[1] for ln in lines[:3]] == ["CLF butterfly", "META butterfly", "SPY#1 butterfly"]
    assert "GOOGL" not in message and "SETTLES" not in message
    assert lines[0] == "**CLF butterfly**: CLOSE (65) · +$22.00 gross · the report is out"
    assert title == "Positions to manage, 2026-10-19 16:35 ET: 3 of 5"
    assert embed["description"] == message and embed["color"] == 0xE5534B


def test_all_hold_still_posts_and_says_so():
    title, message, embed = managepost.build(_doc(_trade("A", "HOLD", 60, -5.0)), "12:30")
    assert title.endswith(": 0 of 1")
    assert message.startswith("Nothing needs action: all 1 trades hold.")
    assert embed["color"] == 0x3FB950


def test_review_only_is_amber_and_losses_are_signed():
    _, message, embed = managepost.build(_doc(_trade("A", "REVIEW", 60, -95.5)), "10:00")
    assert "-$95.50 gross" in message
    assert embed["color"] == 0xD4A72C


def test_several_accounts_are_labelled_and_long_posts_fit_discord():
    many = [_trade(f"T{i}", "REVIEW", 60, 1.0, reason="x" * 200) for i in range(40)]
    doc = _doc(accounts=[{"account": "****1111", "trades": many}, {"account": "****2222", "trades": []}])
    _, message, embed = managepost.build(doc, "10:00")
    assert "__****1111__" in message and "__****2222__" in message
    assert len(embed["description"]) <= 4096
