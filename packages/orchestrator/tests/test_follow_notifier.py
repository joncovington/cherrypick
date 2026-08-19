"""Tests for the tastylive Follow Feed notifier.

Three properties carry the design and are asserted here rather than left to prose:
  - it is off the reliability path (a feed outage is a skip, never an exception),
  - it seeds instead of backfilling, and never re-notifies an id it has already pushed,
  - a long gap is capped rather than flooding the channel with the whole 50-order window.

Every test stubs the HTTP layer; nothing here touches the network.
"""

import json

import pytest

import cherrypick.orchestrator.follow_notifier as ff
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


def _order(order_id, trader_id=166462, **over):
    o = {
        "id": order_id,
        "trader_id": trader_id,
        "order_type": "net_credit",
        "price_string": "0.87",
        "strategy": "Vertical",
        "executed_at": f"2026-08-04T12:{order_id:02d}:00Z",  # monotonic with id, as the real feed is
        "underlying_price_string": "163.86",
        "tos_iv_rank": "35.7653791130186",
        "probability_of_profit": None,
        "is_earnings_play": False,
        "order_legs": [
            {
                "underlying_symbol": "PLTR",
                "strike_price": "122.0",
                "call_or_put": "P",
                "expiration_date": "2026-08-07",
                "open_close": "O",
                "quantity": "1.0",
            },
            {
                "underlying_symbol": "PLTR",
                "strike_price": "117.0",
                "call_or_put": "P",
                "expiration_date": "2026-08-07",
                "open_close": "O",
                "quantity": "1.0",
            },
        ],
        "comments": [],
    }
    o.update(over)
    return o


