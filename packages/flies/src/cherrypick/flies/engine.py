"""Entry/completion decision engine for cherrypick-flies.

Pure functions over one pre-fetched snapshot, exactly like MEIC's `paper.py`: this module never
calls the broker, never reaches the network, and never asks a model anything. That is a suite
guardrail (no AI or network on a loop-decision path), and here it is also what makes the experiment
valid — the three arms are only comparable if the only thing that differs between them is the arm.

Two entry modes, both derived from real order chains:

  legged    Sell a defined-risk credit spread for credit C. Then, each iteration, price the spread
            that would COMPLETE it into a symmetric fly and buy it when the debit D is comfortably
            below C. The result is a butterfly held for a net credit of C - D — a position whose
            worst case at expiry is a profit. When D never gets low enough, nothing is bought and
            the credit spread simply runs to cash settlement carrying its ordinary defined risk.
            That second branch is expected to be the common one and is reported separately.

  outright  Buy a cheap fly for a debit, but only out of premium the book has already realized.
            This never manufactures a floor of its own; it spends one.

Three arms, differing only in WHERE and WHEN they centre a structure:

  gex           centre on the strongest positive per-strike net GEX near spot (the pin candidate)
  time_window   centre ATM, entering only inside configured time-of-day windows
  control       centre ATM at one fixed midday time — the naive baseline that makes the other two
                falsifiable. Without it, a profitable `gex` arm proves nothing about GEX.
"""

from __future__ import annotations

from cherrypick.core import entry as _entry

from cherrypick.flies import fly

PUT, CALL = fly.PUT, fly.CALL
# `wide_wing` is control's twin — ATM, same window — differing only in `wing_width`, so the pair
# isolates wing width the way gex vs control isolates centring. It exists because completions arrive
# only after spot has walked away from the centre: median drift to completion was 15.3-17.3 points
# against a 5-point wing over 07-20..07-24, so 19 of 23 completed flies settled outside their wings.
# The mechanism that makes a completion cheap is the one that puts the peak out of reach, and a wing
# that brackets the observed drift is the obvious test of whether that is fixable or fundamental.
#
# The `width-N` arms (2026-07-29, with the XSP move) generalize that single hypothesis into a sweep.
# Originally N pinned `wing_width` to N POINTS, which is why the 2026-08-01 SPX move disabled the
# whole sweep: SPX's 5-point strikes cannot build a 2, 3 or 4-point wing at all. Rebuilt 2026-08-15
# on `wing_width_strikes` instead (resolved to points by `merged_params`, below) so each rung pins N
# STRIKE INCREMENTS rather than a raw point value that only made sense on the symbol it was fit
# against — 10/15/20/25/50 points on SPX today, and it rescales for free if this module ever trades a
# tighter-strike symbol again. Still no `width-1` arm: 1 strike is exactly control's own default
# width, so it would duplicate control's book under a second name regardless of symbol.
# `wide_wing` stays for the SPX-era books' attribution but is superseded by the sweep (its 20-point
# wing is exactly width-4's 4 strikes on SPX) and left disabled.
ARMS = (
    "gex",
    "gex-intrinsic",
    "time_window",
    "control",
    "control-drift",
    "wide_wing",
    "width-2",
    "width-3",
    "width-4",
    "width-5",
    "width-10",
    "debit-first",
    "iron",
    "bwb",
    # ATM twins of the two GEX-centred construction arms (2026-08-07). `bwb` and `debit-first` each
    # differ from `control` in TWO things -- entry construction AND centring -- so neither could
    # attribute a result to either, against this module's own one-variable rule. These pin the
    # centring to ATM so the construction is isolated, and are the hub of a three-way read:
    # X-atm vs control isolates the construction, X-atm vs X isolates the centring.
    # No `spot + N strikes` arm to go with them: `center_offset` is stored as a continuous float and
    # the GEX arms already sweep it (-22..+23 points measured, against ATM's -2.5..+2.5), so the
    # offset curve is re-cut with `by_regime(bucket_edges=...)` rather than pinned by a new arm.
    "bwb-atm",
    "debit-first-atm",
    # Centres the shorts at the GEX call wall (2026-08-31, from the gex module's pin study over 23
    # recorded sessions). NOT a pin bet -- the study killed that reading (the tent captured 2/23) --
    # but a bound bet: the close finished at or below the morning wall 19-21/23, so the entry is the
    # OTM call spread whose shorts sit at the wall, and completion (when the market offers it)
    # manufactures the usual floor. One variable vs control: centring, same discipline as `gex` was
    # -- and the gex arm's retirement finding ("centres on where price HAS BEEN") is answered rather
    # than ignored: this arm WANTS a level price has not reached, refuses the session when the wall
    # is not above spot, and its unknown -- whether an OTM spread at the wall pays enough credit to
    # clear the gates -- is exactly what the refusal rows will measure.
    "callwall",
)


# --------------------------------------------------------------------------- config
def merged_params(config: dict, arm: str) -> dict:
    """Base defaults overlaid with this arm's overrides. Arms are thin by design — an arm that
    redefined the gates as well as the centring would confound what the comparison measures.

    `wing_width_strikes`, when an arm sets it, resolves HERE to `wing_width = strike_increment *
    wing_width_strikes` -- the width-N arms are defined in STRIKES so the sweep means the same thing
    on any symbol's chain regardless of that symbol's own strike spacing, rather than naming a point
    value fit against one symbol (see the `width-N` note above ARMS)."""
    params = dict(config.get("defaults", {}))
    params.update(config.get("arms", {}).get(arm, {}))
    if "wing_width_strikes" in params:
        params["wing_width"] = params["strike_increment"] * params["wing_width_strikes"]
    params["arm"] = arm
    return params


def time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_entry_window(now_min: int | None, windows: list) -> tuple[bool, str | None]:
    """Is `now_min` (minute-of-day, ET) inside any configured window? Returns the window label too,
    because every trade is tagged with it — the per-window ranking has to emerge from our own
    sessions rather than from an assumption about which time of day is best."""
    if not windows:
        return True, None
    if now_min is None:
        return False, None
    for w in windows:
        start, end = time_to_minutes(w[0]), time_to_minutes(w[1])
        if start <= now_min <= end:
            return True, f"{w[0]}-{w[1]}"
    return False, None


# --------------------------------------------------------------------------- quotes
def quote(snapshot: dict, side: str, strike: float) -> dict | None:
    """Look up one leg quote. Strike keys are normalized because a snapshot round-tripped through
    JSON has string keys while one built in a test has floats."""
    book = snapshot.get("puts" if side == PUT else "calls") or {}
    for key, q in book.items():
        try:
            if abs(float(key) - float(strike)) < 1e-6:
                return q
        except (TypeError, ValueError):
            continue
    return None


def _have(snapshot: dict, side: str, strikes) -> bool:
    return all(quote(snapshot, side, s) is not None for s in strikes)


# --------------------------------------------------------------------------- centre selection
def atm_strike(spot: float, increment: float) -> float:
    return round(round(spot / increment) * increment, 4)


def select_center(snapshot: dict, params: dict) -> tuple[float | None, str]:
    """Pick the fly's centre strike for this arm. Returns (centre, reason).

    Centring is chosen by `center_rule` if the arm sets one, falling back to the arm's own name
    otherwise (so the original `gex` arm needs no config change). This split exists so a
    non-`gex`-named arm can still opt into GEX centring — e.g. `debit-first` uses it to require the
    same directional evidence (a strike GEX suggests price will be pulled toward) that justifies
    entering a debit vertical: buying the cheap side and betting on convergence only makes sense if
    something besides chance suggests spot moves that way.

    The `gex` rule degrades to ATM rather than skipping when GEX is unavailable, so a streamer that
    hasn't cached open interest yet costs us a signal, not a whole session of samples. The degrade is
    recorded in the reason string so those trades can be excluded from the arm's headline later.
    """
    spot = snapshot.get("underlying_price")
    increment = params.get("strike_increment", 5)
    if spot is None:
        return None, "no_underlying_price"

    rule = params.get("center_rule", params.get("arm"))

    if rule == "call_wall":
        # The wall IS the thesis, so this rule never degrades to ATM the way `gex` does: an ATM
        # fallback would trade control's trade under this arm's name, and the comparison the arm
        # exists for is centring, isolated. No wall, no trade -- recorded as the skip reason.
        gex = snapshot.get("gex") or {}
        wall = gex.get("call_wall") if gex.get("ok") else None
        if wall is None:
            return None, "gex_unavailable_for_call_wall"
        # Shorts at or below spot are not a "wall holds" bet -- they are short calls in or at the
        # money, a directional position the pin study's evidence says nothing about.
        if float(wall) <= spot:
            return None, "call_wall_not_above_spot"
        return float(wall), "call_wall"

    if rule != "gex":
        return atm_strike(spot, increment), "atm"

    gex = snapshot.get("gex") or {}
    per_strike = gex.get("per_strike") or []
    if not gex.get("ok") or not per_strike:
        return atm_strike(spot, increment), "atm_gex_unavailable"

    # Centre on TOTAL gamma (call + put), not net GEX.
    #
    # Pinning is caused by dealer gamma CONCENTRATION: a strike where dealers hold a large hedged
    # position drags price toward it as they trade against every move, and that pull does not care
    # which side the gamma sits on. Net GEX (call - put) measures directional positioning instead,
    # and it nets a strike with huge call AND huge put gamma — the hardest-pinning kind — to roughly
    # zero.
    #
    # Measured against a real SPX chain: net GEX was negative at EVERY strike within +/-40 points of
    # spot, because put open interest dominates there. "Max positive net GEX near spot" therefore had
    # no candidate to find near spot at all, and could only reach strikes ~67 points away where calls
    # finally take over — far enough that the structure stops being a pin bet. Total gamma on the
    # same chain peaked at -8, +12 and +32 points, exactly the range a 0DTE pin bet wants.

    # Deliberately tight. This is a 0DTE PIN bet: a centre far from spot needs a large move in the
    # remaining hours just to reach the peak, which is the opposite of the thesis. At 0.003 on a
    # ~7500 index this is about +/-22 points, roughly four strikes on the 5-point grid.
    #
    # It was 0.01, which allowed +/-75 points at that index level and let the gex arm centre on a
    # GEX wall 67 points away — GEX walls sit away from spot by nature, so the loose cap bit exactly
    # where the arm was most active. Trade-off worth watching: too tight and the gex arm collapses
    # onto ATM, making it indistinguishable from `control`. `analytics.arm_divergence` is the
    # instrument for that — if agreement runs near 100%, this is the first number to revisit.
    max_dist = params.get("max_center_distance_pct", 0.003) * spot

    def total_gamma(entry):
        return abs(entry.get("call_gex", 0.0)) + abs(entry.get("put_gex", 0.0))

    near = [s for s in per_strike if total_gamma(s) > 0 and abs(s["strike"] - spot) <= max_dist]
    if not near:
        return atm_strike(spot, increment), "atm_no_gamma_near_spot"
    best = max(near, key=total_gamma)
    return float(best["strike"]), "max_total_gamma"


