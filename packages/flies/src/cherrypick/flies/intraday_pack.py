"""The intraday agent's fact pack: what the session looked like at one instant, and nothing after it.

docs/intraday-agent-plan.md. A pure function over three read-only stores -- the GEX recorder's
history (spot, the regime readings, the minute market readings) and this module's paper ledger --
evaluated AS OF one epoch. The same function serves the live tick, the paper arm, the historical
replay and the rule fitted from the stored packs, so all four see identical inputs.

**No look-ahead, by construction and by test.** Every read is bounded `ts <= as_of`, and every
ledger field that is only known later is masked at `as_of`: a position completed after `as_of` is
still an open vertical in the pack, and nothing about settlement, exits or P&L is read at all.
`tests/test_intraday_pack.py` checks the pack is identical when every row after `as_of` is
deleted (the `core.rangefeatures` truncation rule), and a guard mutant shows the test fails if the
bound is dropped.

**No dates in the pack.** It names times of day only (`HH:MM` ET). The model's knowledge ends before
these sessions, so it cannot know how they ended, and stripping the date keeps it that way. Position
ids carry their entry date, so positions are labelled `p1`, `p2`, ... and the real ids ride in the
private `_position_ids` map; `for_model` strips every `_` key before a pack leaves the process.
"""

from __future__ import annotations

from datetime import datetime, time

from cherrypick.flies.clock import ET

# The minute market readings the pack carries, by the recorder's reading name. Each appears as its
# value at as_of and its change since the session's first reading.
MARKET_READINGS = (
    "vix",
    "vix1d",
    "vix9d",
    "vix3m",
    "vvix",
    "skew",
    "spy",
    "rsp",
    "hyg",
    "lqd",
    "tlt",
    "vx1",
    "vx2",
    "put_call_oi_ratio",
    "gamma_concentration",
    "atm_iv",
)
PACK_VERSION = 1
SPOT_STEP_MINUTES = 5
GEX_STEP_MINUTES = 15
# The trend band the fixed rule uses (engine._classify_trend's regime_trend_points default).
TREND_BAND_POINTS = 20.0


def _open_epoch(session: str) -> float:
    d = datetime.fromisoformat(session).date()
    return datetime.combine(d, time(9, 30), tzinfo=ET).timestamp()


def _hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, ET).strftime("%H:%M")


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _at_or_before(rows: list[tuple[float, float]], as_of: float) -> list[tuple[float, float]]:
    """The rows recorded by `as_of`: the one bound every read below goes through."""
    return [(t, v) for t, v in rows if t <= as_of]