def _fields(embed):
    return {f["name"]: f["value"] for f in embed["fields"]}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point state + lock at a temp dir and capture pushes instead of sending them."""
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ff, "_STATE", tmp_path / "follow_notify.json")
    monkeypatch.setattr(ff, "_LOCK", tmp_path / "follow_notify.lock")
    monkeypatch.setattr(cfgmod, "ensure_dirs", lambda: None)

    sent = []
    monkeypatch.setattr(
        ff.Notifier,
        "notify",
        lambda self, level, key, title, message, embed=None, identity=None: (
            sent.append((key, message, embed, identity)) or {"log": {"ok": True}}
        ),
    )
    monkeypatch.setattr(ff, "fetch_trader_names", lambda: {166462: "Jim Schultz"})
    return sent


def _cfg(**over):
    ffcfg = {"enabled": True, "channels": ["log"], "max_per_run": 8, "filters": {}}
    ffcfg.update(over)
    return {"follow_feed": ffcfg, "notify": {}}


def _feed(monkeypatch, orders):
    monkeypatch.setattr(ff, "fetch_orders", lambda _filters: orders)


# --------------------------------------------------------------------------- reliability
def test_feed_outage_is_a_skip_not_an_exception(wired, monkeypatch):
    """The feed is a third-party HTTP service. Down, slow, or serving garbage, it must degrade to
    'nothing to notify' — this notifier having its own task is only half the isolation."""
    monkeypatch.setattr(ff, "fetch_orders", lambda _filters: [])
    res = ff.run(_cfg())
    assert res["ok"] is True and res["orders_seen"] == 0
    assert wired == []


def test_get_json_swallows_transport_errors(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("connection reset")

    monkeypatch.setattr(ff.urllib.request, "urlopen", boom)
    assert ff._get_json("/api/traders") is None
    assert ff.fetch_orders({}) == []
    assert ff.fetch_trader_names() == {}


def test_disabled_by_default(wired, monkeypatch):
    _feed(monkeypatch, [_order(1)])
    assert ff.run({"follow_feed": {"enabled": False}})["skipped"] == "follow_feed not enabled"
    assert ff.run({})["skipped"] == "follow_feed not enabled"
    assert wired == []


# --------------------------------------------------------------------------- watermark
def test_first_run_seeds_without_backfilling(wired, monkeypatch):
    _feed(monkeypatch, [_order(i) for i in range(1, 21)])
    res = ff.run(_cfg())
    assert res["seeded"] is True
    assert wired == []  # switching it on must not blast the existing window
    assert set(json.loads(ff._STATE.read_text())["notified_ids"]) == set(range(1, 21))


def test_only_new_orders_notify_and_never_twice(wired, monkeypatch):
    _feed(monkeypatch, [_order(1), _order(2)])
    ff.run(_cfg())  # seed

    _feed(monkeypatch, [_order(1), _order(2), _order(3)])
    assert ff.run(_cfg())["notified"] == 1
    assert len(wired) == 1 and wired[0][0] == "follow.order.3"

    assert ff.run(_cfg())["notified"] == 0  # same window again — no re-notify
    assert len(wired) == 1


def test_burst_is_capped_and_remainder_watermarked(wired, monkeypatch):
    """After downtime the whole window looks new. Push the newest few, mark the rest seen — the
    alternative is a 50-message flood, and the tail is the least interesting part of it."""
    _feed(monkeypatch, [_order(1)])
    ff.run(_cfg())  # seed

    _feed(monkeypatch, [_order(i) for i in range(1, 32)])
    res = ff.run(_cfg(max_per_run=3))
    assert res["notified"] == 3 and res["suppressed"] == 27
    assert [k for k, _, _, _ in wired] == ["follow.order.29", "follow.order.30", "follow.order.31"]

    # Suppressed ids are watermarked, not left to resurface on the next tick.
    assert ff.run(_cfg(max_per_run=3))["notified"] == 0


def test_orders_push_in_chronological_order(wired, monkeypatch):
    _feed(monkeypatch, [_order(1)])
    ff.run(_cfg())
    _feed(monkeypatch, [_order(1), _order(5), _order(3), _order(4)])
    ff.run(_cfg())
    assert [k for k, _, _, _ in wired] == ["follow.order.3", "follow.order.4", "follow.order.5"]


def test_lock_blocks_a_concurrent_run(wired, monkeypatch):
    _feed(monkeypatch, [_order(1)])
    assert ff._acquire_lock()
    try:
        assert ff.run(_cfg())["skipped"] == "another follow-notify run holds the lock"
    finally:
        ff._release_lock()


# --------------------------------------------------------------------------- formatting: the plain line
def test_line_leads_with_lifecycle_then_carries_the_numbers():
    line = ff.format_order(
        _order(
            1,
            order_type="net_debit",
            price_string="4.98",
            probability_of_profit="30.9506712758445",
            is_earnings_play=True,
            comments=[{"body": "Taking max profit after the earnings move."}],
        ),
        {166462: "Jim Schultz"},
    )
    head, detail, body = line.split("\n")
    assert head == "➕ [tastylive] Jim Schultz · OPEN PLTR Vertical"  # open/close leads, not Bought/Sold
    assert "1× 122P/117P" in detail
    assert "exp Aug 7" in detail
    assert "$4.98 db" in detail  # debit/credit survives in the price suffix
    assert "PLTR 163.86" in detail  # spot at fill
    assert "IVR 36" in detail
    assert "POP 31%" in detail
    assert "Earnings" in detail
    assert body == "> Taking max profit after the earnings move."


def test_closing_order_is_tagged_close_and_credit():
    legs = [
        {
            "underlying_symbol": "XSP",
            "strike_price": "770.0",
            "call_or_put": "C",
            "expiration_date": "2026-08-04",
            "open_close": "C",
            "quantity": "1.0",
        }
    ]
    line = ff.format_order(_order(2, order_legs=legs), {166462: "Jim Schultz"})
    assert line.startswith("➖ [tastylive] Jim Schultz · CLOSE XSP Vertical")
    assert "$0.87 cr" in line
    assert "POP" not in line  # POP describes an opening trade only


def test_mixed_open_close_is_a_roll():
    """Legs marked on both sides are a close paired with an open — the feed tags every leg, so
    mixed marks aren't ambiguity, they're a roll. Only an order with NO marks falls to the verb."""
    legs = [
        {"underlying_symbol": "SPX", "strike_price": "6300.0", "call_or_put": "P", "open_close": "C"},
        {"underlying_symbol": "SPX", "strike_price": "6250.0", "call_or_put": "P", "open_close": "O"},
    ]
    line = ff.format_order(_order(3, order_legs=legs), {166462: "Jim"})
    assert "ROLL SPX Vertical" in line
    assert "OPEN" not in line and "CLOSE" not in line
    unmarked = [{"underlying_symbol": "SPX", "strike_price": "6300.0", "call_or_put": "P"}]
    assert "Sold SPX" in ff.format_order(_order(3, order_legs=unmarked), {166462: "Jim"})


def test_unknown_trader_id_still_formats():
    """The roster call is best-effort; an unknown id must not drop the order."""
    assert "trader 999" in ff.format_order(_order(4, trader_id=999), {})


def test_stock_leg_names_its_symbol_once():
    """An equity leg's only identifier is its underlying, already on the line — printing the leg too
    gave "NVTS NVTS"."""
    legs = [
        {
            "underlying_symbol": "NVTS",
            "symbol": "NVTS",
            "asset_type": "E",
            "action": "selltoclose",
            "open_close": "C",
            "quantity": "100.0",
        }
    ]
    line = ff.format_order(
        _order(5, order_legs=legs, order_type="limit", price_string="12.65", underlying_price_string="12.65"),
        {},
    )
    assert line.count("NVTS") == 2  # once in the headline, once as the spot label — never as a leg
    assert "100 sh" in line  # shares, not contracts — "100×" reads as a 100-lot


def test_single_leg_limit_order_reads_as_bought_or_sold():
    """Single-leg orders carry order_type "limit", not net_debit/net_credit. The db/cr suffix comes
    from the leg action instead — otherwise a sale rendered with no suffix at all."""
    stock = [
        {
            "underlying_symbol": "NVTS",
            "symbol": "NVTS",
            "action": "selltoclose",
            "open_close": "C",
            "quantity": "100.0",
        }
    ]
    line = ff.format_order(
        _order(6, order_legs=stock, order_type="limit", strategy="Stock", price_string="12.65"), {}
    )
    assert "CLOSE NVTS Stock" in line and "$12.65 cr" in line

    opt = [
        {
            "underlying_symbol": "IWM",
            "strike_price": "300.0",
            "call_or_put": "C",
            "action": "buytoopen",
            "open_close": "O",
            "quantity": "1.0",
        }
    ]
    line = ff.format_order(
        _order(7, order_legs=opt, order_type="limit", strategy="Option", price_string="9.61"), {}
    )
    assert "OPEN IWM Option" in line and "$9.61 db" in line


def test_missing_context_fields_just_drop_out():
    """The feed nulls these constantly. A missing IV rank must not leave 'IVR ' dangling."""
    line = ff.format_order(
        _order(8, tos_iv_rank=None, underlying_price_string=None, underlying_price=None, executed_at=None),
        {166462: "Jim Schultz"},
    )
    assert "IVR" not in line and "UTC" not in line
    assert "· ·" not in line and not line.endswith("·")


# --------------------------------------------------------------------------- formatting: the discord embed
def test_embed_carries_the_numbers_as_fields():
    embed = ff.build_embed(
        _order(
            9,
            order_type="net_debit",
            price_string="4.98",
            probability_of_profit="30.95",
            is_earnings_play=True,
            comments=[{"body": "LG!"}],
        ),
        {166462: "Jim Schultz"},
    )
    assert embed["author"]["name"] == "Jim Schultz"
    assert embed["title"] == "🟩 OPEN · PLTR Vertical"
    assert embed["description"] == "> LG!"
    # The tags ride the Stats column now; the footer uniformly names the feed.
    assert "Earnings" in _fields(embed)["Stats"]
    assert embed["footer"]["text"] == "tastylive Follow Feed"
    assert _fields(embed) == {
        "Trade": "1× 122P/117P · $4.98 db",
        "Context": "Aug 7 · PLTR 163.86",
        "Stats": "IVR 36 · POP 31% · Earnings",
    }
    assert all(f["inline"] for f in embed["fields"])


def test_embed_timestamp_is_the_fill_time_in_a_shape_discord_accepts():
    """Discord renders this in each reader's own timezone — the honest way to stamp a message that
    can arrive well after the fill."""
    assert ff.build_embed(_order(10), {166462: "Jim Schultz"})["timestamp"] == "2026-08-04T12:10:00Z"


def test_embed_color_is_the_cash_direction_not_the_lifecycle():
    """The stripe answers "which way did money move", the headline answers open/close — two
    independent facts. Futures quote a level rather than cash, so they stay neutral, as does an
    order whose direction the feed didn't say."""

    def embed(**over):
        return ff.build_embed(_order(11, **over), {166462: "Jim Schultz"})

    assert embed()["color"] == ff.COLOR_CREDIT  # the default order is a net_credit
    assert embed()["title"].startswith("🟩 OPEN")  # ...and the lifecycle still leads the headline
    assert embed(order_type="net_debit")["color"] == ff.COLOR_DEBIT
    closed = [dict(leg, open_close="C") for leg in _order(0)["order_legs"]]
    assert embed(order_type="net_debit", order_legs=closed)["color"] == ff.COLOR_DEBIT
    rolled = [dict(_order(0)["order_legs"][0]), dict(_order(0)["order_legs"][1], open_close="C")]
    assert embed(order_legs=rolled)["color"] == ff.COLOR_CREDIT  # a roll takes the color of its net
    futures = [{"underlying_symbol": "/MNQU6", "asset_type": "/", "quantity": "1.0", "action": "buytoopen"}]
    assert embed(order_type="limit", order_legs=futures)["color"] == ff.COLOR_NEUTRAL
    unsaid = [dict(leg, action=None) for leg in _order(0)["order_legs"]]
    assert embed(order_type="limit", order_legs=unsaid)["color"] == ff.COLOR_NEUTRAL


