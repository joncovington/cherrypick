"""Section adapter — map build_gex output onto the cherrypick.core.viz declarative section schema.

This is the module's integration point with the umbrella's generic dashboard: `run.py section --json`
emits a schema-conforming payload (metrics tiles + a signed net-GEX-by-strike bar series, OI vs volume),
which the umbrella renders with no GEX-specific code. The rich interactive Chart.js view stays in this
module's own `dashboard --serve`; this is the compact card for the suite dashboard.
"""

from __future__ import annotations

import service as _service

_NOTE = "positioning = open interest · flow = traded volume · a simple self-hosted gexbot / SpotGamma / MenthorQ"


def _fmt(v) -> str:
    if v is None:
        return "–"
    a = abs(v)
    s = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}{a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}{a / 1e3:.0f}K"
    return f"{v:.0f}"


def _val(x) -> str:
    return "–" if x is None else str(x)


def build_section(cfg: dict, symbol: str | None = None) -> dict:
    """Return a cherrypick.core.viz section payload for `symbol`, or {ok: False, error}."""
    d = _service.build_gex(cfg, symbol)
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error", "GEX unavailable")}

    t = d.get("totals", {})
    series = d.get("series", [])
    spot = d.get("underlying_price")
    net = t.get("net_gex") or 0
    flip = t.get("zero_gamma")
    return {
        "ok": True,
        "title": f"GEX — {d['symbol']}",
        "subtitle": f"exp {d.get('expiration', '?')} · {len(series)} strikes · {d.get('source', '')}",
        "metrics": [
            {"label": "Spot", "value": f"{spot:.2f}" if spot is not None else "–", "tone": "accent"},
            {"label": "Net GEX", "value": _fmt(t.get("net_gex")), "tone": "pos" if net >= 0 else "neg"},
            {"label": "Gamma Flip", "value": f"{flip:.2f}" if flip is not None else "–"},
            {"label": "Call Wall", "value": _val(t.get("call_wall")), "tone": "pos"},
            {"label": "Put Wall", "value": _val(t.get("put_wall")), "tone": "neg"},
        ],
        "bars": {
            "labels": [s["strike"] for s in series],
            "focus": spot,
            "series": [
                {"name": "Net GEX (OI)", "values": [s["net_gex"] for s in series], "tone_by_sign": True},
                {"name": "Net GEX (Volume)", "values": [s["net_gex_vol"] for s in series], "tone": "vol"},
            ],
        },
        "note": _NOTE,
    }