def _stepped(rows: list[tuple[float, float]], start: float, as_of: float, step_minutes: int) -> list[dict]:
    """The last value in each `step_minutes` bucket from `start` to `as_of`."""
    out: dict[int, tuple[float, float]] = {}
    for t, v in rows:
        if start <= t <= as_of:
            out[int((t - start) // (step_minutes * 60))] = (t, v)
    return [
        {"t": _hhmm(start + k * step_minutes * 60), "v": round(v, 2)} for k, (_, v) in sorted(out.items())
    ]


def spx_block(gex_conn, session: str, as_of: float) -> dict | None:
    start = _open_epoch(session)
    rows = _at_or_before(
        [
            (float(r[0]), float(r[1]))
            for r in gex_conn.execute(
                "SELECT ts, spot FROM gex_spot_history WHERE symbol = 'SPX' AND trade_date = ? AND ts >= ? ORDER BY ts",
                (session, start),
            )
        ],
        as_of,
    )
    if not rows:
        return None
    spots = [v for _, v in rows]
    open_, last = spots[0], spots[-1]
    vs_open = round(last - open_, 2)
    return {
        "open": round(open_, 2),
        "high": round(max(spots), 2),
        "low": round(min(spots), 2),
        "last": round(last, 2),
        "vs_open_points": vs_open,
        "below_high_points": round(max(spots) - last, 2),
        "trend_bucket": "up"
        if vs_open > TREND_BAND_POINTS
        else ("down" if vs_open < -TREND_BAND_POINTS else "flat"),
        "path_5min": _stepped(rows, start, as_of, SPOT_STEP_MINUTES),
    }


def gex_block(gex_conn, session: str, as_of: float) -> dict | None:
    start = _open_epoch(session)
    rows = [
        r
        for r in gex_conn.execute(
            "SELECT ts, net_gex, zero_gamma, call_wall, put_wall FROM gex_regime_history "
            "WHERE symbol = 'SPX' AND trade_date = ? AND ts >= ? ORDER BY ts",
            (session, start),
        )
        if float(r[0]) <= as_of
    ]
    if not rows:
        return None
    first, last = rows[0], rows[-1]

    def walls(r):
        return {
            "net_gex_bn": round(float(r[1]) / 1e9, 2) if r[1] is not None else None,
            "zero_gamma": r[2],
            "call_wall": r[3],
            "put_wall": r[4],
        }

    return {
        "now": walls(last),
        "at_open": walls(first),
        "age_minutes": round((as_of - float(last[0])) / 60, 1),
        "path_15min": [
            {"t": p["t"], "net_gex_bn": round(p["v"] / 1e9, 2)}
            for p in _stepped(
                [(float(r[0]), float(r[1])) for r in rows if r[1] is not None], start, as_of, GEX_STEP_MINUTES
            )
        ],
        "call_wall_path_15min": _stepped(
            [(float(r[0]), float(r[3])) for r in rows if r[3] is not None], start, as_of, GEX_STEP_MINUTES
        ),
    }


def market_block(gex_conn, session: str, as_of: float) -> dict:
    out: dict[str, dict] = {}
    marks = ",".join("?" * len(MARKET_READINGS))
    by: dict[str, list[tuple[float, float]]] = {}
    for reading, ts, value in gex_conn.execute(
        f"SELECT reading, ts, value FROM market_regime_history WHERE trade_date = ? AND usable = 1 "
        f"AND reading IN ({marks}) AND value IS NOT NULL ORDER BY ts",
        (session, *MARKET_READINGS),
    ):
        by.setdefault(reading, []).append((float(ts), float(value)))
    for reading in MARKET_READINGS:
        rows = _at_or_before(by.get(reading, []), as_of)
        if not rows:
            continue
        first, last = rows[0][1], rows[-1][1]
        out[reading] = {"now": round(last, 4), "change": round(last - first, 4)}
    if "rsp" in out and "spy" in out:
        rsp, spy = by["rsp"], by["spy"]
        r_now, s_now = _at_or_before(rsp, as_of)[-1][1], _at_or_before(spy, as_of)[-1][1]
        r_0, s_0 = rsp[0][1], spy[0][1]
        if r_0 > 0 and s_0 > 0:
            # Equal-weight against cap-weight since the first reading: breadth, in percent.
            out["breadth_rsp_vs_spy_pct"] = {"now": round(((r_now / r_0) / (s_now / s_0) - 1) * 100, 3)}
    return out


def flies_block(ledger_conn, session: str, as_of: float, arm: str, spot: float | None) -> dict:
    """The arm's legged positions as they stood at `as_of`. Only entry-time facts and the completion
    time are read, and a completion after `as_of` is masked: nothing about how a position ended."""
    positions = []
    ids: dict[str, str] = {}
    for r in ledger_conn.execute(
        "SELECT position_id, kind, side, center, wing_width, credit, entry_time, completed_at "
        "FROM fly_positions WHERE trade_date = ? AND arm = ? ORDER BY entry_time",
        (session, arm),
    ):
        entered = _epoch(r[6])
        if entered is None or entered > as_of:
            continue
        done = _epoch(r[7])
        completed = done is not None and done <= as_of
        if r[1] not in ("fly", "short_vertical") and not completed:
            continue
        label = f"p{len(positions) + 1}"
        ids[label] = r[0]
        p = {
            "id": label,
            "side": r[2],
            "center": r[3],
            "wing": r[4],
            "credit": r[5],
            "entered": _hhmm(entered),
            "state": "completed" if completed else "open_vertical",
        }
        if completed:
            p["completed"] = _hhmm(done)
        elif spot is not None and r[3] is not None:
            # Signed so + means spot has moved through the short strike toward a full loss.
            p["spot_past_short_points"] = round((spot - r[3]) if r[2] == "call" else (r[3] - spot), 2)
        positions.append(p)
    return {
        "arm": arm,
        "positions": positions,
        "entries": len(positions),
        "completed": sum(p["state"] == "completed" for p in positions),
        "open_verticals": sum(p["state"] == "open_vertical" for p in positions),
        "_position_ids": ids,
    }


def build_pack(*, gex_conn, ledger_conn, session: str, as_of: float, arm: str = "control") -> dict:
    """The fact pack at `as_of` (an epoch) for `arm` on `session` (YYYY-MM-DD, never written into
    the pack). Blocks with nothing recorded yet are None or empty, never zero."""
    spx = spx_block(gex_conn, session, as_of)
    return {
        "pack_version": PACK_VERSION,
        "now": _hhmm(as_of),
        "spx": spx,
        "gex": gex_block(gex_conn, session, as_of),
        "market": market_block(gex_conn, session, as_of),
        "flies": flies_block(ledger_conn, session, as_of, arm, spx["last"] if spx else None),
    }


def for_model(pack):
    """The pack as the model sees it: every `_`-prefixed key (the real position ids) removed."""
    if isinstance(pack, dict):
        return {k: for_model(v) for k, v in pack.items() if not str(k).startswith("_")}
    if isinstance(pack, list):
        return [for_model(v) for v in pack]
    return pack