def test_embed_omits_empty_fields_rather_than_showing_blanks():
    embed = ff.build_embed(
        _order(12, tos_iv_rank=None, underlying_price_string=None, underlying_price=None), {}
    )
    fields = _fields(embed)
    assert "Stats" not in fields  # IV rank and POP both empty
    assert fields["Trade"] == "1× 122P/117P · $0.87 cr"
    assert fields["Context"] == "Aug 7"  # spot dropped, expiry survives alone


def test_embed_survives_an_order_with_almost_nothing_in_it():
    """The endpoints are undocumented and unversioned. A stripped-down order must still render."""
    embed = ff.build_embed({"id": 1, "trader_id": 999}, {})
    assert embed["author"]["name"] == "trader 999"
    assert embed["title"] and "fields" in embed
    assert "timestamp" not in embed


# --------------------------------------------------------------------------- filters
def test_filters_become_the_feeds_own_query_parameters():
    params = ff._filter_params(
        {
            "traders": ["Jim Schultz", "Katie"],
            "underlying_symbols": ["pltr"],
            "earnings_only": True,
            "open_close": "O",
            "strategy": "Vertical",
        }
    )
    assert ("traders[]", "Jim Schultz") in params and ("traders[]", "Katie") in params
    assert ("underlying_symbols[]", "PLTR") in params  # normalized
    assert ("attrs[is_earnings_play]", "true") in params
    assert ("attrs[open_close]", "O") in params
    assert ("strategy", "Vertical") in params


