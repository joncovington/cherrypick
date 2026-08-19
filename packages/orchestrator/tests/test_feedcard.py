"""The shared feed-card grammar, and the parity it exists to guarantee.

Both feed notifiers map into `notify.feedcard`'s spec and render THERE, so uniformity is by
construction — these tests pin the construction: same field names, same separator glyphs, same
title grammar, same lifecycle vocabulary, whichever feed a card came from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cherrypick.notify import feedcard
from cherrypick.orchestrator import follow_notifier as ff
from cherrypick.orchestrator import lossdog_notifier as ld

GREEN_SQ = "\U0001f7e9"


def _lossdog_trade(**over):
    t = {
        "id": "t1",
        "underlyingSymbol": "TSLA",
        "assetType": "Options",
        "strategyName": "Long Call",
        "price": 3.26,
        "priceLabel": "debit",
        "executionTime": "2026-08-03T14:15:08Z",
        "legs": [
            {
                "unitQuantity": 1,
                "expirationDate": "2026-08-21",
                "dte": 18,
                "strike": 360,
                "callOrPut": "CALL",
                "action": "BUY_TO_OPEN",
                "averageFillPrice": 3.26,
            }
        ],
        "trader": {"name": "Tom", "jobPosition": "CEO"},
    }
    t.update(over)
    return t


def _follow_order(**over):
    o = {
        "id": 9,
        "trader_id": 166462,
        "strategy": "Vertical",
        "order_type": "net_debit",
        "price": "4.98",
        "price_string": "4.98",
        "underlying_price": "163.86",
        "tos_iv_rank": "36.2",
        "executed_at": "2026-08-03T14:15:08Z",
        "order_legs": [
            {
                "underlying_symbol": "PLTR",
                "strike_price": "122.0",
                "call_or_put": "P",
                "open_close": "O",
                "action": "buytoopen",
                "quantity": 1,
                "expiration_date": "2026-08-07",
            }
        ],
    }
    o.update(over)
    return o


# --------------------------------------------------------------------------- parity
def test_both_feeds_render_the_same_card_shape():
    lossdog = ld.build_embed(_lossdog_trade())
    follow = ff.build_embed(_follow_order(), {166462: "Jim"})
    # Same field columns, same order, all inline — the channel reads as one format.
    assert [f["name"] for f in lossdog["fields"]] == ["Trade", "Context", "Stats"]
    assert [f["name"] for f in follow["fields"]] == ["Trade", "Context", "Stats"]
    assert all(f["inline"] for f in lossdog["fields"] + follow["fields"])
    # Same title grammar: colored square, lifecycle word, dot, symbol + strategy.
    assert lossdog["title"] == f"{GREEN_SQ} OPEN · TSLA Long Call"
    assert follow["title"] == f"{GREEN_SQ} OPEN · PLTR Vertical"
    # Same stripe vocabulary from the same constants.
    assert lossdog["color"] == follow["color"] == feedcard.COLOR_DEBIT


def test_both_feeds_carry_their_service_identity():
    ld_spec = ld.card_spec(_lossdog_trade())
    ff_spec = ff.card_spec(_follow_order(), {})
    assert feedcard.identity(ld_spec)["username"] == "Lossdog VIP"
    assert feedcard.identity(ff_spec)["username"] == "tastylive Follow"
    # Config can re-point an avatar; the username is the service label and stays.
    custom = feedcard.identity(ld_spec, {"lossdog": "https://img.example/dog.png"})
    assert custom == {"username": "Lossdog VIP", "avatar_url": "https://img.example/dog.png"}
    assert feedcard.identity({"service": "unknown"}) is None


def test_text_floor_is_uniform_and_colorless():
    lossdog = ld.format_trade(_lossdog_trade())
    follow = ff.format_order(_follow_order(), {166462: "Jim"})
    assert lossdog.startswith("➕ [lossdog] Tom · OPEN TSLA Long Call")
    assert follow.startswith("➕ [tastylive] Jim · OPEN PLTR Vertical")
    assert GREEN_SQ not in lossdog and GREEN_SQ not in follow


# --------------------------------------------------------------------------- the grammar itself
def test_lifecycle_marks_pair_color_with_word():
    for word, glyph in (
        ("OPEN", GREEN_SQ),
        ("CLOSE", "⬜"),
        ("ROLL", "\U0001f7e8"),
        ("FUTURES", "\U0001f7e6"),
    ):
        title = feedcard.build_embed({"lifecycle": word, "symbol": "X", "strategy": "Y"})["title"]
        assert title == f"{glyph} {word} · X Y"  # color beside the word, never instead of it
    # A verb fallback gets no square — an unrecognized word has no color in the scheme.
    assert feedcard.build_embed({"lifecycle": "Sold", "symbol": "X"})["title"] == "Sold · X"


def test_stripe_is_cash_and_unknown_is_neutral_not_red():
    assert feedcard.build_embed({"cash": "credit"})["color"] == feedcard.COLOR_CREDIT
    assert feedcard.build_embed({"cash": "debit"})["color"] == feedcard.COLOR_DEBIT
    assert feedcard.build_embed({"cash": "level"})["color"] == feedcard.COLOR_NEUTRAL
    assert feedcard.build_embed({})["color"] == feedcard.COLOR_NEUTRAL


def test_body_in_embed_false_keeps_the_text_floor_intact():
    spec = {"body": ["leg line"], "body_in_embed": False, "trader": {"name": "T"}}
    assert "description" not in feedcard.build_embed(spec)
    assert "> leg line" in feedcard.format_text(spec)


def test_timestamp_renders_utc_z():
    when = datetime(2026, 8, 3, 14, 15, 8, tzinfo=timezone.utc)
    embed = feedcard.build_embed({"timestamp": when})
    assert embed["timestamp"] == "2026-08-03T14:15:08Z"
