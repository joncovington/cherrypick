"""Regime tagging for MEIC entries — ported from packages/flies/src/cherrypick/flies/engine.py's
classify_regime, adapted to what a MEIC snapshot actually carries (IV rank/VIX/VIX1D/ATR/intraday-
range instead of flies' ATM-straddle vol proxy, short-put/short-call mids instead of OTM verticals
at a fixed centre, GEX flip distance instead of strike concentration).

Every dimension returns a (bucket, float) pair — the bucket alone destroys the information needed
to recalibrate a threshold later, since a session tagged 'high' cannot be re-bucketed under a
different cut without re-running it. Storing the float means thresholds can be re-derived from
history at analysis time instead (analytics.regime_coverage's degeneracy guard is what catches a
bucket that never took its other value).

Every classifier degrades to ('unknown', None) on missing inputs, INCLUDING an empty
snapshot/params — classify_regime({}, {}) must not raise. This is what lets db.stale_writer_columns
enumerate the column set this module writes without a live snapshot to hand it.

Bands are fractions of spot/credit, never raw points — a points band silently reads 'flat'/'thin'/
whatever differently on a different-priced underlying (MEIC trades symbols spanning ~297 (IWM) to
~7500 (SPX); flies got exactly this wrong once already, on XSP vs SPX, before switching to
DRIFT_BAND_PCT).

Descriptive only. Nothing in this module gates an entry or exit decision.
"""

from __future__ import annotations

DIMENSIONS = (
    "vol_implied",
    "vol_event",
    "vol_realized",
    "vol_intraday",
    "gex",
    "skew",
    "center_offset",
    "trend",
)


def _leg_mid(q: dict | None) -> float | None:
    if not q:
        return None
    m = q.get("mid")
    if m is not None:
        return m
    bid, ask = q.get("bid"), q.get("ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def classify_regime(
    snapshot: dict,
    params: dict,
    *,
    put_strike: float | None = None,
    call_strike: float | None = None,
    put_quote: dict | None = None,
    call_quote: dict | None = None,
) -> dict:
    """Tag the current market state + chosen structure along 8 dimensions, each a pure read of
    the snapshot (and, for the two position-dependent dimensions, the chosen short strikes/quotes)
    already in hand — no cross-tick state, no new data source. Recorded at entry, not acted on;
    the eventual question this exists to answer — "which regime does this arm actually win in" —
    needs real, regime-labelled outcomes once enough sessions accumulate.

    put_strike/call_strike/put_quote/call_quote are optional: center_offset and skew are properties
    of the STRUCTURE chosen, not the market alone (the MEIC analogue of flies' `center` argument to
    classify_regime), so a caller with no structure in hand yet still gets the other six dimensions
    instead of an error — those two record 'unknown'.
    """
    vi_bucket, vi_value = _classify_vol_implied(snapshot, params)
    ve_bucket, ve_value = _classify_vol_event(snapshot, params)
    vr_bucket, vr_value = _classify_vol_realized(snapshot, params)
    vd_bucket, vd_value = _classify_vol_intraday(snapshot, params)
    gex_bucket, gex_value = _classify_gex(snapshot, params)
    skew_bucket, skew_value = _classify_skew(snapshot, params, put_quote, call_quote)
    offset_bucket, offset_value = _classify_center_offset(snapshot, params, put_strike, call_strike)
    trend_bucket, trend_value = _classify_trend(snapshot, params)
    return {
        "vol_implied_bucket": vi_bucket,
        "vol_event_bucket": ve_bucket,
        "vol_realized_bucket": vr_bucket,
        "vol_intraday_bucket": vd_bucket,
        "gex_bucket": gex_bucket,
        "skew_bucket": skew_bucket,
        "center_offset_bucket": offset_bucket,
        "trend_bucket": trend_bucket,
        "vol_implied_value": vi_value,
        "vol_event_value": ve_value,
        "vol_realized_value": vr_value,
        "vol_intraday_value": vd_value,
        "gex_value": gex_value,
        "skew_value": skew_value,
        "center_offset_value": offset_value,
        "trend_value": trend_value,
    }


