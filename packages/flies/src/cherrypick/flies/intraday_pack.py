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
PACK_VERSION = 2
SPOT_STEP_MINUTES = 5
# The opening range: the session's first 30 minutes, the window traders read a trend day's break from.
OPENING_RANGE_MINUTES = 30
# How many complete candles the higher-highs / lower-lows count looks back over.
STRUCTURE_BARS = 6
# The GEX recorder's flow columns (2026-10-05 on); older rows and stores lack them, read as None.
FLOW_COLUMNS = ("net_gex_vol", "call_volume", "put_volume", "call_wall_volume", "put_wall_volume")
FLOW_LOOKBACK_MINUTES = 15
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


def candles(
    rows: list[tuple[float, float]], start: float, step_minutes: int = SPOT_STEP_MINUTES
) -> list[dict]:
    """OHLC per `step_minutes` bucket from the recorded spot samples (about every 15 s, so a spike
    between two samples is missed and wicks read slightly short). A bucket with no sample is
    ABSENT, never filled: a recorder gap must not read as a quiet market. The last candle is marked
    `partial` when it is still forming at the pack's instant."""
    buckets: dict[int, list[float]] = {}
    for t, v in rows:
        if t >= start:
            buckets.setdefault(int((t - start) // (step_minutes * 60)), []).append(v)
    out = [
        {
            "t": _hhmm(start + k * step_minutes * 60),
            "o": round(vs[0], 2),
            "h": round(max(vs), 2),
            "l": round(min(vs), 2),
            "c": round(vs[-1], 2),
        }
        for k, vs in sorted(buckets.items())
    ]
    return out


def price_action(rows: list[tuple[float, float]], start: float, as_of: float, bars: list[dict]) -> dict:
    """Computed from the candles, so an agent that says "lower highs since 11:30" can be checked,
    and so the rule fitted later has the same features as columns."""
    or_end = start + OPENING_RANGE_MINUTES * 60
    or_rows = [v for t, v in rows if t < or_end]
    out: dict = {
        "opening_range": None,
        "higher_highs": None,
        "lower_lows": None,
        "last_bar_range_vs_avg": None,
    }
    if or_rows and as_of >= or_end:
        hi, lo = max(or_rows), min(or_rows)
        broke = next(((t, "above") for t, v in rows if t >= or_end and v > hi), None)
        broke_below = next(((t, "below") for t, v in rows if t >= or_end and v < lo), None)
        first = min([b for b in (broke, broke_below) if b], default=None, key=lambda b: b[0])
        out["opening_range"] = {
            "high": round(hi, 2),
            "low": round(lo, 2),
            "first_break": None if first is None else {"side": first[1], "t": _hhmm(first[0])},
        }
    # The candle holding the pack's own instant is still forming: structure is read off complete ones.
    forming = _hhmm(start + int((as_of - start) // (SPOT_STEP_MINUTES * 60)) * SPOT_STEP_MINUTES * 60)
    complete = [b for b in bars if b["t"] != forming]
    window = complete[-(STRUCTURE_BARS + 1) :]
    if len(window) >= 2:
        out["higher_highs"] = sum(b["h"] > a["h"] for a, b in zip(window, window[1:], strict=False))
        out["lower_lows"] = sum(b["l"] < a["l"] for a, b in zip(window, window[1:], strict=False))
        out["structure_bars"] = len(window) - 1
    ranges = [b["h"] - b["l"] for b in complete]
    if len(ranges) >= 3 and sum(ranges) > 0:
        out["last_bar_range_vs_avg"] = round(ranges[-1] / (sum(ranges) / len(ranges)), 2)
    return out


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
        "candles_5min": (bars := _mark_forming(candles(rows, start), start, as_of)),
        "candle_gaps": _gaps(bars, start, as_of),
        "price_action": price_action(rows, start, as_of, bars),
    }


def _mark_forming(bars: list[dict], start: float, as_of: float) -> list[dict]:
    forming = _hhmm(start + int((as_of - start) // (SPOT_STEP_MINUTES * 60)) * SPOT_STEP_MINUTES * 60)
    return [{**b, "partial": True} if b["t"] == forming else b for b in bars]


def _gaps(bars: list[dict], start: float, as_of: float) -> list[str]:
    """The 5-minute buckets between the open and the pack's instant with no recorded spot."""
    have = {b["t"] for b in bars}
    n = int((as_of - start) // (SPOT_STEP_MINUTES * 60)) + 1
    return [t for k in range(n) if (t := _hhmm(start + k * SPOT_STEP_MINUTES * 60)) not in have]


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


def flow_block(gex_conn, session: str, as_of: float) -> dict | None:
    """Options flow from the GEX recorder: session-cumulative call and put contracts and the volume
    at the walls (from 2026-10-05; None before), their change over the last 15 minutes, and the
    gamma-weighted `net_gex_vol` path (longer history). Columns are selected by name and missing ones
    read as None: the recorder added them by ALTER TABLE."""
    present = {r[1] for r in gex_conn.execute("PRAGMA table_info(gex_regime_history)")}
    cols = [c for c in FLOW_COLUMNS if c in present]
    if not cols:
        return None
    start = _open_epoch(session)
    rows = [
        dict(zip(["ts", *cols], r, strict=True))
        for r in gex_conn.execute(
            f"SELECT ts, {', '.join(cols)} FROM gex_regime_history "
            "WHERE symbol = 'SPX' AND trade_date = ? AND ts >= ? ORDER BY ts",
            (session, start),
        )
    ]
    rows = [r for r in rows if float(r["ts"]) <= as_of]
    if not rows:
        return None
    now = rows[-1]
    back = [r for r in rows if float(r["ts"]) <= float(now["ts"]) - FLOW_LOOKBACK_MINUTES * 60]
    then = back[-1] if back else None

    def change(col):
        if then is None or now.get(col) is None or then.get(col) is None:
            return None
        return round(float(now[col]) - float(then[col]), 0)

    out: dict = {}
    for col in ("call_volume", "put_volume", "call_wall_volume", "put_wall_volume"):
        if col in cols:
            out[col] = {"session": now.get(col), "last_15min": change(col)}
    if "net_gex_vol" in cols:
        out["net_gex_vol_bn_path_15min"] = [
            {"t": p["t"], "v": round(p["v"] / 1e9, 2)}
            for p in _stepped(
                [(float(r["ts"]), float(r["net_gex_vol"])) for r in rows if r["net_gex_vol"] is not None],
                start,
                as_of,
                GEX_STEP_MINUTES,
            )
        ]
    return out or None


def spy_vwap_block(gex_conn, session: str, as_of: float) -> dict | None:
    """SPY's VWAP and recent volume from the recorder's `spy_volume` (session-cumulative, from
    2026-10-06) and `spy` price readings: each minute's volume is the difference between samples,
    priced at that sample's SPY. None before the reading existed."""
    by: dict[str, list[tuple[float, float]]] = {"spy": [], "spy_volume": []}
    for reading, ts, value in gex_conn.execute(
        "SELECT reading, ts, value FROM market_regime_history WHERE trade_date = ? AND usable = 1 "
        "AND reading IN ('spy', 'spy_volume') AND value IS NOT NULL ORDER BY ts",
        (session,),
    ):
        by[reading].append((float(ts), float(value)))
    vols = _at_or_before(by["spy_volume"], as_of)
    prices = dict(_at_or_before(by["spy"], as_of))
    if len(vols) < 2:
        return None
    pv = v = 0.0
    recent = 0.0
    for (_t0, c0), (t1, c1) in zip(vols, vols[1:], strict=False):
        dv = c1 - c0
        if dv <= 0 or t1 not in prices:
            continue
        pv += prices[t1] * dv
        v += dv
        if t1 > as_of - FLOW_LOOKBACK_MINUTES * 60:
            recent += dv
    if v <= 0:
        return None
    vwap = pv / v
    last = prices[max(prices)] if prices else None
    minutes = max((vols[-1][0] - vols[0][0]) / 60, 1)
    return {
        "vwap": round(vwap, 2),
        "spy_vs_vwap_pct": round((last / vwap - 1) * 100, 3) if last else None,
        "volume_session": int(vols[-1][1]),
        "volume_last_15min_vs_avg": round(recent / (v / minutes * FLOW_LOOKBACK_MINUTES), 2) if v else None,
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
        "flow": flow_block(gex_conn, session, as_of),
        "spy_vwap": spy_vwap_block(gex_conn, session, as_of),
        "market": market_block(gex_conn, session, as_of),
        "flies": flies_block(ledger_conn, session, as_of, arm, spx["last"] if spx else None),
    }


#: Header keys, not evidence: never part of a completeness reading.
_HEADER = ("pack_version", "now")

#: Per block, the fields a recording feeds directly: one missing there means the data was not
#: recorded. Derived fields are deliberately absent -- an empty `candle_gaps`, a null opening-range
#: `first_break` before a break, an empty `positions` are facts about the session, not gaps in the
#: record. `*` is every reading of a block of readings. A block not declared here (a later pack
#: version) is judged whole: present or not.
RECORDED_FIELDS = {
    "spx": ("open", "last", "candles_5min"),
    "gex": ("now", "at_open", "path_15min"),
    "flow": (
        "call_volume.session",
        "put_volume.session",
        "call_wall_volume.session",
        "put_wall_volume.session",
        "net_gex_vol_bn_path_15min",
    ),
    "market": ("*.now",),
    "spy_vwap": ("",),
    "flies": ("",),
}


def _at(value, path: str):
    for part in [p for p in path.split(".") if p]:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _missing(v) -> bool:
    return v is None or v == {} or v == []


def completeness(pack: dict) -> dict:
    """Each evidence block of a pack as `filled`, `partial` or `empty`, over RECORDED_FIELDS.

    `pack_version` says which blocks a pack HAS; this says which were RECORDED when it was built. A
    block present but empty looks the same to `pack_version` (SPY VWAP and the options-flow volumes
    before 2026-10-06), so results built on thinner packs could be pooled with fuller ones unnoticed.
    Kept on the record beside the pack, never in it, so the model's input is unchanged."""
    out = {}
    for key, value in pack.items():
        if str(key).startswith("_") or key in _HEADER:
            continue
        if _missing(value):
            out[key] = "empty"
            continue
        probes = []
        for path in RECORDED_FIELDS.get(key, ("",)):
            if path.startswith("*."):
                probes += [_at(v, path[2:]) for v in value.values()] if isinstance(value, dict) else [None]
            else:
                probes.append(_at(value, path))
        missing = sum(_missing(v) for v in probes)
        out[key] = "empty" if missing == len(probes) else ("partial" if missing else "filled")
    return out


def for_model(pack):
    """The pack as the model sees it: every `_`-prefixed key (the real position ids) removed."""
    if isinstance(pack, dict):
        return {k: for_model(v) for k, v in pack.items() if not str(k).startswith("_")}
    if isinstance(pack, list):
        return [for_model(v) for v in pack]
    return pack
