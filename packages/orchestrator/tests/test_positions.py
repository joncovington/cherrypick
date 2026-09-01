"""Tests for `cherrypick positions` (orchestrator.positions).

Unit lane: the per-leg arithmetic, the cache-first/feed-second mark resolution, and the two honesty rules
the report exists to keep (an unpriced leg is excluded and counted, never zeroed; a wide market is
flagged with the doubt it carries). The broker and the shared cache are both stubbed, so no checkout, no
credential, and no market are needed.
"""

import pytest

from cherrypick.orchestrator import positions

pytestmark = pytest.mark.unit


def _position(symbol, *, direction="Long", quantity="1", open_price="1.00", close_price="1.00", **kw):
    """A position shaped like the broker's own payload — every number a string, direction separate."""
    out = {
        "symbol": symbol,
        "instrument_type": "Equity Option",
        "underlying_symbol": symbol[:6].strip(),
        "quantity": quantity,
        "quantity_direction": direction,
        "average_open_price": open_price,
        "close_price": close_price,
        "multiplier": "100.0",
        "expires_at": "2026-09-18T20:15:00Z",
    }
    out.update(kw)
    return out


def _mark(mid, *, bid=None, ask=None, source="feed"):
    return {"mid": mid, "bid": bid, "ask": ask, "age_seconds": 0.0, "source": source}


# --------------------------------------------------------------------------- per-leg arithmetic
def test_a_long_leg_gains_when_the_mark_rises():
    leg = positions._price_leg(_position("APO   260918C00125000", open_price="6.28"), _mark(10.90))
    assert leg["quantity"] == 1
    assert leg["value"] == pytest.approx(1090.0)
    assert leg["open_pl"] == pytest.approx(462.0)


def test_a_short_leg_loses_when_the_mark_rises():
    """The sign lives in `quantity_direction`, not in `quantity` — the broker sends both, and reading only
    the magnitude would report a losing short as a winner."""
    leg = positions._price_leg(
        _position("APO   260918C00120000", direction="Short", open_price="8.73"), _mark(14.55)
    )
    assert leg["quantity"] == -1
    assert leg["value"] == pytest.approx(-1455.0)
    assert leg["open_pl"] == pytest.approx(-582.0)


def test_day_pl_is_measured_from_the_prior_close():
    leg = positions._price_leg(
        _position("SPY   260821C00780000", direction="Short", open_price="1.35", close_price="0.69"),
        _mark(0.20),
    )
    assert leg["open_pl"] == pytest.approx(115.0)
    assert leg["day_pl"] == pytest.approx(49.0)


def test_an_equity_leg_uses_a_multiplier_of_one():
    """Shares are not contracts. A multiplier applied here would report 100 shares as 10,000."""
    share = _position("SCHD", instrument_type="Equity", quantity="100", open_price="32.66")
    share["expires_at"] = None
    leg = positions._price_leg(share, _mark(34.56))
    assert leg["value"] == pytest.approx(3456.0)
    # No strike/right at all rather than a null one: the renderer keys off its absence to print "shares".
    assert leg.get("strike") is None and leg.get("right") is None


def test_an_unpriced_leg_carries_no_value_at_all():
    """Not zero — absent. A zero would be summed into the totals as though the position were worthless."""
    leg = positions._price_leg(_position("APO   260918C00125000"), None)
    assert leg["priced"] is False
    assert "value" not in leg and "open_pl" not in leg


# --------------------------------------------------------------------------- wide-market flagging
def test_a_wide_market_is_flagged_with_the_doubt_it_carries():
    """The APO case this was built from: a 13.40/15.70 market whose mid is worth +/-$115 per contract."""
    leg = positions._price_leg(
        _position("APO   260918C00120000", direction="Short"), _mark(14.55, bid=13.40, ask=15.70)
    )
    assert leg["wide"] is True
    assert leg["mark_doubt"] == pytest.approx(115.0)


