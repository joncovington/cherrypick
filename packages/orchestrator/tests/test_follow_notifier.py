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
        "is_earnings_play": False,
        "order_legs": [
            {
                "underlying_symbol": "PLTR",
                "strike_price": "122.0",
                "call_or_put": "P",
                "expiration_date": "2026-08-07",
                "open_close": "O",
            },
            {
                "underlying_symbol": "PLTR",
                "strike_price": "117.0",
                "call_or_put": "P",
                "expiration_date": "2026-08-07",
                "open_close": "O",
            },
        ],
        "comments": [],
    }
    o.update(over)
    return o


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
        lambda self, level, key, title, message: sent.append((key, message)) or {"log": {"ok": True}},
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
    assert [k for k, _ in wired] == ["follow.order.29", "follow.order.30", "follow.order.31"]

    # Suppressed ids are watermarked, not left to resurface on the next tick.
    assert ff.run(_cfg(max_per_run=3))["notified"] == 0


def test_orders_push_in_chronological_order(wired, monkeypatch):
    _feed(monkeypatch, [_order(1)])
    ff.run(_cfg())
    _feed(monkeypatch, [_order(1), _order(5), _order(3), _order(4)])
    ff.run(_cfg())
    assert [k for k, _ in wired] == ["follow.order.3", "follow.order.4", "follow.order.5"]


def test_lock_blocks_a_concurrent_run(wired, monkeypatch):
    _feed(monkeypatch, [_order(1)])
    assert ff._acquire_lock()
    try:
        assert ff.run(_cfg())["skipped"] == "another follow-notify run holds the lock"
    finally:
        ff._release_lock()


# --------------------------------------------------------------------------- formatting
def test_format_reads_like_the_follow_page():
    line = ff.format_order(
        _order(
            1,
            order_type="net_debit",
            price_string="0.01",
            is_earnings_play=True,
            comments=[{"body": "Taking max profit after the earnings move."}],
        ),
        {166462: "Jim Schultz"},
    )
    assert "Jim Schultz Bought Vertical" in line
    assert "PLTR" in line and "122P/117P" in line and "2026-08-07" in line
    assert "$0.01 db" in line
    assert "[OPEN Earnings]" in line
    assert "Taking max profit" in line


def test_closing_order_is_tagged_close_and_credit():
    legs = [
        {
            "underlying_symbol": "XSP",
            "strike_price": "770.0",
            "call_or_put": "C",
            "expiration_date": "2026-08-04",
            "open_close": "C",
        }
    ]
    line = ff.format_order(_order(2, order_legs=legs), {166462: "Jim Schultz"})
    assert "Sold Vertical" in line and "$0.87 cr" in line and "[CLOSE]" in line


def test_mixed_open_close_gets_no_tag():
    """A roll touches both. No tag beats a wrong one."""
    legs = [
        {"underlying_symbol": "SPX", "strike_price": "6300.0", "call_or_put": "P", "open_close": "C"},
        {"underlying_symbol": "SPX", "strike_price": "6250.0", "call_or_put": "P", "open_close": "O"},
    ]
    assert "[" not in ff.format_order(_order(3, order_legs=legs), {166462: "Jim"})


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
        }
    ]
    line = ff.format_order(_order(5, order_legs=legs, order_type="limit", price_string="12.65"), {})
    assert line.count("NVTS") == 1


def test_single_leg_limit_order_reads_as_bought_or_sold():
    """Single-leg orders carry order_type "limit", not net_debit/net_credit. Direction and the db/cr
    suffix both come from the leg action instead — otherwise the feed's "Sold Stock" rendered as
    "Limit Stock" with no suffix at all."""
    stock = [{"underlying_symbol": "NVTS", "symbol": "NVTS", "action": "selltoclose", "open_close": "C"}]
    line = ff.format_order(
        _order(6, order_legs=stock, order_type="limit", strategy="Stock", price_string="12.65"), {}
    )
    assert "Sold Stock" in line and "$12.65 cr" in line

    opt = [
        {
            "underlying_symbol": "IWM",
            "strike_price": "300.0",
            "call_or_put": "C",
            "action": "buytoopen",
            "open_close": "O",
        }
    ]
    line = ff.format_order(
        _order(7, order_legs=opt, order_type="limit", strategy="Option", price_string="9.61"), {}
    )
    assert "Bought Option" in line and "$9.61 db" in line


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