def test_empty_filters_request_the_whole_feed():
    assert ff._filter_params({}) == []
    assert ff._filter_params({"traders": [], "open_close": None, "earnings_only": False}) == []


# --------------------------------------------------------------------------- leg side + asset shape
def test_vertical_shows_which_leg_is_short():
    """'122P/117P' is a CREDIT spread short the 122 and a DEBIT spread long it, and the net db/cr
    can't settle it — buying back a credit spread is a debit. The sign is the only thing that can."""
    credit = [
        {
            "underlying_symbol": "AAPL",
            "strike_price": "315.0",
            "call_or_put": "C",
            "action": "selltoopen",
            "quantity": "1.0",
            "open_close": "O",
        },
        {
            "underlying_symbol": "AAPL",
            "strike_price": "320.0",
            "call_or_put": "C",
            "action": "buytoopen",
            "quantity": "1.0",
            "open_close": "O",
        },
    ]
    assert "-315C/+320C" in ff.format_order(_order(1, order_legs=credit, order_type="net_credit"), {})

    debit = [
        {
            "underlying_symbol": "PLTR",
            "strike_price": "122.0",
            "call_or_put": "P",
            "action": "buytoopen",
            "quantity": "1.0",
            "open_close": "O",
        },
        {
            "underlying_symbol": "PLTR",
            "strike_price": "117.0",
            "call_or_put": "P",
            "action": "selltoopen",
            "quantity": "1.0",
            "open_close": "O",
        },
    ]
    assert "+122P/-117P" in ff.format_order(_order(2, order_legs=debit, order_type="net_debit"), {})


def test_lone_option_shows_the_side_taken():
    """ "Option" alone says nothing about whether they bought or sold it."""
    sold = [
        {
            "underlying_symbol": "IWM",
            "strike_price": "300.0",
            "call_or_put": "C",
            "action": "selltoclose",
            "quantity": "1.0",
            "open_close": "C",
        }
    ]
    assert "-300C" in ff.format_order(_order(3, order_legs=sold, order_type="limit"), {})

    bought = [
        {
            "underlying_symbol": "QQQ",
            "strike_price": "710.0",
            "call_or_put": "P",
            "action": "buytoopen",
            "quantity": "1.0",
            "open_close": "O",
        }
    ]
    assert "+710P" in ff.format_order(_order(4, order_legs=bought, order_type="limit"), {})