def test_a_cheap_penny_wide_leg_is_not_flagged():
    """A 0.01/0.03 far wing is 100% wide by ratio and $1 of doubt. Flagging it would train the reader to
    ignore the flag on the legs where it means something."""
    leg = positions._price_leg(_position("F     260918C00015000"), _mark(0.02, bid=0.01, ask=0.03))
    assert leg["wide"] is False


def test_a_tight_market_is_not_flagged():
    leg = positions._price_leg(_position("SPY   260824P00770000"), _mark(4.13, bid=4.12, ask=4.14))
    assert leg["wide"] is False


# --------------------------------------------------------------------------- grouping
def test_underlyings_are_ordered_by_the_size_of_the_move_either_way():
    """Ranked by magnitude, not signed value: the reader's question is "what needs attention", and a big
    winner is as worth surfacing as a big loser."""
    legs = [
        {"underlying": "SMALL", "priced": True, "open_pl": -5.0, "day_pl": 0.0, "value": 0.0},
        {"underlying": "WINNER", "priced": True, "open_pl": 400.0, "day_pl": 0.0, "value": 0.0},
        {"underlying": "LOSER", "priced": True, "open_pl": -300.0, "day_pl": 0.0, "value": 0.0},
    ]
    assert [g["underlying"] for g in positions._group_by_underlying(legs)] == ["WINNER", "LOSER", "SMALL"]


def test_an_unpriced_leg_is_counted_on_its_underlying_and_left_out_of_the_sums():
    legs = [
        {"underlying": "APO", "priced": True, "open_pl": 10.0, "day_pl": 1.0, "value": 100.0},
        {"underlying": "APO", "priced": False},
    ]
    group = positions._group_by_underlying(legs)[0]
    assert group["leg_count"] == 2
    assert group["unpriced"] == 1
    assert group["open_pl"] == pytest.approx(10.0)


# --------------------------------------------------------------------------- mark resolution
def _stub_broker(monkeypatch, positions_payload, quotes=None, *, record=None):
    """Stub the two broker touchpoints reconcile owns, plus the module-resolution it does on the way."""
    monkeypatch.setattr(
        positions.reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "module": "meic",
            "accounts": [
                {
                    "account": "****2375",
                    "designated": True,
                    "open_positions": positions_payload,
                    "balances": {"net-liquidating-value": "14744.52"},
                    "open_count": len(positions_payload),
                }
            ],
        },
    )

    def fake_tt(root, *argv, tool=None):
        if record is not None:
            record.append(list(argv))
        return {"ok": True, "quotes": quotes or {}, "missing": []}

    monkeypatch.setattr(positions.reconcile, "_tt", fake_tt)
    monkeypatch.setattr(positions.cfgmod, "enabled_modules", lambda cfg: {"meic": {}})
    monkeypatch.setattr(positions.cfgmod, "module_root", lambda mcfg, name: positions.data_dir("x"))
    monkeypatch.setattr(positions.cfgmod, "broker_tool", lambda mcfg, name: ["-m", "x"])


def test_the_cache_is_consulted_first_and_only_the_misses_go_to_the_feed(monkeypatch):
    """The suite-wide rule, asserted on the actual call: a symbol the cache answered must not appear in
    the argv handed to the broker tool."""
    asked = []
    _stub_broker(
        monkeypatch,
        [
            _position("APO   260918C00120000", direction="Short"),
            _position("APO   260918C00125000"),
        ],
        quotes={".APO260918C125": {"bid": 10.40, "ask": 11.40, "mid": 10.90}},
        record=asked,
    )
    monkeypatch.setattr(
        positions, "_cached_marks", lambda syms: {".APO260918C120": _mark(14.55, source="stream_cache")}
    )

    result = positions.run({})
    assert result["marks"]["from_cache"] == 1
    assert result["marks"]["from_feed"] == 1
    quote_calls = [a for a in asked if a and a[0] == "get_quotes"]
    assert quote_calls == [["get_quotes", "--symbols", ".APO260918C125"]]


def test_no_feed_call_at_all_when_the_cache_answers_everything(monkeypatch):
    asked = []
    _stub_broker(monkeypatch, [_position("APO   260918C00125000")], record=asked)
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {".APO260918C125": _mark(10.90)})

    positions.run({})
    assert [a for a in asked if a and a[0] == "get_quotes"] == []