# --------------------------------------------------------------------------- regime tagging
def classify_regime(snapshot: dict, params: dict, center: float | None = None) -> dict:
    """Tag the current market state along four dimensions, each a pure read of the snapshot
    already in hand -- no cross-tick state, no new data source, same no-I/O discipline as every
    other function here. Recorded (not acted on) at entry and completion, so the eventual
    question this exists to answer -- "which entry/completion mode wins under which regime" --
    has real, regime-labelled outcomes to be answered from once enough sessions accumulate.
    **This used to say a trend read was impossible here, and that was wrong (corrected
    2026-08-04).** The claim was that trend needs a reference point in time -- spot now vs. spot N
    minutes ago -- that no single snapshot carries, so including one would mean cross-tick state
    this module refuses to keep. The premise is false: the streamer already persists the session's
    open in `stream_summary`, so `spot - day_open` is a plain read of one row, and the snapshot now
    carries it as `session` (`provider._session_bounds`). No state, no history, no I/O on the
    decision path -- the discipline was never what stood in the way, only the assumption about what
    a snapshot could contain.

    The cost of that assumption is the reason it is written up rather than quietly fixed. On
    2026-08-04 the `gex` book lost $386 at 60% completion; both misses legged into the side a
    106-point up-from-open day was against, and no recorded tag could distinguish them. Measured
    afterwards, refusing an entry whose completing direction opposes a committed drift from the
    open separates completion 89% vs **7%** across the SPX sessions with session coverage -- the
    sharpest split any dimension here has produced. It is 15 blocked trades over 3 sessions, so it
    is tagged and not gated, exactly like everything else in this function.

    2026-08-05 was the mirror test and it held: a session that opened 7771.62 and settled 7723.55,
    where all three gex misses were up-completions on a falling day and both completions were
    down-completions. The lagging centre sat ABOVE spot rather than below, exactly as the mechanism
    predicts when the sign of the move flips.

    Returns a bucket per dimension -- independent values, not one collapsed string, so analytics
    can slice on any one dimension or their cross product -- AND the continuous measure each bucket
    was derived from, plus the GEX surface's own provenance.

    **Recording the raw measure alongside the bucket is the point** (added 2026-08-01). Every
    threshold here is a placeholder pending recalibration, and a bucket alone destroys the
    information needed to recalibrate: a session tagged "thin" cannot later be re-bucketed under a
    different threshold without re-running it, which is impossible. Storing the float means
    thresholds can be re-derived from history at analysis time instead. MEIC learned this first --
    see the rationale on its `gex_net_at_entry` columns (`meic/src/db.py:84-94`).
    """
    vol_bucket, vol_value = _classify_vol(snapshot, params)
    gex_bucket, gex_value = _classify_gex(snapshot, params)
    time_bucket, time_value = _classify_time(snapshot, params)
    skew_bucket, skew_value = _classify_skew(snapshot, params)
    offset_bucket, offset_value = _classify_center_offset(snapshot, params, center)
    trend_bucket, trend_value = _classify_trend(snapshot, params)
    gex = snapshot.get("gex") or {}
    gex_stats = snapshot.get("gex_stats") or {}
    return {
        "vol_bucket": vol_bucket,
        "gex_bucket": gex_bucket,
        "time_bucket": time_bucket,
        "skew_bucket": skew_bucket,
        "center_offset_bucket": offset_bucket,
        "trend_bucket": trend_bucket,
        # The measures behind the buckets.
        "vol_value": vol_value,
        "gex_concentration": gex_value,
        "time_value": time_value,
        "skew_value": skew_value,
        "center_offset_value": offset_value,
        "trend_value": trend_value,
        # GEX surface provenance: what the number was, and how much data stood behind it. Without
        # the coverage/age pair, a regime tag from a healthy surface is indistinguishable from one
        # computed off four surviving stale strikes.
        "net_gex": gex.get("net_gex") if gex.get("ok") else None,
        "gamma_flip": gex.get("gamma_flip") if gex.get("ok") else None,
        "gex_spot": gex.get("spot") if gex.get("ok") else None,
        "gex_strikes": gex_stats.get("strikes_with_data"),
        "gex_input_age": gex_stats.get("oldest_input_age_seconds"),
    }