def test_one_sided_order_trusts_the_leg_over_a_contradicting_order_type():
    """The feed ships single-leg orders whose order_type contradicts the leg — a lone selltoclose
    call tagged net_debit. That is a credit to the trader; reading order_type first printed 'db'."""
    legs = [
        {
            "underlying_symbol": "SPY",
            "strike_price": "160.0",
            "call_or_put": "C",
            "action": "selltoclose",
            "quantity": "1.0",
            "open_close": "C",
        }
    ]
    line = ff.format_order(_order(5, order_legs=legs, order_type="net_debit", price_string="5.95"), {})
    assert "$5.95 cr" in line and "$5.95 db" not in line


def test_package_still_reads_its_net_when_legs_point_both_ways():
    """A vertical's legs disagree by construction, so only order_type says debit or credit."""
    legs = [
        {
            "underlying_symbol": "XSP",
            "strike_price": "767.0",
            "call_or_put": "C",
            "action": "selltoclose",
            "quantity": "1.0",
            "open_close": "C",
        },
        {
            "underlying_symbol": "XSP",
            "strike_price": "770.0",
            "call_or_put": "C",
            "action": "buytoclose",
            "quantity": "2.0",
            "open_close": "C",
        },
    ]
    assert "$0.87 cr" in ff.format_order(_order(6, order_legs=legs, order_type="net_credit"), {})


def test_stock_counts_in_shares_not_contracts():
    """The feed tags equity legs "S", not "E". Checking only "E" printed a 100-share trade as
    "100×" — read as a 100-lot, a 100x overstatement of the position."""
    legs = [
        {
            "underlying_symbol": "NVTS",
            "symbol": "NVTS",
            "asset_type": "S",
            "action": "selltoclose",
            "quantity": "100.0",
            "open_close": "C",
        }
    ]
    assert ff._quantity(_order(7, order_legs=legs)) == "100 sh"


def test_butterfly_reports_the_wing_size_not_the_bodys_doubled_quantity():
    """A 1-lot butterfly's body leg is -2 against two +1 wings by construction, not because two
    butterflies were bought. The raw max of the legs (2) overstated a screenshot-verified 1-lot
    337.5/340/342.5 put fly as '2×'."""
    legs = [
        {"strike_price": "337.5", "call_or_put": "P", "action": "buytoopen", "quantity": "1.0"},
        {"strike_price": "340.0", "call_or_put": "P", "action": "selltoopen", "quantity": "2.0"},
        {"strike_price": "342.5", "call_or_put": "P", "action": "buytoopen", "quantity": "1.0"},
    ]
    assert ff._quantity(_order(20, order_legs=legs)) == "1×"


def test_scaled_butterfly_still_reports_its_true_lot_count():
    """The max-based distortion scales with size: 5 lots of the same fly is 5/-10/5, which a raw
    max would wrongly report as '10×'. The GCD recovers the true lot count at any size."""
    legs = [
        {"strike_price": "337.5", "call_or_put": "P", "action": "buytoopen", "quantity": "5.0"},
        {"strike_price": "340.0", "call_or_put": "P", "action": "selltoopen", "quantity": "10.0"},
        {"strike_price": "342.5", "call_or_put": "P", "action": "buytoopen", "quantity": "5.0"},
    ]
    assert ff._quantity(_order(21, order_legs=legs)) == "5×"


def test_two_leg_ratio_spread_still_reports_its_widest_side():
    """Only 3+-leg structures use the GCD. A two-leg combo has no symmetric 'base structure' for a
    GCD to recover cleanly, so it keeps the pre-existing widest-leg behavior."""
    legs = [
        {"strike_price": "300.0", "call_or_put": "C", "action": "selltoopen", "quantity": "1.0"},
        {"strike_price": "310.0", "call_or_put": "C", "action": "buytoopen", "quantity": "3.0"},
    ]
    assert ff._quantity(_order(22, order_legs=legs)) == "3×"


def test_futures_price_is_a_level_not_a_credit():
    """Nobody pays $29,728.75 to buy one MNQ — that is a quoted level, not cash. Tagging it "cr"
    put a five-figure credit next to a $1.46 butterfly and read as a windfall."""
    legs = [
        {
            "underlying_symbol": "/MNQU6",
            "symbol": "/MNQU6",
            "asset_type": "/",
            "action": "sell",
            "quantity": "1.0",
            "open_close": "O",
        }
    ]
    line = ff.format_order(_order(9, order_legs=legs, order_type="market", price_string="29,728.75"), {})
    assert "$29,728.75" in line
    assert " cr" not in line and " db" not in line
    # The side has to survive somewhere: the leg body is suppressed (it is just the underlying), so
    # the size carries it. Losing db/cr must not lose long-vs-short.
    assert "-1×" in line


