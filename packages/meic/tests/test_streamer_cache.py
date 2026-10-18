"""Unit tests for MEIC's rollback streamer (cherrypick.meic.streamer): which symbols it streams and
what it subscribes. The REST cache and the 127.0.0.1:7699 API it used to serve were removed with the
sidecar on 2026-10-18, and their tests with them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cherrypick.meic import streamer as _streamer


class TestConfiguredSymbols:
    def test_symbols_list_uppercased(self):
        assert _streamer._configured_symbols({"symbols": ["xsp", "spx"]}) == ["XSP", "SPX"]

    def test_deprecated_singular_symbol_alias(self):
        assert _streamer._configured_symbols({"symbol": "xsp"}) == ["XSP"]

    def test_symbols_takes_precedence_over_singular(self):
        assert _streamer._configured_symbols({"symbols": ["SPX"], "symbol": "XSP"}) == ["SPX"]

    def test_default_when_config_empty(self):
        assert _streamer._configured_symbols({}) == ["XSP"]

    def test_cli_override_takes_precedence(self):
        assert _streamer._configured_symbols({"symbols": ["XSP"]}, cli_override=["spx", "ndx"]) == [
            "SPX",
            "NDX",
        ]

    def test_blank_entries_filtered(self):
        assert _streamer._configured_symbols({"symbols": ["XSP", " ", "SPX"]}) == ["XSP", "SPX"]


# ---------------------------------------------------------------------------
# Multi-symbol subscription resolution
# ---------------------------------------------------------------------------


class TestResolveSubscriptionsMultiSymbol:
    def test_trade_subscribes_every_symbol(self):
        subs = _streamer._resolve_subscriptions(["XSP", "SPX", "NDX"])
        assert subs["Trade"] == ["XSP", "SPX", "NDX"]

    def test_summary_seeds_every_symbol(self):
        subs = _streamer._resolve_subscriptions(["XSP", "SPX"])
        assert "XSP" in subs["Summary"]
        assert "SPX" in subs["Summary"]

    def test_single_symbol_still_a_list(self):
        subs = _streamer._resolve_subscriptions(["XSP"])
        assert subs["Trade"] == ["XSP"]


# ---------------------------------------------------------------------------
# Multi-symbol cache isolation — two symbols' chains must never cross-contaminate
# ---------------------------------------------------------------------------