def _classify_trend(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """How far the session has travelled from its own open: `spot - day_open` in points.

    Measured against the OPEN rather than a trailing window, and that choice is the finding rather
    than a detail. A 20-minute trailing drift was tested on the same ledger and separated P&L but
    NOT completion (64% vs 67%) -- it was picking up something incidental. Spot-vs-open separates
    completion 72% vs 15% over the 210 entries with session coverage. The 2026-08-04 14:01 entry is
    the worked example of the difference: its trailing 20-minute drift was -7.8 points, which reads
    as a pullback and would have passed a trailing gate, while against the open it was +106.0 on an
    unambiguous up day. It never completed. A 0DTE position lives one session, so the session's own
    open is the reference point that matches the horizon being traded.

    `regime_trend_points` (default 20) is the flat band. Below it the day has not committed to a
    direction and 'flat' is the honest tag, not a rounding of noise into a trend.

    **The band was 5 -- one SPX strike -- for exactly one day, and 5 was wrong (2026-08-05).** A
    strike is the resolution the CENTRE moves in; it has nothing to do with how far a session must
    travel before its direction means anything, and picking it was reaching for a familiar number
    instead of measuring one. Split by how far the day had committed, the 5-point tag is not merely
    weak in the 10-25 range, it is INVERTED: entries whose completing direction opposed a 10-25
    point drift completed 100% of the time (n=5). Past 25 points the same read is nearly absolute
    (0% and 14% completion in the two higher bands). Sweeping the band over the SPX sessions with
    session coverage, the opposing bucket completes 33% at a band of 5, 7% at 20, and degrades
    again by 30. 20 and 25 give identical splits, which is why 20 is used -- a plateau rather than
    a single winning value, so it is a shape in the data rather than one lucky cut.

    That dead zone is this dimension's own failure mode, and 2026-08-05 10:01 is the worked
    example: the gex arm legged into an up-completion at +13.6 from the open, the tag read 'flat',
    and the day then reversed to settle 48 points BELOW its open. Trend-from-open lags too -- it is
    slower to lag than a trailing window, not immune to it.

    Sized on 76 SPX entries across 3 sessions, and the band was chosen on the same rows that
    measure it. Treat 20 as the current best estimate, not a calibrated constant.

    Deliberately NOT a chop/trend distinction. That would need the path between open and now --
    whether spot travelled 106 points once or crossed the open nine times -- which really is
    cross-tick state this module does not keep. day_high/day_low are on the snapshot and could
    approximate it, but the approximation is untested and inventing it now, on the strength of one
    session, is exactly the mistake the honesty rules exist to prevent.

    Returns ('unknown', None) whenever the session row is absent -- coverage starts 2026-07-29,
    and a missing open is never replaced with prev_day_close or a guess, since the two answer
    different questions (drift within the session vs. gap across it).

    Descriptive only. Nothing gates on it.
    """
    spot = snapshot.get("underlying_price")
    day_open = (snapshot.get("session") or {}).get("day_open")
    if spot is None or day_open is None:
        return "unknown", None
    drift = spot - day_open
    band = params.get("regime_trend_points", 20.0)
    if drift > band:
        return "up_from_open", drift
    if drift < -band:
        return "down_from_open", drift
    return "flat", drift


def _classify_center_offset(snapshot: dict, params: dict, center: float | None) -> tuple[str, float | None]:
    """Where the chosen centre sits relative to spot, signed: `center - spot` in points.

    This is the dimension that turned out to matter on 2026-08-04 (docs/centre-lag.md), and it is
    here because centre-vs-spot silently decides something no other tag captures: `choose_side`
    sells PUTS when spot is at or below the centre and CALLS when it is above, and
    `fly.completing_side_direction` then makes the put side complete on an UP move and the call
    side on a DOWN one. So this single number fixes which way spot must go for the position to
    complete at all -- and a non-completion costs about eleven times what a completion earns.

    **Deliberately signed and side-neutral, not a "lagging" boolean.** "The centre lags spot" is the
    trend-relative reading of this number: on a rising day a lagging centre sits BELOW spot, on a
    falling day it sits ABOVE. A single snapshot carries no trend (the reason `classify_regime` has
    no trend dimension at all), so collapsing to "lagging" here would bake in an up-day assumption
    and quietly mislabel every down day. Recording the sign leaves the trend reading to analysis
    time, where the session's drift is actually known.

    Buckets on `regime_center_offset_points`, defaulting to one strike -- the resolution the centre
    can actually move in, so "behind spot by more than a strike" means the rule picked a strike it
    genuinely could have picked closer. As everywhere else here, the float is stored beside the
    bucket so the cut can be re-derived rather than re-run.

    Note this is derived, not new information: `center` and `underlying_at_entry` are both already
    on the row, so any past session can be re-cut without this column. It exists so the slice is one
    `by_regime` call rather than a bespoke query every time.

    Descriptive only. Nothing gates on it -- 34 gex entries is a hypothesis, not a threshold.
    """
    spot = snapshot.get("underlying_price")
    if center is None or spot is None:
        return "unknown", None
    offset = center - spot
    threshold = params.get("regime_center_offset_points", params.get("strike_increment", 5))
    if offset < -threshold:
        return "below_spot", offset  # sells calls, needs spot DOWN to complete
    if offset > threshold:
        return "above_spot", offset  # sells puts, needs spot UP to complete
    return "at_spot", offset


def _classify_vol(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """ATM straddle price / spot -- a cheap 0DTE expected-move proxy. No IV surface is available
    here, so this reads the market's own pricing of the straddle directly rather than backing out
    an implied vol number the snapshot has no inputs to compute honestly.

    Returns (bucket, straddle/spot ratio)."""
    spot = snapshot.get("underlying_price")
    if spot is None or spot <= 0:
        return "unknown", None
    strike = atm_strike(spot, params.get("strike_increment", 5))
    put_q, call_q = quote(snapshot, PUT, strike), quote(snapshot, CALL, strike)
    if put_q is None or call_q is None:
        return "unknown", None
    straddle = fly._leg_mid(put_q) + fly._leg_mid(call_q)
    ratio = straddle / spot
    if ratio < params.get("regime_vol_low_pct", 0.0015):
        return "low", ratio
    if ratio > params.get("regime_vol_high_pct", 0.0035):
        return "high", ratio
    return "normal", ratio


# Concentration is measured over the top N strikes, not the single largest. Pinning is a property
# of a cluster: a doubled centre plus its immediate neighbours hold price together, and a measure
# that only sees one strike splits that cluster's own mass and reads it as thin.
_GEX_CONCENTRATION_TOP_N = 3


def _classify_gex(snapshot: dict, params: dict) -> tuple[str, float | None]:
    """How concentrated gamma is NEAR SPOT -- the condition under which pinning can actually act.

    Returns (bucket, concentration share). "unknown" whenever the OI cache the streamer would need
    isn't populated yet, mirroring `select_center`'s own honest degrade -- never guessed.

    **Windowed to `regime_gex_window_pct` of spot (2026-08-01 fix).** This previously took one
    strike's share of the ENTIRE chain -- and flies computes GEX over the whole surface
    (`provider.build_snapshot`), typically 109-121 strikes on a real 0DTE session. A single strike's
    share of that is small by construction, gamma 300 points away has no bearing on whether price
    pins here, and the resulting tag was degenerate in practice: `entry_gex_bucket` came back
    'thin' 60 times out of 60, never once 'pinning', while the sibling vol and skew tags varied
    normally. A measure that cannot take its other value is not measuring anything.

    **Cut points calibrated 2026-08-21 from the recorded shares** -- the second degeneracy fix on
    this same tag, caught the same way. The windowing fix made the share vary, but the 0.60
    pinning cut was a guess that landed ABOVE the 95th percentile of everything the tag then
    recorded (605 settled SPX entries across 15 sessions: median 0.359, p90 0.511, max 0.838), so
    'thin' still swallowed 97% of rows. The cuts are now the distribution's own terciles, rounded
    (p33=0.291, p67=0.412 -> 0.30/0.42), three ways: diffuse / clustered / pinning, 215/217/173
    rows and 10/14/14 sessions per bucket on the calibration data. Kept because the direction
    matches the mechanism, same standard as the 11:00/13:00 time re-cut: a legged fly completes
    only when spot drifts off the centre, concentrated near-spot gamma suppresses exactly that
    drift, and completion falls monotonically 68% -> 63% -> 55% across the three buckets, in both
    halves of the calibration window. A current best estimate measured on the rows that chose it,
    not a calibrated constant. Historical rows re-bucket at read time via
    `analytics.by_regime(..., bucket_edges=[0.30, 0.42])` -- the reason the share is stored.
    """
    gex = snapshot.get("gex") or {}
    per_strike = gex.get("per_strike") or []
    spot = gex.get("spot") or snapshot.get("underlying_price")
    if not gex.get("ok") or not per_strike or not spot:
        return "unknown", None
    window = params.get("regime_gex_window_pct", 0.02) * spot
    near = [s for s in per_strike if abs(s.get("strike", 0) - spot) <= window]
    if not near:
        return "unknown", None
    totals = sorted((abs(s.get("call_gex", 0) + s.get("put_gex", 0)) for s in near), reverse=True)
    total_sum = sum(totals)
    if total_sum <= 0:
        return "unknown", None
    share = sum(totals[:_GEX_CONCENTRATION_TOP_N]) / total_sum
    pinning = params.get("regime_gex_pinning_concentration", 0.42)
    clustered = params.get("regime_gex_clustered_concentration", 0.30)
    if share >= pinning:
        return "pinning", share
    return ("clustered" if share >= clustered else "diffuse"), share


def _classify_time(snapshot: dict, params: dict) -> tuple[str, int | None]:
    """Session phase, and the raw minute-of-day behind it.

    Was degenerate at the old 10:00/15:30 boundaries (measured 2026-08-01, again 2026-08-06): entries
    only ever occur between 10:00 and 14:42, so every tagged row came back 'midday' -- constant by
    construction, first at 60/60 rows and then at 97/97. Deliberately not re-guessed at the time; the
    raw minute is recorded alongside the bucket precisely so the cut could later be made against what
    actually happened. It now has been: **11:00/13:00**, re-derived on 2026-08-06 from the recorded
    minute with no session re-run.

    The re-cut splits 43/35/19 with completion falling monotonically **72% -> 63% -> 58%** through the
    day. That direction is what makes it worth keeping rather than merely non-degenerate: a legged
    entry completes only once spot drifts off the centre, and a later entry has less session left to
    drift in, so the decline is the mechanism showing up rather than a bucket boundary flattering
    itself. Chosen on the same 97 rows that measure it -- a current best estimate, not a calibrated
    constant.

    **Not redundant with `entry_window`, which was the standing hypothesis and is now answered.**
    `entry_window`'s dominant '10:00-14:30' window holds 74 of those 97 rows and splits 35/27/12
    across these buckets, so it structurally cannot see this variation. The narrow windows do map 1:1
    onto single buckets, so the two agree exactly where `entry_window` is already precise and diverge
    where it is not -- which is the useful shape, not a reason to retire either.
    """
    now_min = snapshot.get("now_min")
    if now_min is None:
        return "unknown", None
    # Defaults carry the 2026-08-06 re-cut, not the degenerate originals: no deployed config sets
    # these keys, so the fallback IS the live tag definition rather than a placeholder behind one.
    open_end = time_to_minutes(params.get("regime_time_open_end", "11:00"))
    close_start = time_to_minutes(params.get("regime_time_close_start", "13:00"))
    if now_min < open_end:
        return "open", now_min
    if now_min >= close_start:
        return "close", now_min
    return "midday", now_min


def _classify_skew(snapshot: dict, params: dict) -> str:
    """Reads directional skew straight out of the chain already in hand: compares the OTM put at
    `center - wing_width` against the OTM call at `center + wing_width` -- the exact strikes this
    module already trades, not an arbitrary distance. A richer put than its equidistant call means
    the market is pricing more downside risk than upside, and vice versa.

    Returns (bucket, normalised put-minus-call difference)."""
    spot = snapshot.get("underlying_price")
    if spot is None:
        return "unknown", None
    center = atm_strike(spot, params.get("strike_increment", 5))
    width = params.get("wing_width", 5)
    put_q = quote(snapshot, PUT, center - width)
    call_q = quote(snapshot, CALL, center + width)
    if put_q is None or call_q is None:
        return "unknown", None
    put_mid, call_mid = fly._leg_mid(put_q), fly._leg_mid(call_q)
    avg = (put_mid + call_mid) / 2.0
    if avg <= 0:
        return "unknown", None
    diff = (put_mid - call_mid) / avg
    threshold = params.get("regime_skew_threshold", 0.15)
    if diff > threshold:
        return "put_skew", diff
    if diff < -threshold:
        return "call_skew", diff
    return "flat", diff


def choose_side(snapshot: dict, center: float) -> str:
    """Which credit spread to sell first when legging in.

    Sell the side spot is already on the far end of, so the COMPLETING spread is the one that
    cheapens if the current drift continues. Spot below centre means the put spread is the one with
    room to work. This is a heuristic about which leg-in has a chance, not a directional view — the
    fly ends up symmetric either way.
    """
    spot = snapshot.get("underlying_price", center)
    return PUT if spot <= center else CALL


def choose_bwb_side(snapshot: dict, center: float) -> str:
    """Which side to build a broken-wing butterfly on — deliberately the INVERSE of `choose_side`.

    `choose_side` answers a *legged* question: sell the side spot is already on the far end of, so
    the COMPLETING spread is the one that cheapens as the drift continues. A bwb's roll is a
    different trade with the opposite geometry — it buys `centre -/+ wing_width` and sells the far
    wing, and BOTH of those sit on the risk side. Reusing the legged rule therefore places the roll
    spread IN THE MONEY, and an ITM vertical cannot be bought for less than its intrinsic:

        spot 7000, centre 7010, wing 5, far 10
          legged rule -> puts: hold +1 7015P / -2 7010P / +1 7000P, far wing AT spot
                         roll = buy 7005P sell 7000P -> intrinsic floor 5.00
          this rule   -> calls: hold +1 7005C / -2 7010C / +1 7020C, tail 20 points away
                         roll = buy 7015C sell 7020C -> both OTM, intrinsic 0.00

    A bwb entry credit runs ~1-3 points, so a roll with a 5.00 intrinsic floor can never satisfy
    `roll_debit < credit - fee_buffer` — unreachable before a single quote is read, which is what
    "the roll balloons exactly when the tail is threatened" was actually describing.

    The rule also states the structure's intent: the whole butterfly sits OUT of the money with the
    near wing closest to spot, so spot drifting further away carries the roll further OTM and
    cheapens it. That is the drift the arm is built to monetize.
    """
    spot = snapshot.get("underlying_price", center)
    return CALL if spot <= center else PUT


def intrinsic_at_entry(side: str, center: float, spot: float, width: float) -> float:
    """How much of this credit spread's value is already decided: the short strike's intrinsic,
    capped at the wing width.

    `center` IS the short strike for a legged entry. A put spread is in the money when spot sits
    BELOW it, a call spread when spot sits above; the cap at `width` is the structure's own maximum,
    since a vertical cannot be worth more than the distance between its legs.

    Reaching the cap means BOTH legs are in the money -- the spread's expiry value is already pinned
    at maximum loss and only a reversal through the entire width recovers it. That is a directional
    bet, not the pin bet this module is built on.

    Deliberately computed from spot and the strike rather than inferred from the credit. See
    `max_intrinsic_pct_of_width` for why the credit cannot carry this signal at 0DTE.
    """
    intrinsic = (center - spot) if side == PUT else (spot - center)
    return min(max(intrinsic, 0.0), width)


def before_open_gate(params: dict, now_min: int | None) -> bool:
    """Is it still inside the post-open blackout? A floor that an arm's own windows cannot override.

    Deliberately NOT expressed as "just set every arm's first window later". Each arm carries its own
    `entry_windows`, so four separate lists are four chances to silently reopen the hole when an arm is
    added or edited — and MEIC is the worked example of what config-only enforcement looks like when it
    fails there (its `entry_window_start` said 10:00 while the paper engine, which only applied the
    check for opt-in profiles, traded from 09:30 for the entire dataset). This gate is checked before
    the window logic and answers to `no_entry_before` alone, so an arm asking for an earlier window
    gets refused rather than obeyed.

    Off when unset, so nothing changes for a config that has not opted in.
    """
    floor = params.get("no_entry_before")
    if not floor or now_min is None:
        return False
    return now_min < time_to_minutes(floor)


def _window_cap_reached(params: dict, open_positions: list, window: str | None) -> bool:
    """Has this entry window already used up its own share of the position budget?

    A global `max_positions` alone does not make a multi-window arm test its windows: the book fills
    in the first window and the later ones are never reached. Over 07-20..07-24 the time_window arm
    put 15 of its 16 legged entries in 10:30-11:00, 1 in 12:30-13:00 and 0 in 14:00-14:30 — so the
    timing hypothesis the arm exists to test was never actually exercised, and the per-window ranking
    the config asks for had nothing to rank. It is the same failure the arm's `_history_note` already
    records once, and a global cap cannot prevent it.

    Off unless `max_positions_per_window` is configured, so single-window arms and the existing
    behaviour are untouched. Positions entered before the cap existed carry no window and are simply
    not counted against one.
    """
    cap = params.get("max_positions_per_window")
    if cap is None or window is None:
        return False
    return sum(1 for p in open_positions if p.get("entry_window") == window) >= cap


# The single expiry token the sign rule keys on -- see portfolio_gates. These are 0DTE
# structures scoped to one trade date, so there is exactly one expiry per day book.
_EXPIRY = "0dte"


def completion_opposes_drift(snapshot: dict, params: dict, side: str) -> tuple[bool, float | None]:
    """Would this entry need spot to REVERSE a committed session drift in order to complete?

    Returns ``(opposes, drift)``. `drift` is `spot - day_open` in points, or None when the session
    row is absent -- coverage starts 2026-07-29, and a missing open is never replaced with a guess.

    The mechanism, and why this is the sharpest dimension this module has found. `choose_side` sells
    the side spot has already crossed, and `fly.completing_side_direction` then makes a PUT spread
    complete on an UP move and a CALL spread on a DOWN one. So on a day that has committed to a
    direction, the arm systematically legs into the side that needs the day to turn around.

    Measured across the SPX era: entries whose completing direction opposed a committed drift
    completed 7% against 89% for the rest. It reproduced in mirror image on 2026-08-04 (up day,
    both losing gex entries legged into the side the +115 run was against) and 2026-08-05 (down day,
    all three misses were up-completions), and again on 2026-08-11 -- with-drift 79% completion for
    +$1,376, against-drift 23% for -$3,237. The sign flips with the market rather than persisting,
    which is what separates a mechanism from an up-trending artefact.

    Reads `regime_trend_points` (default 20) as the flat band, the SAME key `_classify_trend` uses,
    so the gate and the tag can never disagree about what "committed" means. Inside the band the day
    has not committed and this returns False: the band's own dead zone is a known failure mode (see
    `_classify_trend`), and widening the gate past the tag to cover it would be fitting the number
    to the outcome.

    Fails OPEN on unknown coverage -- a missing session row means no opinion, not a refusal.
    """
    spot = snapshot.get("underlying_price")
    day_open = (snapshot.get("session") or {}).get("day_open")
    if spot is None or day_open is None:
        return False, None
    drift = spot - day_open
    band = params.get("regime_trend_points", 20.0)
    if abs(drift) <= band:
        return False, drift
    needs = fly.completing_side_direction(side)
    opposes = (needs == "up" and drift < 0) or (needs == "down" and drift > 0)
    return opposes, drift


def _day_book(open_positions: list, day_positions: list | None) -> list:
    """The positions the portfolio rules read: the arm's WHOLE day, not just what is still open.

    Flies are completed, not closed, so a structure never leaves the book before EOD -- which means
    the duplicate rule and the sign rule must both see everything entered today. `day_positions` is
    passed by `book.py`, which already holds it; the fallback to `open_positions` exists for the
    live loop and for direct callers/tests, where the two are the same list in practice.
    """
    return open_positions if day_positions is None else day_positions


def structure_key(position: dict) -> tuple:
    """The identity of a structure for "never enter the same trade twice": ``(centre, wing, far)``.

    Deliberately GEOMETRY ONLY -- neither `kind` nor `side` is part of it, and both omissions are
    load-bearing:

    * `kind` is excluded because every entry mode converges on the same structure. A legged short
      vertical at K completes into a fly at K; so does a debit_first long vertical at K. Keying on
      kind would let an arm open both and call them different trades, when the book ends the day
      holding one structure entered twice -- exactly what the rule forbids.
    * `side` is excluded because a put fly and a call fly on the same centre and wings have the same
      payoff. They are the same bet expressed two ways, and the arm's own `choose_side` can pick
      either depending on where spot sits at the moment it looks.

    `far_width` stays in, and is None for every symmetric structure, which is what keeps a bwb from
    ever colliding with a symmetric fly on the same centre.

    This is the generalization of the `center_already_occupied` rule it replaces, and collapses to
    it exactly today: `wing_width` is a scalar per arm (width variation lives in SEPARATE arms), so
    within one arm the same centre already implies the same wings.
    """
    return (
        float(position["center"]),
        None if position.get("wing_width") is None else float(position["wing_width"]),
        None if position.get("far_width") is None else float(position["far_width"]),
    )


def cadence_state(params: dict, day_positions: list, now_min: int | None) -> tuple[bool, float | None]:
    """Has this arm's entry cadence elapsed? Returns ``(allowed, seconds_remaining)``.

    The clock runs from the arm's last FILLED entry, per `cherrypick.core.entry`. An arm is its own
    portfolio with unbounded capital, so this and the entry rules are the only things pacing it --
    which also makes `seconds_remaining` worth recording on every refusal, because the distribution
    of time spent waiting IS the measured cost of the current spacing.

    Works in minute-of-day like the rest of this module's gating rather than in wall-clock datetimes,
    because that is what a snapshot carries; the seconds it returns are therefore a whole number of
    minutes. `entry_time_min` is stamped on each position by `book.py` at fill time.
    """
    spacing = params.get("min_seconds_between_entries", 0) or 0
    if spacing <= 0 or now_min is None:
        return True, None
    fills = [p.get("entry_time_min") for p in day_positions if p.get("entry_time_min") is not None]
    if not fills:
        return True, None
    elapsed_seconds = (now_min - max(fills)) * 60
    if elapsed_seconds >= spacing:
        return True, None
    return False, float(spacing - elapsed_seconds)


def portfolio_gates(
    params: dict,
    day_positions: list,
    now_min: int | None,
    *,
    proposed_legs: list | None = None,
    structure: tuple | None = None,
) -> tuple[str | None, dict]:
    """The three per-arm portfolio rules, in the order a refusal is most cheaply explained.

    Returns ``(reason | None, detail)`` -- None meaning "no rule refused this". `detail` carries the
    measurement the attempts ledger records for that refusal (seconds still to wait, or the strike
    that collided), so the caller never has to re-derive why.

    Cadence first because it is true of the whole tick regardless of what was proposed, then the
    duplicate rule (a pure key lookup), then the sign rule (the only one that walks legs). `structure`
    and `proposed_legs` may each be None, which skips their rule -- a caller that has not built a
    concrete plan yet can still ask the cadence question.
    """
    allowed, remaining = cadence_state(params, day_positions, now_min)
    if not allowed:
        return "entry_cadence_wait", {"seconds_until_cadence_clear": remaining}

    if structure is not None:
        existing = {structure_key(p) for p in day_positions if p.get("center") is not None}
        if structure in existing:
            return "duplicate_structure", {}

    if proposed_legs:
        # Both sides are stamped with ONE expiry token here rather than each carrying its own.
        #
        # The day book is scoped to a single (trade_date, arm, symbol) and every structure in it is
        # 0DTE for that date, so there is exactly one expiry in play and forcing it is correct. It is
        # also the only safe construction: the stored rows carry `trade_date` while a snapshot's own
        # date field is not guaranteed to be populated, and if the two ever disagreed the legs would
        # land in different buckets and the rule would silently permit everything. A gate that fails
        # OPEN and silently is worse than no gate, because it still reads as enforced.
        open_legs = []
        for p in day_positions:
            try:
                open_legs.extend((_EXPIRY, r, k, s) for _e, r, k, s in fly.position_legs(p))
            except (ValueError, KeyError, TypeError):
                # A row whose geometry cannot be read constrains nothing rather than crashing the
                # tick. Deliberately permissive in ONE direction only: this can admit an entry the
                # rule would have refused, never refuse one it would have allowed, and the attempts
                # ledger still records what was taken. A refusal here would turn a malformed
                # historical row into an outage for the rest of the session.
                continue
        stamped = [(_EXPIRY, r, k, sg) for _e, r, k, sg in proposed_legs]
        hit = _entry.sign_conflict(open_legs, stamped)
        if hit is not None:
            return "sign_rule_conflict", {"blocking_strike": hit[2], "blocking_right": hit[1]}

    return None, {}


# --------------------------------------------------------------------------- legged entry (step 1)
def evaluate_credit_spread_entry(
    snapshot: dict,
    params: dict,
    open_positions: list,
    day_positions: list | None = None,
    gate_detail: dict | None = None,
) -> tuple:
    """Should this arm sell an opening credit spread? Returns (enter, reason, plan | None).

    `plan` carries everything the fill needs: side, centre (the SHORT strike, which becomes the fly's
    centre), wing width, the modeled credit, and the strike that would complete the fly later.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    # Checked BEFORE the arm's own windows so an arm cannot configure its way past the blackout.
    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    width = params.get("wing_width", 5)
    # `choose_side` sells the side spot is on the far end of, which for a centre ABOVE spot means
    # the put spread -- shorts at the wall, in the money, credit mostly intrinsic. That is the
    # opposite of a call_wall entry: its whole trade is the OTM call spread whose shorts sit at the
    # wall, winning when the close stays below it. Forced here rather than left to the heuristic,
    # because the heuristic answers a legging question and this arm's side is part of its thesis.
    side = CALL if params.get("center_rule") == "call_wall" else choose_side(snapshot, center)
    long_strike = center - width if side == PUT else center + width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    # Drift gate (opt-in per arm via `refuse_completion_against_trend`; off when unset).
    #
    # Refuses an entry that would need spot to REVERSE a committed session drift to complete. This
    # is the module's sharpest measured dimension and until now nothing gated on it -- 7% completion
    # against 89%, reproduced in both directions across sessions. See `completion_opposes_drift`.
    #
    # Placed BEFORE the moneyness gate so the attempts ledger attributes a refusal to the drift when
    # both would fire: drift is a property of the day and decides whether the trade can work at all,
    # while moneyness is a property of the strike we picked. Crediting the wrong one would make the
    # arm comparison read the wrong rule.
    if params.get("refuse_completion_against_trend"):
        opposes, drift = completion_opposes_drift(snapshot, params, side)
        if opposes:
            if gate_detail is not None:
                gate_detail["drift_points"] = round(drift, 2) if drift is not None else None
            return False, "completion_against_drift", None

    # Moneyness gate (opt-in per arm via `max_intrinsic_pct_of_width`; off when unset).
    #
    # This is what `max_credit_pct_of_width` was meant to do and structurally cannot. That gate uses
    # the CREDIT as a proxy for moneyness, and at 0DTE the proxy does not hold: with hours left, a
    # fully in-the-money 5-wide prices near 2.5-3.0 rather than near 5.00, because there is still
    # real probability of moving back out. Measured over the SPX era, all 16 fully-ITM entries priced
    # at 48-59.7% of width -- every one of them UNDER the 0.60 ceiling, so the gate named for this
    # job had never once caught it.
    #
    # Intrinsic is exact and needs no calibration: `center` is the short strike and spot is in the
    # snapshot. At `intrinsic >= width` both legs are in the money and the expiry value is already
    # pinned at maximum loss; only a reversal through the entire width recovers it. Those 16 entries
    # realized -$1,119.64 (8 completed for +$630, 8 stranded for -$1,750) against -$15.27 average for
    # the shallow bucket.
    #
    # Applied to the LEGGED path only. bwb and debit_first carry different geometry and their own
    # populations have not been measured, and a gate extended to a structure it was never derived
    # against is the mistake this module keeps a rule about.
    max_intrinsic = params.get("max_intrinsic_pct_of_width")
    if max_intrinsic is not None:
        spot = snapshot.get("underlying_price")
        if spot is not None:
            intrinsic = intrinsic_at_entry(side, center, spot, width)
            if intrinsic >= max_intrinsic * width:
                if gate_detail is not None:
                    gate_detail["intrinsic"] = round(intrinsic, 4)
                return False, "entry_mostly_intrinsic", None

    # The per-arm portfolio rules: cadence, no duplicate structure, no self-cancelling leg.
    #
    # These replace the old `center_already_occupied` check. That rule refused a second structure on
    # an occupied centre, and the duplicate rule below is its generalization: it keys on the full
    # geometry (kind, side, centre, wings) rather than the centre alone. The two coincide exactly
    # today, because `wing_width` is a scalar per arm -- width variation is expressed as SEPARATE
    # arms (width-2..width-5, width-10, wide_wing) precisely to keep the sweep one-variable, so within one arm
    # the same centre implies the same wings implies the same trade. Written as the general rule
    # anyway, because that is what "never enter the same trade twice" actually means, and it stays
    # correct the day an arm sweeps width internally.
    #
    # The legs are the structure this entry WOULD hold, which for a legged entry is the opening
    # short vertical, not the fly it hopes to become. The completing leg is evaluated separately and
    # deliberately doubles this short into the fly's -2 centre -- same sign, so the sign rule permits
    # it, which is the whole reason the rule is about sign rather than about strike occupancy.
    proposed = [(_EXPIRY, side, center, -1), (_EXPIRY, side, long_strike, 1)]
    structure = (float(center), float(width), None)
    refusal, _detail = portfolio_gates(
        params,
        _day_book(open_positions, day_positions),
        snapshot.get("now_min"),
        proposed_legs=proposed,
        structure=structure,
    )
    if refusal:
        # Recorded through an out-dict rather than the return tuple on purpose. `plan is None on
        # refusal` is an invariant live_loop documents and leans on, and telemetry is not a good
        # enough reason to loosen a contract that live-order code reads. Callers that want the
        # detail pass a dict; the live loop passes nothing and is untouched.
        if gate_detail is not None:
            gate_detail.update(_detail)
        return False, refusal, None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit = fly.vertical_credit(quote(snapshot, side, center), quote(snapshot, side, long_strike), slip)

    min_credit = params.get("min_credit_pct_of_width", 0.20) * width
    if credit < min_credit:
        return False, "credit_below_floor", None

    # Ceiling as well as floor. A defined-risk vertical cannot be worth more than its width, so a
    # credit approaching that width means the short leg is deep in the money and the "premium" is
    # mostly intrinsic. Selling one of those is a low-probability directional bet, not a pin bet.
    #
    # Found against real SPX data: the gex arm centred 67 points from spot and produced a short
    # 7525/7520 put spread with spot at 7457 — 77% of width in credit, 67 points of intrinsic,
    # profitable only on a 67-point rally. `min_credit_pct_of_width` cannot catch this, because a
    # fatter intrinsic-heavy credit looks BETTER to a floor. Hence a separate ceiling, applied to
    # every arm rather than just the one that exposed it.
    max_credit = params.get("max_credit_pct_of_width", 0.60) * width
    if credit > max_credit:
        # Record the number it refused on. Until 2026-08-11 this gate returned a bare reason, so a
        # session could show 146 refusals with no way to ask afterwards what was turned down or
        # whether turning it down was right -- the same blind spot `best_completing_debit` exists to
        # close on the completion side.
        if gate_detail is not None:
            gate_detail["would_be_credit"] = round(credit, 4)
        return False, "credit_above_ceiling_mostly_intrinsic", None

    # A credit spread whose credit can't clear the fee stack on BOTH legs of the leg-in can never
    # produce a risk-free fly, so there is no reason to open it inside this strategy.
    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    round_trip_fees = fly.vertical_open_fee(symbol, qty) * 2
    if credit * fly.CONTRACT_MULTIPLIER * qty <= round_trip_fees:
        return False, "credit_cannot_clear_fees", None

    completing_strike = center + width if side == PUT else center - width
    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "credit": round(credit, 4),
            "quantity": qty,
            "open_fee": fly.vertical_open_fee(symbol, qty),
            "completing_strike": completing_strike,
            "completing_direction": fly.completing_side_direction(side),
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- legged entry (step 2)
def evaluate_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open credit spread be completed into a butterfly now? Returns (complete, reason, plan).

    The gate is `D < C - fee_buffer`, where the buffer must cover the second fee stack. Completing at
    D just under C would produce a fly with a positive gross credit and a negative floor after fees —
    the exact failure this module is built to expose rather than hide.
    """
    if position.get("kind") != "short_vertical":
        return False, "not_a_credit_spread", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    long_strike = center + width if side == PUT else center - width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    # Buying the completing spread: long the far strike, short the centre (which offsets nothing —
    # it doubles the existing short into the fly's -2 centre).
    debit = fly.vertical_debit(quote(snapshot, side, long_strike), quote(snapshot, side, center), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    # fee_buffer is expressed in price points so it reads like every other threshold in the config;
    # the floor check below is the one that actually enforces solvency in dollars.
    buffer_pts = params.get("fee_buffer", 0.10)
    credit = position["net"]

    net = credit - debit
    completed_fees = position.get("fees", 0.0) + completion_fee
    # Reuse fly.position_floor rather than a second, duplicate formula: it already carries the
    # worst-case exercise-assignment fee (4 contracts for a fly, see position_floor's own
    # docstring), which a completion's own inline floor formerly omitted entirely -- a completion
    # could clear the dollar gate on a floor that only looked non-negative because it never priced
    # in the one cost every ITM leg of the resulting fly can actually incur.
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
    # Every return carries the priced debit, including the refusals. A refusal that discarded the
    # price would make "never completed" permanently ambiguous between "the market never offered it"
    # and "our buffer was too tight" -- and those call for opposite fixes. The caller records the
    # running minimum, which is what makes that question answerable after the fact.
    plan = {
        "debit": round(debit, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "long_strike": long_strike,
        "gate_debit": round(credit - buffer_pts, 4),  # the debit this would have had to beat
    }

    if debit >= credit - buffer_pts:
        return False, "completing_debit_too_high", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- debit-first entry (step 1)
def choose_debit_side(snapshot: dict, center: float) -> str:
    """Which debit vertical to buy first when legging in via `debit_first` -- the inverse of
    `choose_side`. Buy the side spot is currently on the OTM end of, so the debit is cheap now and
    has room to richen as spot moves toward the centre -- the same direction the completing credit
    spread richens in (see `fly.debit_first_completing_direction`). `choose_side` instead sells the
    side spot has already crossed, betting on the COMPLETING spread cheapening on continued drift
    away -- the opposite regime.
    """
    spot = snapshot.get("underlying_price", center)
    return CALL if spot <= center else PUT


def evaluate_debit_vertical_entry(
    snapshot: dict,
    params: dict,
    open_positions: list,
    day_positions: list | None = None,
    gate_detail: dict | None = None,
) -> tuple:
    """Should this arm buy an opening debit vertical? Returns (enter, reason, plan | None).

    Mirror of `evaluate_credit_spread_entry`, buying instead of selling: the debit vertical bought
    here is completed later by SELLING the credit spread at the same centre
    (`evaluate_debit_completion`) -- literally the same two trades `legged` makes, in the opposite
    order.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    width = params.get("wing_width", 5)
    side = choose_debit_side(snapshot, center)
    long_strike = center - width if side == CALL else center + width
    if not _have(snapshot, side, [center, long_strike]):
        return False, "missing_leg_quotes", None

    # Per-arm portfolio rules -- see the equivalent block in `evaluate_credit_spread_entry`. This
    # entry holds the mirror geometry (short the centre, long the far strike) and is refused on the
    # same three grounds.
    proposed = [(_EXPIRY, side, center, -1), (_EXPIRY, side, long_strike, 1)]
    refusal, _detail = portfolio_gates(
        params,
        _day_book(open_positions, day_positions),
        snapshot.get("now_min"),
        proposed_legs=proposed,
        structure=(float(center), float(width), None),
    )
    if refusal:
        # Recorded through an out-dict rather than the return tuple on purpose. `plan is None on
        # refusal` is an invariant live_loop documents and leans on, and telemetry is not a good
        # enough reason to loosen a contract that live-order code reads. Callers that want the
        # detail pass a dict; the live loop passes nothing and is untouched.
        if gate_detail is not None:
            gate_detail.update(_detail)
        return False, refusal, None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    debit = fly.vertical_debit(quote(snapshot, side, long_strike), quote(snapshot, side, center), slip)

    if debit <= 0:
        # A non-positive modeled debit means a stale or crossed quote -- a debit vertical's value
        # is bounded below by zero, so nobody sells one for a credit.
        return False, "implausible_debit_quote", None

    min_debit = params.get("min_debit_pct_of_width", 0.20) * width
    if debit < min_debit:
        # Too cheap to be plausible: the far-OTM side of a debit spread this thin has little room
        # left to richen before the completing credit spread's own ceiling caps it.
        return False, "debit_below_floor_completion_implausible", None

    max_debit = params.get("max_debit_pct_of_width", 0.60) * width
    if debit > max_debit:
        # Mirror of legged's intrinsic-heavy ceiling: a debit approaching the width means the long
        # leg is deep ITM already, leaving little room for the completing sale to out-earn it.
        return False, "debit_above_ceiling_mostly_intrinsic", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.vertical_open_fee(symbol, qty)
    fee_buffer = params.get("fee_buffer", 0.10)
    # The completing credit can never exceed `width` (a vertical's value is capped there), so a
    # debit that already leaves no room for buffer + both fee stacks, expressed in price points,
    # can never be out-earned -- refuse before ever taking the position on, the same feasibility
    # check `credit_cannot_clear_fees` is for legged's entry.
    completion_fee_est = fly.vertical_open_fee(symbol, qty)
    fees_in_points = (open_fee + completion_fee_est) / (fly.CONTRACT_MULTIPLIER * qty)
    if debit + fee_buffer + fees_in_points >= width:
        return False, "debit_cannot_be_out_earned", None

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "debit": round(debit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "completing_direction": fly.debit_first_completing_direction(side),
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- debit-first entry (step 2)
def evaluate_debit_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open long vertical (debit_first's opening trade) be completed into a butterfly
    now, by SELLING the credit spread at the same centre? Returns (complete, reason, plan).

    Mirror of `evaluate_completion` with the trade direction reversed: the gate is
    `C > D + fee_buffer`, where D is the debit already paid.
    """
    if position.get("kind") != "long_vertical":
        return False, "not_a_debit_vertical", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    # The completing credit spread is legged's own entry geometry -- short the centre, long the
    # wing on the far side, same formula `evaluate_credit_spread_entry` uses for its long_strike.
    wing_strike = center - width if side == PUT else center + width
    if not _have(snapshot, side, [center, wing_strike]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit = fly.vertical_credit(quote(snapshot, side, center), quote(snapshot, side, wing_strike), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    debit_paid = -position["net"]  # net is negative (a debit) for an open long_vertical

    net = credit - debit_paid
    completed_fees = position.get("fees", 0.0) + completion_fee
    # Reuse fly.position_floor exactly as evaluate_completion does -- see its own comment on why
    # a completion's floor check must go through the shared function rather than a duplicate.
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
    plan = {
        "credit": round(credit, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "wing_strike": wing_strike,
        "gate_credit": round(debit_paid + buffer_pts, 4),  # the credit this would have had to beat
    }

    if credit <= debit_paid + buffer_pts:
        return False, "completing_credit_too_low", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- iron completion
def evaluate_iron_completion(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open credit spread be completed into an IRON butterfly now, by selling the
    OPPOSITE-type credit spread at the same centre? Returns (complete, reason, plan).

    RETIRED 2026-08-03 and unreachable in config — `completion_modes` is `["debit"]` everywhere and
    the `iron` arm is disabled. Kept because it is correct and tested, and because deleting it would
    delete the ability to re-derive why. Read docs/iron-completion.md before re-enabling anything.

    Put held -> sell the call spread; call held -> sell the put spread -- the final geometry
    (long (center-w) put, short center put, short center call, long (center+w) call) is the same
    regardless of which side was legged first. Payoff-equivalent to a same-type fly shifted down
    by `wing_width`, so risk-free iff the two credits summed clear `wing_width` plus fees -- NOT
    assumed; the floor gate is what actually enforces it (`fly.iron_fly_payoff`,
    `fly.position_floor`'s iron_fly branch).

    Why it is retired: this completion and `evaluate_completion`'s use the SAME strike pair (center,
    center +/- wing_width), so put-call parity pins `D + credit2 = wing_width` exactly -- every
    implied-vol term cancels, for any skew. Hence `credit1 + credit2 > W + buffer` below is
    algebraically the same inequality as `evaluate_completion`'s `D < credit1 - buffer`: both fire on
    the same tick or neither does, and the completed positions have the same net at every settlement
    price. The iron's larger credit is not extra money; it buys exactly `W` of extra liability. What
    remains is cost, all of it adverse: an iron always has one side ITM where a same-type fly can
    settle clean (+$3.46/position in assignment fees over 143 measured completions), plus a wider
    crossing cost that a flat `slippage_frac` cannot represent.
    """
    if position.get("kind") != "short_vertical":
        return False, "not_a_credit_spread", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    opposite_side = CALL if side == PUT else PUT
    opposite_wing = center - width if opposite_side == PUT else center + width
    if not _have(snapshot, opposite_side, [center, opposite_wing]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    credit2 = fly.vertical_credit(
        quote(snapshot, opposite_side, center), quote(snapshot, opposite_side, opposite_wing), slip
    )

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    completion_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    credit1 = position["net"]

    net = credit1 + credit2
    completed_fees = position.get("fees", 0.0) + completion_fee
    floor = fly.position_floor(
        {
            "kind": "iron_fly",
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": completed_fees,
        }
    )
    # The second credit this would have had to beat: with the first, enough to clear the width
    # (the iron fly's worst-case tent) plus the fee buffer.
    gate_credit = round(width + buffer_pts - credit1, 4)
    plan = {
        "credit": round(credit2, 4),
        "net": round(net, 4),
        "completion_fee": completion_fee,
        "floor": round(floor, 2),
        "opposite_side": opposite_side,
        "opposite_wing": opposite_wing,
        "gate_credit": gate_credit,
    }

    if credit2 <= gate_credit:
        return False, "iron_credit_too_low", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- bwb entry (step 1)
def _bwb_lower_upper(side: str, near_wing: float, far_wing: float) -> tuple[float, float]:
    """Which of the two wings is numerically lower, by strike -- `fly.fly_debit`'s formula is
    symmetric in lower/upper (the mid terms just add), but the quotes must be looked up at the
    right strikes regardless."""
    return (far_wing, near_wing) if side == PUT else (near_wing, far_wing)


def evaluate_bwb_entry(
    snapshot: dict,
    params: dict,
    open_positions: list,
    day_positions: list | None = None,
    gate_detail: dict | None = None,
) -> tuple:
    """Should this arm enter a broken-wing butterfly for a net credit? Returns (enter, reason, plan).

    Side via `choose_bwb_side`, NOT `choose_side` — the legged heuristic is the wrong one here and
    placed the roll spread in the money; see that function. `far_width` (> wing_width) is the wide,
    risk-carrying wing; the credit collected is rent for that tail, measured against it
    (`min_bwb_credit_pct_of_tail`/`max_bwb_credit_pct_of_tail`), not against wing_width the way
    legged's credit gates are -- there is no width-bounded ceiling here since the structure's own
    worst case is already `wing_width - far_width`, not `-wing_width`.
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None

    width = params.get("wing_width", 5)
    # A ratio, not an absolute point value: wing_width itself is manually rescaled per symbol and
    # per width-sweep arm (control=1, width-2..width-5), and a fixed absolute far_width would need
    # separately rescaling every time either of those changes. A ratio (the common real-world rule
    # of thumb is roughly 1:2, near:far) scales automatically with whatever wing_width the arm is
    # already using.
    far_width = width * params.get("bwb_far_width_ratio", 2.0)
    if far_width <= width:
        return False, "far_width_not_wider_than_wing", None

    side = choose_bwb_side(snapshot, center)
    near_wing, _, far_wing = fly.bwb_strikes(side, center, width, far_width)
    if not _have(snapshot, side, [near_wing, center, far_wing]):
        return False, "missing_leg_quotes", None

    # Per-arm portfolio rules -- see `evaluate_credit_spread_entry`. A bwb is entered complete, so
    # unlike the two legged modes its proposed legs ARE the whole structure: both wings plus the
    # doubled centre. `far_width` is part of the structure key here and None for every other kind,
    # which is what keeps a bwb from ever reading as a duplicate of a symmetric structure.
    proposed = [
        (_EXPIRY, side, near_wing, 1),
        (_EXPIRY, side, center, -1),
        (_EXPIRY, side, center, -1),
        (_EXPIRY, side, far_wing, 1),
    ]
    refusal, _detail = portfolio_gates(
        params,
        _day_book(open_positions, day_positions),
        snapshot.get("now_min"),
        proposed_legs=proposed,
        structure=(float(center), float(width), float(far_width)),
    )
    if refusal:
        # Recorded through an out-dict rather than the return tuple on purpose. `plan is None on
        # refusal` is an invariant live_loop documents and leans on, and telemetry is not a good
        # enough reason to loosen a contract that live-order code reads. Callers that want the
        # detail pass a dict; the live loop passes nothing and is untouched.
        if gate_detail is not None:
            gate_detail.update(_detail)
        return False, refusal, None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    lower_wing, upper_wing = _bwb_lower_upper(side, near_wing, far_wing)
    # fly_debit prices the +1 lower / -2 centre / +1 upper combo as a debit (positive = cost);
    # a broken-wing entered for a net credit comes out NEGATIVE there, so credit is its negation.
    raw = fly.fly_debit(
        quote(snapshot, side, lower_wing),
        quote(snapshot, side, center),
        quote(snapshot, side, upper_wing),
        slip,
    )
    credit = -raw

    tail = far_width - width
    min_credit = params.get("min_bwb_credit_pct_of_tail", 0.15) * tail
    if credit <= min_credit:
        return False, "bwb_credit_below_floor", None

    max_credit = params.get("max_bwb_credit_pct_of_tail", 0.6) * tail
    if credit > max_credit:
        return False, "bwb_credit_above_ceiling_mostly_intrinsic", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.fly_open_fee(symbol, qty)
    # The tail risk this credit is being paid to carry, in dollars, net of the credit itself --
    # capped so no single structure can carry more downside than the operator has decided to
    # accept, independent of how the price gates above happen to price it.
    tail_dollars = (tail - credit) * fly.CONTRACT_MULTIPLIER * qty + open_fee
    if tail_dollars > params.get("max_bwb_tail_dollars", 150.0):
        return False, "bwb_tail_risk_above_max", None

    # The entry fee plus an anticipated roll fee (2-leg, same shape as legged's own completing
    # fee) -- a credit that cannot clear even that combined stack could never be justified
    # regardless of how the roll eventually prices.
    anticipated_roll_fee = fly.vertical_open_fee(symbol, qty)
    if credit * fly.CONTRACT_MULTIPLIER * qty <= open_fee + anticipated_roll_fee:
        return False, "credit_cannot_clear_fees", None

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "far_width": far_width,
            "credit": round(credit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- bwb roll (step 2)
def evaluate_roll(snapshot: dict, position: dict, params: dict) -> tuple:
    """Should this open broken-wing butterfly be rolled now -- buying the near strike, selling the
    held far strike -- converting it into a symmetric fly at (credit - roll_debit)? Returns
    (roll, reason, plan).

    The roll is a 2-leg debit vertical of width (far_width - wing_width): buy the strike the
    symmetric fly needs, sell the wide wing currently held. Gated the same shape as every other
    completion in this module: price gate first (`roll_debit < credit - fee_buffer`), then the
    resulting fly's actual floor (`fly.position_floor`), never assumed from the price gate alone.

    **The bought leg is `center -/+ wing_width`, NOT `bwb_strikes`' `near_wing`** — that one is on
    the PROTECTED side and the position already holds it. Until 2026-08-07 this priced
    `vertical_debit(near_wing, far_wing)`, a spread of width (far + wing) rather than (far - wing):
    3x too wide at the default 2.0 ratio, and describing a trade that does not produce a butterfly
    at all (buying a strike already held leaves two debit spreads, not a fly). See CLAUDE.md.
    """
    if position.get("kind") != "bwb":
        return False, "not_a_bwb", None

    side, center, width = position["side"], position["center"], position["wing_width"]
    far_width = position["far_width"]
    _, _, far_wing = fly.bwb_strikes(side, center, width, far_width)
    # The symmetric fly's wing on the RISK side — near_wing mirrored across the centre. This is the
    # leg the position lacks; `far_wing` is the one it must give up to get it.
    roll_strike = center - width if side == PUT else center + width
    if not _have(snapshot, side, [roll_strike, far_wing]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    # Long the strike the fly needs, short the wide wing currently held -- a debit vertical spanning
    # exactly (far_width - wing_width), which is the tail being bought back.
    roll_debit = fly.vertical_debit(quote(snapshot, side, roll_strike), quote(snapshot, side, far_wing), slip)

    symbol = snapshot["symbol"]
    qty = position.get("quantity", 1)
    roll_fee = fly.vertical_open_fee(symbol, qty)
    buffer_pts = params.get("fee_buffer", 0.10)
    credit = position["net"]

    net = credit - roll_debit
    rolled_fees = position.get("fees", 0.0) + roll_fee
    floor = fly.position_floor(
        {
            "kind": "fly",
            "side": side,
            "center": center,
            "wing_width": width,
            "net": net,
            "quantity": qty,
            "fees": rolled_fees,
        }
    )
    plan = {
        "roll_debit": round(roll_debit, 4),
        "net": round(net, 4),
        "roll_fee": roll_fee,
        "floor": round(floor, 2),
        "roll_strike": roll_strike,
        "far_wing": far_wing,
        "roll_span": round(far_width - width, 4),
        "gate_debit": round(credit - buffer_pts, 4),  # the roll debit this would have had to beat
    }

    if roll_debit >= credit - buffer_pts:
        return False, "roll_debit_too_high", plan
    if floor < params.get("min_floor_dollars", 0.0):
        return False, "floor_below_minimum_after_fees", plan

    return True, "ok", plan


# --------------------------------------------------------------------------- outright entry
def evaluate_outright_entry(
    snapshot: dict, params: dict, open_positions: list, realized_cash: float
) -> tuple:
    """Should the book buy a cheap fly outright, funded by premium it has already realized?

    `realized_cash` is the book's credit-minus-debits-minus-fees so far. Requiring the debit to fit
    inside it is what keeps this mode honest: the book never spends money it hasn't taken in, so its
    floor is bounded by construction. That floor is still only BOOK-level and only holds inside the
    funding spreads' wings — `fly.book_floor` reports the band, and callers must not round it up to
    "risk-free".
    """
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    # Checked BEFORE the arm's own windows so an arm cannot configure its way past the blackout.
    if before_open_gate(params, snapshot.get("now_min")):
        return False, "before_open_gate", None

    ok_window, window = in_entry_window(snapshot.get("now_min"), params.get("entry_windows", []))
    if not ok_window:
        return False, "outside_entry_window", None

    if len(open_positions) >= params.get("max_positions", 4):
        return False, "max_positions_reached", None

    if _window_cap_reached(params, open_positions, window):
        return False, "max_positions_this_window_reached", None

    center, center_reason = select_center(snapshot, params)
    if center is None:
        return False, center_reason, None
    if any(abs(p["center"] - center) < 1e-6 for p in open_positions):
        return False, "center_already_occupied", None

    width = params.get("wing_width", 5)
    side = CALL if snapshot.get("underlying_price", center) > center else PUT
    lower, upper = center - width, center + width
    if not _have(snapshot, side, [lower, center, upper]):
        return False, "missing_leg_quotes", None

    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    debit = fly.fly_debit(
        quote(snapshot, side, lower), quote(snapshot, side, center), quote(snapshot, side, upper), slip
    )
    if debit <= 0:
        # A non-positive modeled debit means a stale or crossed quote, not free money: a long fly's
        # value is bounded below by zero, so nobody sells one for a credit.
        return False, "implausible_fly_quote", None

    symbol = snapshot["symbol"]
    qty = params.get("quantity", 1)
    open_fee = fly.fly_open_fee(symbol, qty)

    max_debit = params.get("max_fly_debit", 0.50)
    if debit > max_debit:
        return False, "fly_debit_above_max", None

    cost = debit * fly.CONTRACT_MULTIPLIER * qty + open_fee
    if cost > realized_cash:
        return False, "not_funded_by_realized_credit", None

    return (
        True,
        "ok",
        {
            "side": side,
            "center": center,
            "center_reason": center_reason,
            "wing_width": width,
            "debit": round(debit, 4),
            "quantity": qty,
            "open_fee": open_fee,
            "cost": round(cost, 2),
            "entry_window": window,
        },
    )


# --------------------------------------------------------------------------- settlement
def _expiry_payoff(position: dict, settlement_price: float) -> float:
    """Per-contract expiry value by kind — explicit dispatch, no fallthrough. A silent else here
    once priced whatever future kind arrived as a short vertical, which is a SIGN-FLIPPED payoff
    for anything whose worst case sits above zero (e.g. a debit-first long vertical) — the exact
    hazard this function exists to close off before a new kind is ever settled for real."""
    kind = position["kind"]
    if kind == "fly":
        return fly.fly_payoff(position["center"], position["wing_width"], settlement_price)
    if kind == "short_vertical":
        return fly.short_vertical_payoff(
            position["side"], position["center"], position["wing_width"], settlement_price
        )
    if kind == "long_vertical":
        return fly.debit_vertical_payoff(
            position["side"], position["center"], position["wing_width"], settlement_price
        )
    if kind == "iron_fly":
        return fly.iron_fly_payoff(position["center"], position["wing_width"], settlement_price)
    if kind == "bwb":
        return fly.bwb_payoff(
            position["side"],
            position["center"],
            position["wing_width"],
            position["far_width"],
            settlement_price,
        )
    raise ValueError(f"_expiry_payoff: unknown position kind {kind!r}")


def settle(positions: list[dict], settlement_price: float) -> list[dict]:
    """Cash-settle every open position at expiry. SPX/XSP are European cash-settled, so there is no
    physical assignment to model — but tastytrade still charges $5/contract the next business day
    for every leg that finishes ITM and is exercised into cash (see `fly.itm_legs_at_settlement`;
    OTM legs expire worthless for free). That charge is folded into the position's `fees` here, once,
    at the moment of settlement, so `pnl` and the persisted `fees` total both include it consistently.

    Deliberately there is no stop loss and no wing adjustment: once a structure exists it is held to
    settlement. v1 is measuring the base rate of this strategy, and an adjustment rule tuned before
    a single completion rate has been observed would be fitting noise.
    """
    out = []
    for p in positions:
        itm_legs = fly.itm_legs_at_settlement(p, settlement_price)
        assignment = fly.expire_fee(itm_legs)
        settled_fees = round(p.get("fees", 0.0) + assignment, 2)
        # status="settled" tells position_pnl the assignment fee is ALREADY folded into
        # settled_fees above -- without it, position_pnl would (correctly, for a still-open
        # position) compute its own fresh assignment fee from settlement_price and double-charge it.
        pnl = fly.position_pnl({**p, "fees": settled_fees, "status": "settled"}, settlement_price)
        out.append(
            {
                **p,
                "settlement_price": settlement_price,
                "expiry_payoff": round(_expiry_payoff(p, settlement_price), 4),
                "fees": settled_fees,
                "itm_legs": itm_legs,
                "assignment_fee": assignment,
                "pnl": round(pnl, 2),
                "pinned": p["kind"] in ("fly", "iron_fly")
                and abs(settlement_price - p["center"]) < p["wing_width"],
                "status": "settled",
            }
        )
    return out


def session_stats(positions: list[dict]) -> dict:
    """The three numbers the whole thesis turns on, per session.

    completion_rate  how often a credit spread actually became a fly. If this is near zero the
                     strategy is just short verticals wearing a costume.
    risk_free_rate   share of flies whose floor survived fees.
    pin_rate         share of flies that finished inside their wings (settled positions only).
    """
    flies = [p for p in positions if p["kind"] == "fly"]
    iron_flies = [p for p in positions if p["kind"] == "iron_fly"]
    bwbs = [p for p in positions if p["kind"] == "bwb"]
    legged = [p for p in positions if p.get("entry_mode") == "legged"]
    # A completion is a completion regardless of which completing trade closed it out -- an iron
    # completion counts toward legged's completion_rate exactly like a debit completion does.
    legged_completed = [p for p in legged if p["kind"] in ("fly", "iron_fly")]
    settled_flies = [p for p in flies + iron_flies if p.get("status") == "settled"]
    debit_first = [p for p in positions if p.get("entry_mode") == "debit_first"]
    debit_first_flies = [p for p in debit_first if p["kind"] == "fly"]
    bwb_roll_entries = [p for p in positions if p.get("entry_mode") == "bwb_roll"]
    bwb_rolled = [p for p in bwb_roll_entries if p["kind"] == "fly"]

    def _rate(n, d):
        return round(n / d, 4) if d else None

    return {
        "positions": len(positions),
        "flies": len(flies),
        "iron_completions": len(iron_flies),
        # Named for what it counts: structures that never became flies. This counts settled ones too,
        # because after the bell "still a vertical" is the outcome, not a transient state.
        "uncompleted_verticals": len([p for p in positions if p["kind"] == "short_vertical"]),
        "uncompleted_long_verticals": len([p for p in positions if p["kind"] == "long_vertical"]),
        # Every bwb still in this kind never got rolled to a symmetric fly -- its real, negative-
        # capable floor is the finding, same spirit as uncompleted_verticals.
        "unrolled_bwbs": len(bwbs),
        "completion_rate": _rate(len(legged_completed), len(legged)),
        "debit_first_completion_rate": _rate(len(debit_first_flies), len(debit_first)),
        "bwb_roll_rate": _rate(len(bwb_rolled), len(bwb_roll_entries)),
        # Includes iron flies and unrolled bwbs -- both carry a floor that can legitimately be
        # negative (unlike a same-type fly's), so they belong in this rate exactly as much as a
        # fly does; excluding either would silently hide the case this rate exists to catch.
        "risk_free_rate": _rate(
            len([p for p in flies + iron_flies + bwbs if fly.is_risk_free(p)]),
            len(flies) + len(iron_flies) + len(bwbs),
        ),
        "pin_rate": _rate(len([p for p in settled_flies if p.get("pinned")]), len(settled_flies)),
    }
