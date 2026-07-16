"""Adapter mapping: build_gex payload -> cherrypick.core.viz section schema (no cache needed)."""

import section

_CANNED = {
    "ok": True, "symbol": "SPX", "expiration": "2026-07-10", "underlying_price": 7575.39,
    "source": "stream_cache",
    "series": [{"strike": 7500, "net_gex": -1e9, "net_gex_vol": -2e8},
               {"strike": 7600, "net_gex": 3e9, "net_gex_vol": 5e8}],
    "totals": {"net_gex": 2e9, "zero_gamma": 7470.0, "call_wall": 7600, "put_wall": 7525},
}


def test_build_section_maps_to_schema(monkeypatch):
    monkeypatch.setattr(section._service, "build_gex", lambda cfg, sym: _CANNED)
    out = section.build_section({}, "SPX")
    assert out["ok"] is True and out["title"] == "GEX — SPX"
    assert "2 strikes" in out["subtitle"]
    m = {x["label"]: x for x in out["metrics"]}
    assert m["Spot"]["value"] == "7575.39" and m["Spot"]["tone"] == "accent"
    assert m["Net GEX"]["value"] == "2.00B" and m["Net GEX"]["tone"] == "pos"
    assert m["Gamma Flip"]["value"] == "7470.00"
    assert m["Call Wall"]["value"] == "7600" and m["Put Wall"]["value"] == "7525"
    b = out["bars"]
    assert b["labels"] == [7500, 7600] and b["focus"] == 7575.39
    assert b["series"][0]["name"] == "Net GEX (OI)" and b["series"][0]["tone_by_sign"] is True
    assert b["series"][1]["tone"] == "vol" and b["series"][1]["values"] == [-2e8, 5e8]


def test_build_section_negative_net_gex_tone(monkeypatch):
    canned = dict(_CANNED, totals=dict(_CANNED["totals"], net_gex=-5e8))
    monkeypatch.setattr(section._service, "build_gex", lambda cfg, sym: canned)
    m = {x["label"]: x for x in section.build_section({})["metrics"]}
    assert m["Net GEX"]["tone"] == "neg" and m["Net GEX"]["value"] == "-500.0M"


def test_build_section_passes_through_error(monkeypatch):
    monkeypatch.setattr(section._service, "build_gex", lambda cfg, sym: {"ok": False, "error": "no cache"})
    out = section.build_section({}, "SPX")
    assert out["ok"] is False and "no cache" in out["error"]