def test_futures_options_still_quote_in_dollars():
    """A futures OPTION is priced in cash like anything else — only an all-futures order is a level.
    A bare startswith("/") on the underlying wrongly stripped its db/cr."""
    legs = [
        {
            "underlying_symbol": "/MNQU6",
            "symbol": "./MNQU6 P29000",
            "asset_type": "O",
            "strike_price": "29000.0",
            "call_or_put": "P",
            "action": "buytoopen",
            "quantity": "1.0",
            "open_close": "O",
        }
    ]
    assert "$4.25 db" in ff.format_order(_order(10, order_legs=legs, price_string="4.25"), {})


def test_stock_keeps_its_credit_and_gains_a_side():
    """Selling stock really does credit the account, so db/cr stays. The sign on the size is new —
    an equity leg's body is suppressed, so nothing else said which way it went."""
    legs = [
        {
            "underlying_symbol": "ETHA",
            "symbol": "ETHA",
            "asset_type": "S",
            "action": "selltoclose",
            "quantity": "100.0",
            "open_close": "C",
        }
    ]
    line = ff.format_order(_order(11, order_legs=legs, order_type="limit", price_string="14.30"), {})
    assert "-100 sh" in line and "$14.30 cr" in line


def test_futures_spot_keeps_its_slash_prefixed_symbol():
    """A futures symbol is itself slash-prefixed, so splitting the joined underlyings on "/" gave an
    empty symbol and printed a bare price."""
    legs = [{"underlying_symbol": "/MNQU6", "action": "buytoopen", "quantity": "1.0", "open_close": "O"}]
    assert ff._spot(_order(8, order_legs=legs, underlying_price_string="29580.50")) == "/MNQU6 29580.50"


# --------------------------------------------------------------------------- formatting: the rationale
def test_reason_is_rendered_when_the_order_carries_no_comment_thread():
    """`reason` is the order's own rationale field and is populated far more often than `comments`
    (23 of 50 against 7 on one live pull), so reading only the thread dropped it on roughly a third
    of the feed — and on a CLOSE it is the whole point of the card."""
    line = ff.format_order(_order(1, comments=[], reason="Closed for 50% gain"), {})
    assert line.endswith("> Closed for 50% gain")


def test_comment_and_reason_both_render_when_they_differ():
    """Neither field subsumes the other — a trader can leave a thread comment and an order reason
    that say different things, so showing one and dropping the other loses real rationale."""
    line = ff.format_order(
        _order(1, comments=[{"body": "Taking it off early."}], reason="Closed for 50% gain"), {}
    )
    assert line.split("\n")[-2:] == ["> Taking it off early.", "> Closed for 50% gain"]


def test_a_reason_repeating_the_comment_is_shown_once():
    """The platform often copies the same text into both; whitespace and case shouldn't defeat that."""
    line = ff.format_order(
        _order(1, comments=[{"body": "Closed for 50% gain"}], reason="  closed for 50%   GAIN "), {}
    )
    assert line.count("> ") == 1


def test_long_reason_is_truncated_on_its_own():
    line = ff.format_order(_order(1, comments=[], reason="x" * 400), {})
    note = line.split("\n")[-1]
    assert note.endswith("…") and len(note) == 222  # "> " + 220


def test_embed_description_carries_both_rationale_lines():
    embed = ff.build_embed(
        _order(1, comments=[{"body": "Taking it off early."}], reason="Closed for 50% gain"), {}
    )
    assert embed["description"] == "> Taking it off early.\n> Closed for 50% gain"


def test_no_rationale_leaves_the_description_off_entirely():
    embed = ff.build_embed(_order(1, comments=[], reason=None), {})
    assert "description" not in embed


def test_replay_last_reposts_regardless_of_seen_state_and_the_enabled_gate(wired, monkeypatch):
    _feed(monkeypatch, [_order(1), _order(2), _order(3)])
    ff.run(_cfg())  # seed everything
    res = ff.run(_cfg(enabled=False), replay_last=2)  # disabled + fully seen: replay still renders
    assert res == {"ok": True, "replayed": 2, "dry_run": False, "state_untouched": True}
    assert [k for k, _, _, _ in wired] == ["follow.order.2", "follow.order.3"]
    _key, _msg, embed, identity = wired[-1]
    assert identity["username"] == "tastylive Follow"
    assert embed["title"].endswith("PLTR Vertical")
