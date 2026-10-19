"""The read behind a setup row: why it fired, what its rule has earned, what the direction and the
volatility say, where the levels sit, and the kind of option trade the read points to.

Written into each row of the setups watchlist (`watchlist.py`) as `read`, so the console lays out
cards and decides nothing. A pure function of data the suite already holds: the chart file (rule,
bars, levels, trend, rank), the historical study's published results, the signal board, the broker's
nightly IV reading and the skate bands. No broker call, no network.

**The structure lean is a kind of trade, never a contract.** Direction x volatility regime x the
earnings calendar picks a family (a debit spread toward the target, a credit spread past a level),
with the levels it would be built against. Every lean restates the setup's own record beside it:
most of these setups tested no better than random entry (docs/setups.md), and a lean on a rule with
no edge is a structure for the read, not a reason to trade.

**The volatility regime reads both IV rank and IV percentile** (the user's choice, 2026-10-18):
rich when both are at or above `rich`, cheap when both are under `cheap`, middling otherwise, and
unread when either is missing. The expected move is to the user's cycle monthly (45-60 days if one
is listed, else 30-60): the lean describes an options trade, and that is the cycle traded.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from . import earnings_calendar, paths, skate

DEFAULTS: dict[str, Any] = {"cheap": 33.0, "rich": 50.0}

# The historical study's results (docs/setups.md, "Historical evidence" 2026-10-04 and "Round 2"):
# R per ATR(14), net of costs, stock fills at the next open, 2011-2026, against a matched random
# baseline. Keyed (family, side); the tested rule by its id.
TRACK_RECORD: dict[Any, dict[str, Any]] = {
    ("trend", "long"): {
        "entries": 31256,
        "net_r": 0.045,
        "edge_r": -0.577,
        "t": -7.6,
        "verdict": "worse than random",
    },
    ("pullback", "long"): {
        "entries": 56634,
        "net_r": -0.058,
        "edge_r": -0.202,
        "t": -8.8,
        "verdict": "worse than random",
    },
    ("reversion", "long"): {
        "entries": 47059,
        "net_r": 0.015,
        "edge_r": 0.066,
        "t": 2.5,
        "verdict": "edge over random, barely profitable",
    },
    ("breakout", "long"): {"entries": 4328, "net_r": 0.138, "edge_r": 0.020, "t": 0.2, "verdict": "no edge"},
    ("trend", "short"): {
        "entries": 30135,
        "net_r": -0.416,
        "edge_r": -0.028,
        "t": -1.2,
        "verdict": "no edge",
    },
    ("pullback", "short"): {
        "entries": 40227,
        "net_r": -0.363,
        "edge_r": 0.056,
        "t": 2.6,
        "verdict": "edge over random, but loses money",
    },
    ("reversion", "short"): {
        "entries": 70838,
        "net_r": -0.369,
        "edge_r": -0.130,
        "t": -5.9,
        "verdict": "worse than random",
    },
    ("breakout", "short"): {
        "entries": 4293,
        "net_r": -0.867,
        "edge_r": -0.198,
        "t": -2.4,
        "verdict": "no edge",
    },
    "mr-300m": {
        "entries": 2880,
        "net_r": 0.235,
        "edge_r": 0.182,
        "t": 3.05,
        "hit": 0.62,
        "median_hold": 10,
        "verdict": (
            "the one confirmed edge (on names it had not seen); stock fills, the options expression untested"
        ),
    },
}


def settings(cfg: dict | None) -> dict[str, Any]:
    return {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if not k.startswith("_")}}


def load_settings() -> dict[str, Any]:
    from cherrypick.core import home as _home

    return settings(_home.load_module_config("technicals", skate.PKG_ROOT).get("setup_read"))


# ------------------------------------------------------------------------------------------------
# Pure pieces.


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    """Wilder's ATR over the last bars; None with too few."""
    if len(closes) <= n:
        return None
    tr = [highs[0] - lows[0]] + [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    a = sum(tr[1 : n + 1]) / n
    for x in tr[n + 1 :]:
        a = (a * (n - 1) + x) / n
    return a


def regime(iv_rank: float | None, iv_pct: float | None, cfg: dict) -> str:
    if iv_rank is None or iv_pct is None:
        return "unread"
    if iv_rank >= cfg["rich"] and iv_pct >= cfg["rich"]:
        return "rich"
    if iv_rank < cfg["cheap"] and iv_pct < cfg["cheap"]:
        return "cheap"
    return "middling"


def cycle_monthly(day: date) -> tuple[str, int] | None:
    """The user's cycle monthly from `day`: (expiry, calendar days)."""
    months = []
    for k in range(1, 4):
        y, m = day.year + (day.month - 1 + k) // 12, (day.month - 1 + k) % 12 + 1
        first = date(y, m, 1)
        months.append(first)
    exps = []
    for first in [date(day.year, day.month, 1), *months]:
        for d in range(15, 22):
            cand = first.replace(day=d)
            if skate.is_monthly(cand):
                exps.append(cand.isoformat())
    exp = skate.cycle_expiry(sorted(set(exps)), day)
    return (exp, (date.fromisoformat(exp) - day).days) if exp else None


def direction(row: dict, board: dict | None) -> dict[str, Any]:
    """The setup's side against our trend scores and the signal board: agrees, conflicts or mixed.
    Mean reversion fades the move by design -- an oversold long always has a falling trend -- so its
    trend is shown but does not vote; the board alone does."""
    side = row["side"]
    want = "bullish" if side == "long" else "bearish"
    counter = row.get("family") == "reversion"
    t1, t6 = row.get("trend_1m"), row.get("trend_6m")
    against = (
        None if t1 is None or t6 is None else (t1 < 0 and t6 < 0 if side == "long" else t1 > 0 and t6 > 0)
    )
    b_side = (board or {}).get("side")
    votes = []
    if row.get("trend_agrees") is not None and not counter:
        votes.append(1 if row["trend_agrees"] else (-1 if against else 0))
    if board is not None:
        votes.append(0 if b_side is None else (1 if b_side == want else -1))
    if not votes:
        verdict = "unread"
    elif all(v > 0 for v in votes):
        verdict = "agrees"
    elif any(v < 0 for v in votes) and not any(v > 0 for v in votes):
        verdict = "conflicts"
    else:
        verdict = "mixed"
    return {
        "verdict": verdict,
        "counter_trend": counter,
        "trend_agrees": row.get("trend_agrees"),
        "trend_against": against,
        "board": None
        if board is None
        else {k: board.get(k) for k in ("side", "count", "bullish", "bearish")}
        | {"tags": [t.get("label") for t in board.get("tags") or []]},
    }


def volatility(spot: float | None, iv: dict | None, day: date, cfg: dict) -> dict[str, Any]:
    """The broker's IV reading for the name, its regime, and the expected move to the cycle monthly."""
    iv = iv or {}
    iv_index = iv.get("iv_index")
    hv = iv.get("hv_30")  # a percentage, as the broker sends it
    cyc = cycle_monthly(day)
    move = None
    if cyc and spot and iv_index:
        dollars = spot * iv_index * math.sqrt(cyc[1] / 365)
        move = {"dollars": round(dollars, 2), "pct": round(100 * dollars / spot, 2)}
    earnings = iv.get("earnings_date")
    in_cycle = None if not cyc or not earnings else day.isoformat() <= earnings <= cyc[0]
    exdiv = iv.get("ex_dividend_date")
    return {
        "iv": None if iv_index is None else round(100 * iv_index, 1),
        "iv_rank": iv.get("iv_rank"),
        "iv_pct": iv.get("iv_percentile"),
        "hv30": hv,
        "iv_hv": None if not iv_index or not hv else round(100 * iv_index / hv, 2),
        "regime": regime(iv.get("iv_rank"), iv.get("iv_percentile"), cfg),
        "cycle": None if not cyc else {"expiry": cyc[0], "days": cyc[1]},
        "expected_move": move,
        "earnings": earnings,
        "earnings_in_cycle": in_cycle,
        "ex_dividend": exdiv if exdiv and exdiv >= day.isoformat() else None,
    }


def levels(row: dict, atr14: float | None, move: dict | None) -> dict[str, Any]:
    """Support, resistance and the target as % away, in ATRs and in sigmas of the expected move."""
    close = row.get("last_close")

    def at(value: float | None) -> dict | None:
        if value is None or not close:
            return None
        gap = value - close
        return {
            "value": value,
            "pct": round(100 * gap / close, 2),
            "atr": None if not atr14 else round(gap / atr14, 2),
            "sigma": None if not move or not move["dollars"] else round(gap / move["dollars"], 2),
        }

    return {
        "atr14": None if atr14 is None else round(atr14, 4),
        "support": at((row.get("support") or {}).get("value")),
        "resistance": at((row.get("resistance") or {}).get("value")),
        "target": at(row.get("target")) if row.get("status") == "open" else None,
    }


def lean(
    row: dict, vol: dict, dirn: dict, lv: dict, band: dict | None, record: dict | None
) -> dict[str, Any]:
    """The kind of option trade the read points to, with the levels it would be built against."""
    long_ = row["side"] == "long"
    reg = vol["regime"]
    cautions: list[str] = []
    if record is not None and (record.get("edge_r") or 0) <= 0:
        cautions.append(
            f"this setup tested {record['verdict']} ({record['edge_r']:+.3f} R against random entry)"
        )
    if dirn["verdict"] == "conflicts":
        cautions.append(
            "the signal board reads against the setup's side"
            if dirn["counter_trend"]
            else "the trend and the signal board read against the setup's side"
        )
    if row.get("status") == "closed":
        cautions.append("this position has exited: the read is for reference")
    debit = "call debit spread" if long_ else "put debit spread"
    credit = "put credit spread" if long_ else "call credit spread"
    target = lv.get("target") or (lv.get("resistance") if long_ else lv.get("support"))
    level = lv.get("support") if long_ else lv.get("resistance")
    if vol["earnings_in_cycle"]:
        structure, why = debit, "a report falls inside the cycle: no premium sold through it"
        cautions.append(f"earnings {vol['earnings']} inside the cycle")
    elif reg == "rich":
        structure = credit
        why = "implied vol is rich on both rank and percentile: sell it, past the level"
    elif reg == "cheap":
        structure = debit
        why = "implied vol is cheap on both rank and percentile: buy the move toward the target"
    elif reg == "middling":
        structure = f"{debit} or {credit}"
        why = "implied vol is neither rich nor cheap: either defined-risk spread fits"
    else:
        structure, why = None, "no IV reading for the name: the structure is undecided"
    legs: list[str] = []
    move = vol.get("expected_move")
    close = row.get("last_close")
    if structure and "debit" in structure:
        if target:
            legs.append(f"buy near the money, sell at {target['value']:.2f} ({target['pct']:+.1f}%)")
        elif move and close:
            # No target and no level in the way: the expected move is the natural width.
            far = close + move["dollars"] if long_ else close - move["dollars"]
            sign = "+" if long_ else "−"
            legs.append(f"buy near the money, sell near {sign}1σ, {far:.2f} ({sign}{move['pct']:.1f}%)")
    if structure and "credit" in structure:
        if band:
            legs.append(f"sell at or past the skate band, {band['band']:.2f}, for {band['expiry']}")
        if level:
            name = "support" if long_ else "resistance"
            legs.append(f"short strike past {name} {level['value']:.2f} ({level['pct']:+.1f}%)")
    return {"structure": structure, "why": why, "legs": legs, "cautions": cautions}


def skate_band(doc: dict | None, symbol: str, side: str, expiry: str | None) -> dict | None:
    """The skate screen's fixed band on the side a credit spread would sell: puts for a long, calls
    for a short; the cycle expiry when the band file holds it."""
    name = ((doc or {}).get("names") or {}).get(symbol)
    if not name or not expiry:
        return None
    right = "P" if side == "long" else "C"
    row = next((r for r in name["rows"] if r["side"] == right and r["expiry"] == expiry), None)
    return None if row is None else {"band": row["bands"]["fixed"]["band"], "expiry": expiry}


def read(row: dict, ctx: dict) -> dict[str, Any]:
    """The whole read for one watchlist row. `ctx`: day, cfg, iv rows, board rows, skate doc, hold medians."""
    day = date.fromisoformat(row["session"])
    sym = row["symbol"]
    record = (
        TRACK_RECORD.get(row["tested"])
        if row.get("tested")
        else TRACK_RECORD.get((row["family"], row["side"]))
    )
    iv = ctx["iv"].get(sym) or ctx["iv"].get(sym.replace(".", "/"))
    vol = volatility(row.get("last_close"), iv, day, ctx["cfg"])
    dirn = direction(row, ctx["board"].get(sym))
    lv = levels(row, row.get("atr14"), vol["expected_move"])
    band = skate_band(ctx["skate"], sym, row["side"], (vol["cycle"] or {}).get("expiry"))
    return {
        "setup": {
            "rule": row.get("rule"),
            "change": row.get("change"),
            "median_hold": ctx["holds"].get(row.get("tested") or row["setup"]),
        },
        "record": record,
        "direction": dirn,
        "volatility": vol,
        "levels": lv,
        "lean": lean(row, vol, dirn, lv, band, record),
    }


# ------------------------------------------------------------------------------------------------
# Inputs, read once per build.


def _board(day: str) -> dict[str, dict]:
    folder = paths.data_dir() / "board"
    files = [p for p in sorted(folder.glob("????-??-??.json")) if p.stem <= day]
    if not files:
        return {}
    try:
        return {r["symbol"]: r for r in json.loads(files[-1].read_text(encoding="utf-8")).get("rows") or []}
    except (OSError, ValueError):
        return {}


def hold_spans(doc: dict) -> dict[str, list[int]]:
    """Sessions held by every closed trade a chart file draws, by setup (a tested rule by its id)."""
    where = {d: i for i, d in enumerate(doc["bars"]["date"])}
    out: dict[str, list[int]] = {}
    blocks = [(s["id"], s) for s in doc.get("setups") or []] + [(t["id"], t) for t in doc.get("tested") or []]
    for key, s in blocks:
        for t in s.get("trades") or []:
            a, b = where.get(t.get("entry_date")), where.get(t.get("exit_date"))
            if a is not None and b is not None:
                out.setdefault(key, []).append(b - a)
    return out


def medians(spans: dict[str, list[int]]) -> dict[str, int]:
    return {k: sorted(v)[len(v) // 2] for k, v in spans.items() if v}


def annotate(
    rows: list[dict], session: str | None, holds: dict[str, int] | None = None, skate_root: Path | None = None
) -> None:
    """Add `read` to every row, in place. `holds` is each setup's median sessions held (`medians`)."""
    if not rows or session is None:
        return
    _, iv = earnings_calendar.broker_rows(session)
    ctx = {
        "cfg": load_settings(),
        "iv": iv,
        "board": _board(session),
        "skate": skate.latest_doc(session, skate_root),
        "holds": holds or {},
    }
    for r in rows:
        r["read"] = read(r, ctx)