def _classify_vol_implied(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """30-day IV rank, already 0-1 scaled and already on every snapshot — no proxy needed (flies
    computes an ATM-straddle/spot ratio here because it has no IV surface; MEIC's snapshot
    carries iv_rank directly from the broker)."""
    ivr = snapshot.get("iv_rank")
    if ivr is None:
        return "unknown", None
    low = params.get("regime_ivr_low", 0.30)
    high = params.get("regime_ivr_high", 0.60)
    if ivr < low:
        return "low", ivr
    if ivr > high:
        return "high", ivr
    return "normal", ivr


def _classify_vol_event(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """vix1d/vix ratio — the documented trader convention already cited in config.example.json's
    _regime_vix1d_ratio_note: >= 1.30 is an 'event day' (Fed/CPI/shock priced in), <= 0.75 is a
    'compression day', otherwise 'normal'. Reuses the SAME threshold the entry gate already
    applies on the high side (regime_vix1d_ratio_pause_threshold) rather than inventing a new
    one, so the tag and the gate agree on what 'event' means."""
    ratio = snapshot.get("vix1d_ratio")
    if ratio is None:
        return "unknown", None
    compression_max = params.get("regime_vix1d_compression_max", 0.75)
    event_min = params.get("regime_vix1d_ratio_pause_threshold") or 1.30
    if ratio >= event_min:
        return "event", ratio
    if ratio <= compression_max:
        return "compression", ratio
    return "normal", ratio


def _classify_vol_realized(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """Trailing 5-day ATR as a fraction of spot — the SAME measure regime_atr_pause_threshold_pct
    already gates entries on above its threshold; this tag reads the same float into three bands
    instead of one binary cut, so 'high' here lines up with what the live gate would refuse."""
    atr = snapshot.get("atr_5day")
    spot = snapshot.get("underlying_price")
    if atr is None or not spot:
        return "unknown", None
    frac = atr / spot
    low = params.get("regime_atr_low_pct", 0.008)
    high = params.get("regime_atr_pause_threshold_pct") or 0.015
    if frac > high:
        return "high", frac
    if frac < low:
        return "low", frac
    return "normal", frac


def _classify_vol_intraday(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """TODAY's realized range so far, as a fraction of spot — distinct from vol_realized's
    trailing 5-day ATR: this is how far the session has actually moved by the time of this
    entry, a same-day companion to the (backward-looking) ATR tag."""
    pct = snapshot.get("intraday_range_pct")
    if pct is None:
        return "unknown", None
    low = params.get("regime_range_low_pct", 0.003)
    high = params.get("regime_range_high_pct", 0.008)
    if pct > high:
        return "high", pct
    if pct < low:
        return "low", pct
    return "normal", pct


def _classify_gex(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """Signed distance from the gamma flip, as a fraction of spot: positive means spot sits ABOVE
    the flip (net-GEX-positive territory, where dealer hedging tends to dampen moves); negative
    means below it. 'unknown' whenever GEX is unavailable, mirroring the entry gate's own
    fail-open convention — never guessed.

    Reuses regime_gex_min_flip_distance_pct (the existing opt-in magnitude-gate threshold, `or`
    rather than a plain default since config ships it explicitly `null` when the gate is off) as
    the near/deep boundary, so this tag and that gate agree on what 'deep' means."""
    gex = snapshot.get("gex") or {}
    if not gex.get("ok"):
        return "unknown", None
    flip, spot = gex.get("gamma_flip"), gex.get("spot") or snapshot.get("underlying_price")
    if flip is None or not spot:
        return "unknown", None
    dist = (spot - flip) / spot
    threshold = params.get("regime_gex_min_flip_distance_pct") or 0.005
    if dist >= threshold:
        return "deep_positive", dist
    if dist <= -threshold:
        return "negative", dist
    return "near_flip", dist


def _classify_skew(
    snapshot: dict, params: dict, put_quote: dict | None, call_quote: dict | None
) -> tuple[str, float | None]:
    """Short-put mid vs. short-call mid, normalized by spot and signed: positive means the put
    side is pricier (more downside fear priced in) than the call side. 'unknown' without both
    short-leg quotes in hand — this is a property of the chosen structure, not the market alone,
    so a symbol-level snapshot with no candidate picked yet cannot tag it."""
    put_mid, call_mid = _leg_mid(put_quote), _leg_mid(call_quote)
    spot = snapshot.get("underlying_price")
    if put_mid is None or call_mid is None or not spot:
        return "unknown", None
    value = (put_mid - call_mid) / spot
    threshold = params.get("regime_skew_threshold_pct", 0.0002)
    if value > threshold:
        return "put_skew", value
    if value < -threshold:
        return "call_skew", value
    return "flat", value


def _classify_center_offset(
    snapshot: dict, params: dict, put_strike: float | None, call_strike: float | None
) -> tuple[str, float | None]:
    """Where the condor's midpoint (the average of its two short strikes) sits relative to spot,
    signed and normalized by spot: positive means the structure's centre sits ABOVE spot (the put
    side has more room, the call side less), negative the reverse. A symmetric-by-delta condor
    should read close to zero most of the time — that is what makes a persistent skew here worth
    watching, unlike flies' analogue where an intentional GEX-centred structure is the norm."""
    spot = snapshot.get("underlying_price")
    if put_strike is None or call_strike is None or not spot:
        return "unknown", None
    midpoint = (put_strike + call_strike) / 2.0
    offset = (midpoint - spot) / spot
    threshold = params.get("regime_center_offset_pct", 0.001)
    if offset > threshold:
        return "above_spot", offset
    if offset < -threshold:
        return "below_spot", offset
    return "at_spot", offset


def _classify_trend(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """How far the session has travelled from its own open, signed and normalized by spot:
    `(spot - day_open) / spot`. Fraction of spot, not points — MEIC trades symbols spanning ~297
    (IWM) to ~7500 (SPX), so a points band would silently mean something different per symbol
    (the mistake flies made first, on XSP vs SPX, before switching to DRIFT_BAND_PCT).

    'unknown' whenever day_open is unavailable — coverage starts wherever stream_summary starts
    capturing it, and a missing open is never replaced with prev_day_close or a guess (drift
    within the session and gap across it answer different questions).

    Descriptive only; nothing gates on it. Band is a reasoned starting point (0.3% of spot), not
    independently fitted — see docs/paper-experiments.md.
    """
    spot = snapshot.get("underlying_price")
    day_open = snapshot.get("day_open")
    if spot is None or not day_open:
        return "unknown", None
    drift = (spot - day_open) / spot
    band = params.get("regime_trend_pct", 0.003)
    if drift > band:
        return "up_from_open", drift
    if drift < -band:
        return "down_from_open", drift
    return "flat", drift


def regime_columns(
    prefix: str,
    snapshot: dict,
    params: dict,
    *,
    put_strike: float | None = None,
    call_strike: float | None = None,
    put_quote: dict | None = None,
    call_quote: dict | None = None,
) -> dict:
    """The regime columns for `prefix` — buckets AND the continuous measures behind them, ready
    to fold straight into synthetic_entry_fill's row. 'entry' is the only phase MEIC currently
    tags: ic_trades has no legging step, so there is no separate 'completion' snapshot the way
    flies' fly_positions has one; the prefix stays a parameter (rather than hardcoded) so a second
    phase can be added later without renaming every column. See classify_regime — descriptive
    telemetry only, nothing here gates a decision.
    """
    regime = classify_regime(
        snapshot,
        params,
        put_strike=put_strike,
        call_strike=call_strike,
        put_quote=put_quote,
        call_quote=call_quote,
    )
    return {f"{prefix}_{key}": value for key, value in regime.items()}


# ---------------------------------------------------------------------------
# Float-only covariates — not bucket+float regime dimensions (nothing here needs a re-cuttable
# threshold), but analysis inputs the entry-fill row should carry alongside the regime tags.
# ---------------------------------------------------------------------------


def credit_richness(net_credit: float | None, wing_width: float | None) -> float | None:
    """net_credit / wing_width — how rich this fill's credit was relative to its own risk,
    independent of dollar scale (a $2 credit on a 10-wide and a $1 credit on a 5-wide read the
    same). The forward-test ledger review found this bucket's SIGN inverted under the full-credit
    stop (richer credit predicted MORE stopping, because the trigger scales with it) — recording
    the float lets that interaction be read against whichever stop policy is being evaluated,
    rather than baked into one gate's own credit-floor check."""
    if not net_credit or not wing_width:
        return None
    return round(net_credit / wing_width, 4)


def put_credit_fraction(put_credit: float | None, net_credit: float | None) -> float | None:
    """This IC's put-side share of its own total credit. The variable a side-credit-basis stop
    policy actually tests — a structure with a symmetric split behaves like the net-credit-basis
    control; skewed splits are where the two policies diverge."""
    if put_credit is None or not net_credit:
        return None
    return round(put_credit / net_credit, 4)


def minutes_to_close(now_et: str | None, close_hhmm: str = "16:00") -> int | None:
    """Minutes remaining to the 4pm ET close from an 'HH:MM' now_et string — entry timing as a
    continuous covariate rather than only the coarse open/prime/midday/afternoon/late session
    bucket, since external research found the direct entry-time evidence genuinely unresolved
    (one backtest favored an earlier window, another favored noon-and-later) and a float lets the
    forward test's own data settle it instead of a hand-picked boundary."""
    if not now_et:
        return None
    try:
        h, m = (int(x) for x in now_et.split(":")[:2])
        ch, cm = (int(x) for x in close_hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        return None
    return (ch * 60 + cm) - (h * 60 + m)