def test_a_leg_neither_source_can_price_is_reported_not_zeroed(monkeypatch):
    _stub_broker(monkeypatch, [_position("APO   260918C00125000", open_price="6.28")], quotes={})
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {})

    result = positions.run({})
    account = result["accounts"][0]
    assert account["unpriced_count"] == 1
    assert account["open_pl"] == 0.0  # the sum of nothing, with the omission stated alongside
    assert "UNPRICED" in positions.format_report(result)
    assert "EXCLUDED from the totals" in positions.format_report(result)


def test_the_same_symbol_in_two_accounts_is_quoted_once(monkeypatch):
    """One market-data pass for the whole login: quoting a symbol twice buys a second subscription for an
    identical answer."""
    asked = []
    monkeypatch.setattr(
        positions.reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "module": "meic",
            "accounts": [
                {"account": "****2375", "open_positions": [_position("APO   260918C00125000")]},
                {"account": "****9999", "open_positions": [_position("APO   260918C00125000")]},
            ],
        },
    )

    def fake_tt(root, *argv, tool=None):
        asked.append(list(argv))
        return {"ok": True, "quotes": {".APO260918C125": {"bid": 10.4, "ask": 11.4, "mid": 10.90}}}

    monkeypatch.setattr(positions.reconcile, "_tt", fake_tt)
    monkeypatch.setattr(positions.cfgmod, "enabled_modules", lambda cfg: {"meic": {}})
    monkeypatch.setattr(positions.cfgmod, "module_root", lambda mcfg, name: positions.data_dir("x"))
    monkeypatch.setattr(positions.cfgmod, "broker_tool", lambda mcfg, name: ["-m", "x"])
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {})

    result = positions.run({})
    assert result["marks"]["requested"] == 1
    assert [a for a in asked if a and a[0] == "get_quotes"] == [
        ["get_quotes", "--symbols", ".APO260918C125"]
    ]
    assert len(result["accounts"]) == 2


def test_the_account_filter_takes_a_last_4(monkeypatch):
    """Callers should never need a full account number to ask for a report about one."""
    monkeypatch.setattr(
        positions.reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "module": "meic",
            "accounts": [
                {"account": "****2375", "open_positions": []},
                {"account": "****9999", "open_positions": []},
            ],
        },
    )
    monkeypatch.setattr(positions.reconcile, "_tt", lambda *a, **k: {"ok": True, "quotes": {}})
    monkeypatch.setattr(positions.cfgmod, "enabled_modules", lambda cfg: {"meic": {}})
    monkeypatch.setattr(positions.cfgmod, "module_root", lambda mcfg, name: positions.data_dir("x"))
    monkeypatch.setattr(positions.cfgmod, "broker_tool", lambda mcfg, name: ["-m", "x"])
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {})

    result = positions.run({}, account="2375")
    assert [a["account"] for a in result["accounts"]] == ["****2375"]


# --------------------------------------------------------------------------- failure + masking
def test_an_unreachable_broker_reports_rather_than_raises(monkeypatch):
    monkeypatch.setattr(
        positions.reconcile,
        "_query_broker",
        lambda cfg, forced: {"reachable": False, "detail": "no positions-capable module found"},
    )
    result = positions.run({})
    assert result["ok"] is False
    assert "broker unreachable" in positions.format_report(result)


def test_no_full_account_number_reaches_the_report(monkeypatch):
    """Suite-wide masking rule. reconcile hands back masked accounts; this asserts nothing here undoes
    that by reaching for a raw number out of a broker payload."""
    full = "5WI62375"
    _stub_broker(
        monkeypatch,
        [_position("APO   260918C00125000", account_number=full)],
        quotes={".APO260918C125": {"bid": 10.4, "ask": 11.4, "mid": 10.90}},
    )
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {})

    result = positions.run({})
    assert full not in positions.format_report(result, detail=True)
    assert full not in repr(result["accounts"])
