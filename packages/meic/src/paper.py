"""Synthetic-fill paper-trading engine for MEICAgent.

Pure functions over an already-fetched market snapshot — this module never calls the
broker. The calling skill (.claude/commands/paper-loop.md) fetches each symbol's chain,
quotes, VIX, and GEX once per iteration (exactly like the live loop's Step 4) and hands
the result to `process_symbol`, which evaluates all four risk profiles from
config.risk.json against that one snapshot, synthesizes fills/exits, and writes to the
paper database (paper_trades.db in the data home, selected via --db or MEIC_DB_PATH).

Design mirrors the live Step 6 hard-stops (CLAUDE.md) and the entry/stop math specified
in .claude/commands/execute-entry.md and .claude/commands/stop-management.md, but applies
them deterministically (fixed policy, no agent judgment) so the four profiles are
compared on identical, reproducible criteria. See docs/paper-trading.md.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

try:  # stdlib zoneinfo first (tzdata supplies the db on Windows); pytz only as fallback
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz
    _ET = pytz.timezone("America/New_York")


def _now_et():
    return datetime.now(_ET)

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.join(_HERE, "..")
_RISK_PROFILES_PATH = os.path.join(_REPO_ROOT, "config.risk.json")
_DB_PY = os.path.join(_HERE, "db.py")

# Shared market calendar from the cherrypick-core submodule (src/_core). Bootstrap it onto sys.path so
# this pure engine is importable standalone (tests, subprocess) with no install — mirrors the
# credentials.py bootstrap. The calendar computes NYSE holidays / quarterly + triple-witching expiries
# from rules (no hand-maintained per-year config lists, and no drift like the old 2026-06-18 bug).
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)  # so `import paths` resolves when imported (tests, paper_loop), not just as a script
_CORE = os.path.join(_HERE, "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from datetime import date as _date  # noqa: E402

from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import fees as _fees  # noqa: E402
from cherrypick.core import profiles as _profiles  # noqa: E402

import paths as _paths  # noqa: E402


def _is_event_day(today, predicate) -> bool:
    """Gate on a 'YYYY-MM-DD' snapshot date string (or None) via a cherrypick.core.calendar predicate."""
    if not today:
        return False
    try:
        d = _date.fromisoformat(today)
    except (ValueError, TypeError):
        return False
    return predicate(d)

ALL_PROFILE_NAMES = ["conservative", "moderate", "aggressive", "very-aggressive"]


def all_profile_names(profiles: dict | None = None) -> list[str]:
    """Every profile the paper engine evaluates, in a stable order: the canonical risk ladder
    first (ALL_PROFILE_NAMES), then any additional experiment / exploratory profiles from
    config.risk.json in file order. Derived from the profile registry so newly-added profiles
    are picked up without editing a hardcoded list — the four-name list was the only place
    (besides the EOD report) that pinned the roster to exactly the ladder."""
    profiles = load_profiles() if profiles is None else profiles
    # Skip underscore-prefixed registry keys (documentation notes, e.g. "_experiment_note"), which
    # are not profiles — mirrors merge_profile's own `_`-comment convention. Also skip any profile
    # explicitly turned off with `enabled: false` (the paper-experiment disable switch); a missing
    # `enabled` defaults to on, so the ladder and any active cell are unaffected.
    names = [n for n in profiles
             if not n.startswith("_") and profiles[n].get("enabled", True) is not False]
    ordered = [n for n in ALL_PROFILE_NAMES if n in names]
    ordered += [n for n in names if n not in ALL_PROFILE_NAMES]
    return ordered


# ---------------------------------------------------------------------------
# tastytrade fee schedule — one home in cherrypick.core.fees (src/_core). The schedule, constants, and
# per-symbol broad-based index exchange fee live in the shared module (also used by db.py's live
# fee estimate and, in equity form, by EarningsAgent). These thin wrappers keep MEIC's paper call
# sites + tests stable and pass ndigits=4 so paper fees keep their exact sub-cent precision (2dp
# rounding would break fee linearity across quantity). See cherrypick.core.fees.
# ---------------------------------------------------------------------------

def open_fees(symbol: str, quantity: int = 1) -> float:
    """Fee to open a full 4-leg IC (2 sell legs: short put + short call)."""
    return _fees.ic_open_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def close_fees_full_ic(symbol: str, quantity: int = 1) -> float:
    """Fee to actively close a full 4-leg IC (2 sell legs: BTC short put + short call)."""
    return _fees.ic_close_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)


def close_fees_one_side(symbol: str, quantity: int = 1) -> float:
    """Fee to actively close one side (2 legs: 1 short being bought back = 1 sell-side TAF)."""
    return _fees.ic_close_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)


def expire_fees() -> float:
    return _fees.ic_expire_fee()


# ---------------------------------------------------------------------------
# Modeled slippage (parity with cherrypick.core.fees / earnings)
#
# MEIC previously priced every paper fill at the worst case -- natural bid to open
# (short legs at bid, longs at ask) and ask/bid * stop_limit to close -- baking ~100%
# of the spread in as implicit slippage. That is far stricter than the earnings model
# (0.125 of the spread from mid). These helpers price a vertical at MID plus/minus a
# `slippage_frac_of_spread` haircut so both suite modules assume the same worked-limit
# fill. Settlement (cash-settled intrinsic value) has no spread and is unaffected.
#
# Keep this in lockstep with cherrypick.core.fees.DEFAULT_COSTS["slippage_frac_of_spread"];
# the 2026-07-17 slippage-literature review (see that module's comment) places 0.125 at the
# optimistic edge of the 25-50%-of-the-way-to-the-touch practitioner band -- kept here for parity.
# ---------------------------------------------------------------------------

DEFAULT_SLIPPAGE_FRAC = 0.125


def _leg_mid(q: dict) -> float:
    """Mid of a leg quote, falling back to (bid+ask)/2 when no explicit mid is present."""
    m = q.get("mid")
    return m if m is not None else (q.get("bid", 0.0) + q.get("ask", 0.0)) / 2.0


def _leg_spread(q: dict) -> float:
    return max(q.get("ask", 0.0) - q.get("bid", 0.0), 0.0)


def _open_credit(short_q: dict, long_q: dict, slippage_frac: float) -> float:
    """Credit received opening a vertical (sell short, buy long): mid MINUS the haircut."""
    mid = _leg_mid(short_q) - _leg_mid(long_q)
    return mid - slippage_frac * (_leg_spread(short_q) + _leg_spread(long_q))


def _close_cost(short_q: dict, long_q: dict, slippage_frac: float) -> float:
    """Cost to close a vertical (buy back short, sell long): mid PLUS the same haircut."""
    mid = _leg_mid(short_q) - _leg_mid(long_q)
    return mid + slippage_frac * (_leg_spread(short_q) + _leg_spread(long_q))


# ---------------------------------------------------------------------------
# Risk-profile loading
# ---------------------------------------------------------------------------

def load_profiles() -> dict:
    return _profiles.load_profiles(external_path=_RISK_PROFILES_PATH)


def load_base_config() -> dict:
    with open(_paths.config_path()) as f:
        return json.load(f)


def _merged_params(base_config: dict, profile: dict) -> dict:
    """Profile keys override base config keys, matching /set-risk-profile's partial-override
    semantics — unspecified keys (force_close_time, max_wing_width, etc.) fall through. Flat overlay
    via cherrypick.core.profiles (src/_core)."""
    return _profiles.merge_profile(base_config, profile)


def _profile_widths_for_symbol(params: dict, symbol: str) -> list | None:
    """This profile's wing shortlist for `symbol` (its own `wing_widths_by_symbol[symbol]`,
    falling back to `DEFAULT`), or None when it declares none — in which case the historical
    behavior applies (consider every scanned candidate width)."""
    wbs = params.get("wing_widths_by_symbol") or {}
    lst = wbs.get(symbol) or wbs.get(symbol.upper()) or wbs.get("DEFAULT")
    return list(lst) if lst else None


def union_widths_for_symbol(symbol: str, base_config: dict | None = None,
                            profiles: dict | None = None) -> list[int]:
    """The union of every evaluated profile's wing widths for `symbol` (plus the base config's),
    so paper_loop can build one candidate *menu* per symbol from which each profile then picks its
    own allowed subset. Without this, candidates were built from the base widths alone and a
    profile could never enter a width the base list didn't already scan (e.g. a 10-wide XSP)."""
    base_config = base_config or load_base_config()
    profiles = profiles or load_profiles()
    widths: set = set()
    base_wbs = base_config.get("wing_widths_by_symbol", {})
    for w in (base_wbs.get(symbol) or base_wbs.get("DEFAULT") or []):
        widths.add(w)
    for _name, pdef in profiles.items():
        if _name.startswith("_"):
            continue  # documentation note, not a profile
        params = _merged_params(base_config, pdef)
        prof_syms = [s.upper() for s in params.get("symbols", [])]
        if prof_syms and symbol.upper() not in prof_syms:
            continue
        for w in (_profile_widths_for_symbol(params, symbol) or []):
            widths.add(w)
    return sorted(widths)


def union_short_deltas_for_symbol(symbol: str, base_config: dict | None = None,
                                  profiles: dict | None = None) -> list[float]:
    """Distinct `short_delta_target` values any profile requests for `symbol` beyond the loop's
    VIX-banded default, so paper_loop can build candidates at each band. Profiles that declare
    none contribute nothing -- they use the banded-default candidate. Mirrors
    `union_widths_for_symbol`: one shared candidate menu per symbol, each profile picks its band."""
    base_config = base_config or load_base_config()
    profiles = profiles or load_profiles()
    deltas: set = set()
    for _name, pdef in profiles.items():
        if _name.startswith("_"):
            continue  # documentation note, not a profile
        params = _merged_params(base_config, pdef)
        prof_syms = [s.upper() for s in params.get("symbols", [])]
        if prof_syms and symbol.upper() not in prof_syms:
            continue
        d = params.get("short_delta_target")
        if d is not None:
            deltas.add(float(d))
    return sorted(deltas)


# ---------------------------------------------------------------------------
# Deterministic gate evaluator
# ---------------------------------------------------------------------------

def _time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _select_candidates(candidates: list, params: dict, symbol: str) -> list:
    """Restrict the scanned candidate menu to this profile's own wing shortlist for `symbol`
    (if it declares one) and order it per `wing_selection`. Default — no per-profile widths and
    no `wing_selection` — is the historical behavior: every candidate, widest-first (the fee-drag
    bias). `narrowest` reverses it (small-account cells); `fixed` preserves the profile's own
    width-list order (first listed = preferred)."""
    # Short-delta-band filter (multi-delta menus). A profile with short_delta_target sees only
    # candidates built at that band; a profile without it sees the loop's default (VIX-banded)
    # candidates. Menus with no short_delta tag at all (unit tests, legacy snapshots) pass through
    # untouched, so existing behavior is exactly preserved.
    target = params.get("short_delta_target")
    if target is not None:
        candidates = [c for c in candidates if c.get("short_delta") is not None
                      and abs(c["short_delta"] - target) < 1e-6]
    elif any(c.get("short_delta") is not None for c in candidates):
        candidates = [c for c in candidates if c.get("is_default_delta")]

    allowed = _profile_widths_for_symbol(params, symbol)
    if allowed is not None:
        candidates = [c for c in candidates if c["wing_width"] in allowed]
    sel = params.get("wing_selection", "widest")
    if sel == "narrowest":
        return sorted(candidates, key=lambda c: c["wing_width"])
    if sel == "fixed" and allowed is not None:
        order = {w: i for i, w in enumerate(allowed)}
        return sorted(candidates, key=lambda c: order.get(c["wing_width"], 1 << 30))
    return sorted(candidates, key=lambda c: -c["wing_width"])


def _low_iv_relief_max(params: dict) -> float:
    """The IV-rank ceiling below which the relaxed credit floor applies, expressed RELATIVE to this
    profile's own `min_iv_rank` (`+ low_iv_credit_floor_iv_rank_offset`).

    It used to be an absolute 0.35 shared by every tier — which was conservative's own 0.30 + 0.05,
    never rescaled for the looser rungs. The effect was backwards: conservative got relief over a
    0.05-wide band while very-aggressive (floor 0.15) got it across 0.15–0.35, i.e. most of its
    range. Falls back to the absolute key so any profile still setting it keeps prior behavior."""
    offset = params.get("low_iv_credit_floor_iv_rank_offset")
    if offset is not None:
        return params.get("min_iv_rank", 0.30) + offset
    return params.get("low_iv_credit_floor_iv_rank_max", 0.35)


def _low_iv_relief_floor(params: dict) -> float:
    """The relaxed credit floor itself, as a MULTIPLE of this profile's own credit floor
    (`min_credit_pct_of_width * low_iv_credit_relief_multiple`).

    It used to be a flat 0.10 on every tier, which silently flattened the ladder: whenever IV rank
    sat under the relief ceiling, all four tiers used the same 0.10 floor, so the 0.15/0.12/0.10/0.10
    progression stopped existing. (Measured on the paper book: 100% of SPX and XSP entries were in
    that zone, so conservative was trading them on identical credit terms to very-aggressive.) For
    aggressive/very-aggressive the "relief" even equalled their normal floor, so it did nothing at
    all. Expressing it as a multiple keeps it strictly below each tier's own floor and scales
    automatically. Falls back to the absolute key for profiles that still set it."""
    mult = params.get("low_iv_credit_relief_multiple")
    if mult is not None:
        return params["min_credit_pct_of_width"] * mult
    return params.get("low_iv_min_credit_pct_of_width", 0.10)


def _late_entry_bias_max(params: dict) -> float:
    """IV-rank ceiling under which the late-entry bias applies, relative to this profile's own
    `min_iv_rank` (`+ late_entry_bias_iv_rank_offset`). Same rescaling argument as
    `_low_iv_relief_max`: a flat 0.45 put very-aggressive under the bias across nearly its whole
    range while barely touching conservative."""
    offset = params.get("late_entry_bias_iv_rank_offset")
    if offset is not None:
        return params.get("min_iv_rank", 0.30) + offset
    return params.get("late_entry_bias_iv_rank_max", 0.45)


def evaluate_entry(snapshot: dict, params: dict, open_ics: list,
                   account_open_count: int | None = None,
                   todays_entry_count: int = 0, last_entry_min: int | None = None) -> tuple:
    """Encode the Step-6 hard-stops from CLAUDE.md against one profile's thresholds.

    snapshot: one pre-fetched market snapshot for a symbol/session (see paper-loop.md for
        exact shape) including a `candidates` list — one entry per scanned wing width.
    params: this profile's merged config (base config.json + config.risk.json overrides).
    open_ics: this profile's currently-open paper ICs **on this symbol** — used for the
        strike-overlap hard stop (strikes are only ever compared within the same symbol).
    account_open_count: the open-IC count the max_concurrent_ics cap is checked against. In the
        paper engine each (profile × symbol) portfolio has its own concurrency budget, so
        process_symbol passes this profile's open ICs **on this symbol** — a busy symbol can't
        starve a quiet one of slots. Defaults to len(open_ics) when omitted (which is already
        this-symbol-scoped for single-symbol callers / tests).
    todays_entry_count / last_entry_min: this profile's entries *so far today on this symbol* and
        the minute-of-day of its most recent one. `todays_entry_count` drives the
        daily_ic_trade_target cap (applied to every profile); `last_entry_min` feeds the spacing
        gate only when the profile opts into `stagger_entries`. process_symbol supplies them
        per-symbol from the paper DB; they default to a fresh day for pure callers / tests.

    Returns (enter: bool, reason: str, chosen_candidate: dict | None).
    """
    symbol = snapshot["symbol"]
    if account_open_count is None:
        account_open_count = len(open_ics)

    # 0DTE hard stop
    if snapshot.get("dte", 0) != 0:
        return False, "no_0dte_expiration", None

    # IV rank floor
    iv_rank = snapshot.get("iv_rank")
    if iv_rank is None or iv_rank < params["min_iv_rank"]:
        return False, "iv_rank_below_floor", None

    # Regime gate (VIX / VIX1D ratio / ATR / GEX)
    vix = snapshot.get("vix")
    if vix is not None and vix > params["regime_vix_pause_threshold"]:
        return False, "regime_vix_elevated", None
    vix1d_ratio = snapshot.get("vix1d_ratio")
    if vix1d_ratio is not None and vix1d_ratio > params.get("regime_vix1d_ratio_pause_threshold", 1.30):
        return False, "regime_vix1d_ratio_elevated", None
    atr = snapshot.get("atr_5day")
    atr_underlying = snapshot.get("underlying_price")
    # ATR gate is percentage-based (5-day ATR as a fraction of spot) so one threshold means
    # the same "elevated realized vol" across symbols spanning ~297 (IWM) to ~7500 (SPX) — a
    # fixed points threshold silently over-blocked SPX and never fired for QQQ/IWM.
    if atr is not None and atr_underlying and (atr / atr_underlying) > params["regime_atr_pause_threshold_pct"]:
        return False, "regime_atr_elevated", None
    gex = snapshot.get("gex") or {}
    if gex.get("ok") and gex.get("gex_positive") is False:
        return False, "regime_gex_negative", None
    # Strict variant (opt-in per profile): require GEX to be explicitly positive, so entries
    # also pause when GEX is unknown/unavailable -- not just when it is confirmed negative. Used
    # opt-in per profile to isolate the effect of the GEX gate.
    if params.get("regime_gex_require_positive", False) and not (
            gex.get("ok") and gex.get("gex_positive") is True):
        return False, "regime_gex_not_positive", None
    # Magnitude variant (opt-in): require positive GEX AND spot at least this fraction from the
    # gamma-flip strike -- deep enough inside positive-gamma territory that pinning dominates, not
    # hovering near the flip where the regime is fragile.
    min_flip_dist = params.get("regime_gex_min_flip_distance_pct")
    if min_flip_dist is not None:
        flip, spot = gex.get("gamma_flip"), gex.get("spot")
        deep_positive = (gex.get("ok") and gex.get("gex_positive") is True and flip and spot
                         and abs(spot - flip) / spot >= min_flip_dist)
        if not deep_positive:
            return False, "regime_gex_flip_too_close", None

    now_min = _time_to_minutes(snapshot["now_et"])

    # Daily target is GUIDANCE, not a hard cap (matching CLAUDE.md): once a portfolio has reached
    # its daily_ic_trade_target on this symbol it may still trade, but only a higher-conviction
    # setup qualifies — the credit floor is scaled by `over_target_credit_multiple` in the candidate
    # loop below. Favorable conditions (richer premium) therefore allow more entries, while marginal
    # ones don't. Counted per (profile × symbol), so a busy symbol never spends a quiet one's budget.
    # The hard stops (max_concurrent_ics, regime gates, delta/OTM floors) still bind normally.
    target = params.get("daily_ic_trade_target", 0)
    over_target = bool(target) and todays_entry_count >= target

    # Staggered-entry controls (opt-in per profile via `stagger_entries`) — spread the daily target
    # across the session instead of filling every slot in the first passing iterations, giving
    # time-of-day coverage of when a condor is opened. This block also enforces the entry window,
    # which the live loop applies (10:00–14:30) but the paper gate historically did not — paper could
    # open a fresh 0DTE IC as late as the force-close. Here the daily target IS a hard cap: staggering
    # exists to spread a fixed number of entries, so opting in means opting into the throttle. Ladder
    # profiles omit `stagger_entries` and keep the soft-guidance behavior above.
    if params.get("stagger_entries"):
        ews = _time_to_minutes(params.get("entry_window_start", "10:00"))
        ewe = _time_to_minutes(params.get("entry_window_end", "14:30"))
        if now_min < ews or now_min >= ewe:
            return False, "outside_entry_window", None
        if over_target:
            return False, "daily_target_reached", None
        spacing = params.get("min_minutes_between_entries", 0)
        if spacing and last_entry_min is not None and (now_min - last_entry_min) < spacing:
            return False, "entry_spacing_wait", None

    # Late-entry bias
    if params.get("late_entry_bias_enabled") and iv_rank <= _late_entry_bias_max(params):
        if now_min < _time_to_minutes(params["late_entry_bias_start_time"]):
            return False, "late_entry_bias_wait", None

    # Concurrent IC cap — account-wide for this profile (across every symbol), matching the
    # live loop's account-wide max_concurrent_ics; each profile is its own virtual account.
    if account_open_count >= params["max_concurrent_ics"]:
        return False, "max_concurrent_ics_reached", None

    # Quarterly / triple-witching hard stops
    today = snapshot.get("date")
    is_quarterly = _is_event_day(today, _cal.is_quarterly_expiry)
    is_witching = _is_event_day(today, _cal.is_triple_witching)
    if is_quarterly or is_witching:
        if snapshot.get("session_quality") == "open_volatile":
            return False, "quarterly_open_volatile_skip", None
        if is_witching and now_min > _time_to_minutes("12:30"):
            return False, "triple_witching_no_new_entries", None

    # FOMC blackout
    if _is_event_day(today, _cal.is_fomc_day):
        blackout_start = _time_to_minutes(params.get("fomc_blackout_start", "13:30"))
        blackout_end = _time_to_minutes(params.get("fomc_blackout_end", "14:30"))
        if now_min >= blackout_start and now_min < blackout_end:
            return False, "fomc_blackout", None
        if now_min >= blackout_end:
            if iv_rank < 0.40 or snapshot.get("intraday_range", 0) > 3.5:
                return False, "fomc_post_blackout_insufficient_premium", None

    open_strikes = set()
    for ic in open_ics:
        for k in ("put_strike", "call_strike"):
            if ic.get(k) is not None:
                open_strikes.add(float(ic[k]))

    # Restrict to this profile's own wing shortlist for this symbol and order it per its
    # `wing_selection` (default: widest-first, the fee-drag bias). Taking the first candidate
    # that clears every gate then yields the profile's preferred width automatically.
    candidates = _select_candidates(snapshot.get("candidates", []), params, symbol)
    otm_floor_call = params["min_call_otm_pct"]
    otm_floor_put = params["min_put_otm_pct"]
    if is_quarterly:
        otm_floor_call = max(otm_floor_call, params.get("quarterly_expiry_min_call_otm_pct", otm_floor_call))

    session = snapshot.get("session_quality")
    delta_ceiling = params["max_call_delta_entry"]
    if session == "open_volatile":
        delta_ceiling = params.get("max_call_delta_entry_open_volatile", delta_ceiling)
    elif session == "late":
        delta_ceiling = params.get("max_call_delta_entry_late", delta_ceiling)

    underlying = snapshot["underlying_price"]
    last_reason = "no_candidate_cleared_all_gates"

    for cand in candidates:
        sp, lp, sc, lc = cand["short_put"], cand["long_put"], cand["short_call"], cand["long_call"]
        wing_width = cand["wing_width"]

        # Strike overlap hard stop (this profile's own open ICs on this symbol)
        cand_strikes = {sp["strike"], lp["strike"], sc["strike"], lc["strike"]}
        if cand_strikes & open_strikes:
            last_reason = "strike_overlap"
            continue

        # Call delta hard stop
        if sc.get("delta") is None or abs(sc["delta"]) > delta_ceiling:
            last_reason = "call_delta_exceeds_ceiling"
            continue

        # OTM distance hard stops
        call_otm_pct = (sc["strike"] - underlying) / underlying
        put_otm_pct = (underlying - sp["strike"]) / underlying
        if call_otm_pct < otm_floor_call:
            last_reason = "call_otm_below_floor"
            continue
        if put_otm_pct < otm_floor_put:
            last_reason = "put_otm_below_floor"
            continue

        # Credit floor (with low-IV relief) + fee-adjusted floor. Priced at mid minus the
        # slippage haircut (see _open_credit) so the gate screens on the credit we model
        # actually receiving -- consistent with the recorded fill and with the earnings model.
        slippage_frac = params.get("slippage_frac_of_spread", DEFAULT_SLIPPAGE_FRAC)
        put_credit = _open_credit(sp, lp, slippage_frac)
        call_credit = _open_credit(sc, lc, slippage_frac)
        net_credit = put_credit + call_credit
        if net_credit <= 0:
            last_reason = "non_positive_credit"
            continue
        low_iv_relief = iv_rank <= _low_iv_relief_max(params)
        pct_floor = _low_iv_relief_floor(params) if low_iv_relief \
            else params["min_credit_pct_of_width"]
        # Past the daily target the bar rises: only a richer-than-usual credit earns an extra entry
        # (the daily target is guidance, so favorable conditions allow more — see `over_target`).
        if over_target:
            pct_floor *= params.get("over_target_credit_multiple", 1.5)
        if net_credit < pct_floor * wing_width:
            last_reason = "over_target_credit_below_floor" if over_target else "credit_below_floor"
            continue

        multiplier = 100
        gross_credit_dollars = net_credit * multiplier
        fee = open_fees(symbol, quantity=1)
        if gross_credit_dollars - fee < pct_floor * wing_width * multiplier:
            last_reason = ("over_target_credit_below_floor" if over_target
                           else "credit_below_fee_adjusted_floor")
            continue

        chosen = dict(cand)
        chosen["net_credit"] = round(net_credit, 4)
        chosen["put_credit"] = round(put_credit, 4)
        chosen["call_credit"] = round(call_credit, 4)
        # Worst-case natural bid retained for reference/back-compat (dashboards, diagnostics).
        chosen["ic_natural_bid"] = round((sp["bid"] + sc["bid"]) - (lp["ask"] + lc["ask"]), 4)
        chosen["open_fee"] = fee
        return True, "entered", chosen

    return False, last_reason, None


# ---------------------------------------------------------------------------
# Synthetic fill / mark / exit engine
# ---------------------------------------------------------------------------

def synthetic_entry_fill(snapshot: dict, profile_name: str, chosen: dict, params: dict, execution_mode: str) -> dict:
    """Build the ic_trades row for a synthetic fill priced at mid minus the slippage haircut."""
    now = str(_now_et())
    today = snapshot["date"]
    sp, lp, sc, lc = chosen["short_put"], chosen["long_put"], chosen["short_call"], chosen["long_call"]
    order_id = f"PAPER-{profile_name}-{snapshot['symbol']}-{_now_et().strftime('%Y%m%d%H%M%S%f')}"
    return {
        "ic_order_id": order_id,
        "trade_date": today,
        "entry_time": now,
        "expiration": snapshot.get("expiration", today),
        "symbol": snapshot["symbol"],
        "put_strike": sp["strike"],
        "call_strike": sc["strike"],
        "wing_width": chosen["wing_width"],
        "put_symbol": sp.get("streamer_symbol"),
        "call_symbol": sc.get("streamer_symbol"),
        "long_put_symbol": lp.get("streamer_symbol"),
        "long_call_symbol": lc.get("streamer_symbol"),
        "put_credit": chosen["put_credit"],
        "call_credit": chosen["call_credit"],
        "net_credit": chosen["net_credit"],
        "quantity": 1,
        "put_delta_at_entry": sp.get("delta"),
        "call_delta_at_entry": sc.get("delta"),
        "long_put_delta_at_entry": lp.get("delta"),
        "long_call_delta_at_entry": lc.get("delta"),
        "underlying_price_entry": snapshot["underlying_price"],
        "iv_rank_at_entry": snapshot.get("iv_rank"),
        # Captured alongside rank because the two diverge (rank is outlier-compressed, percentile is
        # not) and the entry gate may be re-based on percentile — see paper_loop._fetch_overview.
        "iv_pct_at_entry": snapshot.get("iv_pct"),
        "session_quality": snapshot.get("session_quality"),
        "iv_skew_signal": snapshot.get("iv_skew_signal"),
        "price_action_signal": snapshot.get("price_action_signal"),
        "ai_entry_reasoning": f"paper/{execution_mode}/{profile_name}: deterministic entry, "
                               f"widest clearing candidate ({chosen['wing_width']}-wide)",
        "stop_trigger_original": params["stop_trigger_ratio"],
        "stop_limit_original": params.get("stop_limit_ratio", 1.02),
        "stop_trigger_current": params["stop_trigger_ratio"],
        "stop_limit_current": params.get("stop_limit_ratio", 1.02),
        "status": "open",
        "fill_confirmed_at": now,
        "fees": chosen["open_fee"],
        "risk_profile": profile_name,
        "execution_mode": execution_mode,
        "iv_rank_source": snapshot.get("iv_rank_source", "native"),
        "created_at": now,
        "updated_at": now,
    }


def _leg_quote(snapshot_legs: dict, streamer_symbol: str):
    return snapshot_legs.get(streamer_symbol)


# ---------------------------------------------------------------------------
# Physical-settlement exit hardening
# ---------------------------------------------------------------------------
# SPX/XSP are cash-settled European-style — a force-close that misses just settles to
# cash. QQQ/IWM (and any other symbol NOT in cash_settled_symbols) are physically-settled
# American-style: a short leg left open into expiration risks assignment into shares, and a
# strike pinned at-the-money at the bell has an ambiguous ITM/OTM outcome. The live loop
# closes these earlier (physical_settlement_force_close_time) and escalates a failed close
# to CRITICAL; here in paper we mirror the earlier close AND add modeled friction so paper
# P&L for physically-settled symbols isn't misleadingly cleaner than live. See
# docs/paper-trading.md. Friction figures are deliberately conservative, config-tunable, and
# meant to be calibrated against a tiny-live run later (same philosophy as the 20-40% discount).

def _is_cash_settled(symbol: str, base_config: dict) -> bool:
    listed = [s.upper() for s in base_config.get("cash_settled_symbols", [])]
    return symbol.upper() in listed


def _pin_penalty(strike, underlying, wing_width, params: dict) -> float:
    """Extra force-close cost when a short strike is pinned ~ATM at the close — the outcome
    (assigned vs. expires worthless) is a coin flip there, so the modeled cost jumps toward a
    fraction of the wing width. Zero when the strike is comfortably away from spot."""
    if strike is None or not underlying:
        return 0.0
    threshold = params.get("pin_risk_threshold_pct", 0.002)
    if abs(strike - underlying) / underlying < threshold:
        return params.get("pin_risk_penalty_pct_of_width", 0.25) * (wing_width or 0)
    return 0.0


def force_close_active(snapshot: dict, base_config: dict, is_cash_settled: bool) -> tuple:
    """Return (active: bool, reason: str|None) for whether this symbol's open positions must
    be force-closed at the snapshot's time. Mirrors the live loop's force-close cascade
    (stop-management.md Step 7) which paper previously only partially implemented — it had
    just the 15:45 EOD close, missing the FOMC/expiry event closes and the earlier
    physically-settled close entirely."""
    now_min = _time_to_minutes(snapshot["now_et"])
    today = snapshot.get("date")

    # FOMC blackout — all symbols, earliest trigger of the day
    if _is_event_day(today, _cal.is_fomc_day) and \
            now_min >= _time_to_minutes(base_config.get("fomc_blackout_start", "13:30")):
        return True, "force_close_fomc"
    # Triple-witching / quarterly expiry — all symbols, 14:00 ET
    if (_is_event_day(today, _cal.is_triple_witching) or
            _is_event_day(today, _cal.is_quarterly_expiry)) and now_min >= _time_to_minutes("14:00"):
        return True, "force_close_expiry_event"
    # Non-cash-settled symbols (QQQ/IWM/equities/futures) are closed before the bell to avoid
    # physical assignment — first at the earlier physical deadline, then a 15:45 backstop.
    # Cash-settled symbols are NOT force-closed here: they are left to expire and settled at
    # the close (see settlement_active). This is the core of the corrected MEIC exit model.
    if not is_cash_settled:
        if now_min >= _time_to_minutes(base_config.get("physical_settlement_force_close_time", "15:30")):
            return True, "force_close_physical_settlement"
        if now_min >= _time_to_minutes(base_config.get("force_close_time", "15:45")):
            return True, "force_close_eod"
    return False, None


def settlement_active(snapshot: dict, base_config: dict, is_cash_settled: bool) -> bool:
    """True when a cash-settled position should be settled at expiration (0DTE PM settlement
    at the close). This is the 'left to expire' exit path — the position is not bought back;
    its P&L is the credit minus any ITM intrinsic at the settlement price (capped at the wing
    width). Non-cash-settled symbols never reach here — they are force-closed before the bell."""
    if not is_cash_settled:
        return False
    now_min = _time_to_minutes(snapshot["now_et"])
    return now_min >= _time_to_minutes(base_config.get("expiration_settlement_time", "16:00"))


def _settlement_value(strike, underlying, wing_width, side) -> float:
    """The value a defined-risk spread settles for at expiration: the short strike's intrinsic
    value, floored at 0 (expires worthless) and capped at the wing width (fully-ITM = max
    loss). Used for the cash-settled 'left to expire' path."""
    if strike is None or underlying is None:
        return 0.0
    intrinsic = (strike - underlying) if side == "put" else (underlying - strike)
    return round(min(max(intrinsic, 0.0), wing_width or 0.0), 4)


def evaluate_open_trade(trade: dict, leg_quotes: dict, params: dict, force_close: bool,
                        underlying_price: float | None = None, is_cash_settled: bool = True,
                        force_close_reason: str = "force_close_eod", settle: bool = False) -> dict:
    """Mark-to-market one open paper IC and decide its exit (MEIC has no profit target).

    leg_quotes: {streamer_symbol: {"bid":.., "ask":.., "mid":..}} for this trade's 4 legs,
    taken from the snapshot's per-symbol quote set.
    force_close / settle: set by process_symbol from force_close_active / settlement_active.

    Returns a dict describing the action: {"action": "hold"|"stop_put"|"stop_call"|"stop_both"|
    "force_close"|"expire", ...pricing...}. The caller (process_symbol) applies it to the DB.
    """
    # A side is still open only if it hasn't already been closed. In paper, a closed side is
    # marked by its recorded stop cost (put_stop_cost / call_stop_cost) — the live-only
    # *_stop_order_id fields are never set here, so keying off them would re-stop an
    # already-stopped side on every subsequent iteration and double-count its P&L.
    put_open = trade["status"] in ("open", "partial") and trade.get("put_stop_cost") is None
    call_open = trade["status"] in ("open", "partial") and trade.get("call_stop_cost") is None

    # Expiration settlement ('left to expire', cash-settled) needs only the strikes and the
    # settlement price — not live leg quotes — so handle it before the quote-availability gate
    # so a missing quote at the close can't strand an expiring position. force_close (events /
    # non-cash) takes precedence since it fires earlier in the day.
    if settle and not force_close:
        return {
            "action": "expire",
            "put_open": put_open,
            "call_open": call_open,
            "put_exit_price": _settlement_value(trade.get("put_strike"), underlying_price,
                                                trade.get("wing_width"), "put") if put_open else None,
            "call_exit_price": _settlement_value(trade.get("call_strike"), underlying_price,
                                                 trade.get("wing_width"), "call") if call_open else None,
        }

    sq = leg_quotes.get(trade["put_symbol"])
    cq = leg_quotes.get(trade["call_symbol"])
    lpq = leg_quotes.get(trade["long_put_symbol"])
    lcq = leg_quotes.get(trade["long_call_symbol"])
    if not all([sq, cq, lpq, lcq]):
        return {"action": "hold", "reason": "quotes_unavailable"}

    net_credit = trade["net_credit"]
    slippage_frac = params.get("slippage_frac_of_spread", DEFAULT_SLIPPAGE_FRAC)
    # MEIC has no profit target: an iron condor is only ever closed by a per-side stop, a
    # (non-cash-settled) time-based force-close, or an event force-close. See docs/strategy.md.
    if force_close:
        put_exit = max(_close_cost(sq, lpq, slippage_frac), 0) if put_open else None
        call_exit = max(_close_cost(cq, lcq, slippage_frac), 0) if call_open else None
        friction_applied = False
        if not is_cash_settled:
            # Physically-settled symbols pay a modeled friction on the force-close (wider
            # spreads near the bell + assignment/pin risk) so paper doesn't overstate their
            # safety vs. cash-settled index products. Added to the cost-to-close, so P&L
            # (credit − exit_price) moves the right (worse) direction.
            friction = params.get("physical_settlement_exit_friction", 0.05)
            if put_open:
                put_exit = round(put_exit + friction +
                                 _pin_penalty(trade.get("put_strike"), underlying_price,
                                              trade.get("wing_width"), params), 4)
                friction_applied = True
            if call_open:
                call_exit = round(call_exit + friction +
                                  _pin_penalty(trade.get("call_strike"), underlying_price,
                                               trade.get("wing_width"), params), 4)
                friction_applied = True
        return {
            "action": "force_close",
            "put_open": put_open,
            "call_open": call_open,
            "put_exit_price": put_exit,
            "call_exit_price": call_exit,
            "reason": force_close_reason,
            "physical_friction_applied": friction_applied,
        }

    stop_trigger = trade.get("stop_trigger_current") or params["stop_trigger_ratio"]
    call_cost_mid = cq["mid"] - lcq["mid"]
    put_cost_mid = sq["mid"] - lpq["mid"]

    # per_side_stop_management: false disables per-side stops entirely (a hold-to-expiry
    # cell), so the IC is only ever closed by a force-close or settlement -- held to expiry.
    stops_on = params.get("per_side_stop_management", True)
    call_trigger = stops_on and call_open and call_cost_mid >= stop_trigger * net_credit
    put_trigger = stops_on and put_open and put_cost_mid >= stop_trigger * net_credit

    # Closing fills priced at mid + slippage haircut (see _close_cost); the former
    # (short_ask - long_bid) * stop_limit worst-case model is superseded by the parity model.
    if call_trigger and put_trigger:
        return {
            "action": "stop_both",
            "put_exit_price": round(_close_cost(sq, lpq, slippage_frac), 4),
            "call_exit_price": round(_close_cost(cq, lcq, slippage_frac), 4),
        }
    if call_trigger:
        return {"action": "stop_call", "call_exit_price": round(_close_cost(cq, lcq, slippage_frac), 4)}
    if put_trigger:
        return {"action": "stop_put", "put_exit_price": round(_close_cost(sq, lpq, slippage_frac), 4)}

    return {"action": "hold"}


# ---------------------------------------------------------------------------
# DB I/O (shells out to db.py, pointed at the paper DB)
# ---------------------------------------------------------------------------

def _db(args_list: list, db_path: str) -> dict:
    cmd = [sys.executable, _DB_PY, "--db", db_path] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"db.py exited {result.returncode}"}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": f"unparseable db.py output: {result.stdout!r}"}


def _save_trade(row: dict, db_path: str) -> dict:
    return _db(["save_trade", "--data", json.dumps(row)], db_path)


def _update_trade(ic_order_id: str, fields: dict, db_path: str) -> dict:
    args_list = ["update_trade", "--ic_order_id", ic_order_id]
    for k, v in fields.items():
        args_list += [f"--{k}", str(v)]
    return _db(args_list, db_path)


def _get_open_trades(symbol: str, profile: str, trade_date: str, db_path: str) -> list:
    # --date pins the query to the snapshot's own trade_date rather than the real system
    # clock, which is required for replay mode (see db.py's cmd_get_open_trades docstring).
    result = _db(["get_open_trades", "--symbol", symbol, "--date", trade_date], db_path)
    trades = result.get("open_trades", []) if result.get("ok") else []
    return [t for t in trades if t.get("risk_profile") == profile]


def _minutes_of_day(iso_str) -> int | None:
    """Minute-of-day (0–1439) from a recorded entry_time. entry_time is `str(_now_et())`, an
    ISO datetime; fall back to splitting a bare 'HH:MM:SS'. Returns None if unparseable."""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        return dt.hour * 60 + dt.minute
    except (ValueError, TypeError):
        try:
            hh, mm = str(iso_str).strip().split(" ")[-1].split(":")[:2]
            return int(hh) * 60 + int(mm)
        except (ValueError, IndexError):
            return None


def _profile_day_stats(profile: str, trade_date: str, db_path: str, symbol: str | None = None) -> tuple:
    """(entry_count, last_entry_min) for this profile's non-cancelled entries on `trade_date`.
    When `symbol` is given the count is scoped to that symbol — the per-(profile × symbol) daily-cap
    + spacing inputs, so each portfolio's daily_ic_trade_target is its own budget. Read-only direct
    query (cheap; avoids adding subprocess spawns per profile per iteration once the roster is large)."""
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        q = ("SELECT entry_time FROM ic_trades WHERE trade_date=? AND risk_profile=? "
             "AND status NOT IN ('cancelled')")
        params = [trade_date, profile]
        if symbol:
            q += " AND symbol=?"
            params.append(symbol.upper())
        rows = con.execute(q, params).fetchall()
        con.close()
    except sqlite3.Error:
        return 0, None
    last = None
    for r in rows:
        m = _minutes_of_day(r["entry_time"])
        if m is not None and (last is None or m > last):
            last = m
    return len(rows), last


def process_symbol(snapshot: dict, db_path: str, execution_mode: str, profiles_filter=None) -> dict:
    """Run all four profiles' mark-to-market/exit + entry evaluation for one symbol against
    one already-fetched snapshot. Returns a per-profile action summary for logging."""
    base_config = load_base_config()
    all_profiles = load_profiles()
    names = profiles_filter or all_profile_names(all_profiles)
    symbol = snapshot["symbol"]
    is_cash = _is_cash_settled(symbol, base_config)
    force_close, force_close_reason = force_close_active(snapshot, base_config, is_cash)
    settle = settlement_active(snapshot, base_config, is_cash)
    underlying_price = snapshot.get("underlying_price")
    leg_quotes = snapshot.get("leg_quotes", {})

    results = {}
    for name in names:
        params = _merged_params(base_config, all_profiles[name])
        # Per-profile symbol restriction: a profile only trades the symbols it declares (default:
        # all base symbols). A profile pinned to XSP is skipped entirely for an SPX snapshot — it
        # never opened positions there, so there is nothing to mark or exit either.
        prof_syms = [s.upper() for s in params.get("symbols", [])]
        if prof_syms and symbol.upper() not in prof_syms:
            continue
        open_ics = _get_open_trades(symbol, name, snapshot["date"], db_path)
        actions = []

        for trade in open_ics:
            decision = evaluate_open_trade(trade, leg_quotes, params, force_close,
                                           underlying_price=underlying_price,
                                           is_cash_settled=is_cash,
                                           force_close_reason=force_close_reason,
                                           settle=settle)
            actions.append({"ic_order_id": trade["ic_order_id"], "decision": decision})
            _apply_exit_decision(trade, decision, symbol, db_path)

        still_open = _get_open_trades(symbol, name, snapshot["date"], db_path)
        # max_concurrent_ics is a per-(profile × symbol) cap: each portfolio (this profile on this
        # symbol) gets its own concurrency budget, so a busy symbol can't starve a quiet one of
        # slots. `still_open` is already scoped to this symbol + profile, so its length is the count.
        symbol_open = len(still_open)
        if symbol_open < params["max_concurrent_ics"]:
            # Per-(profile × symbol) day stats: entry_count feeds the daily_ic_trade_target cap that
            # applies to every profile; last_entry_min additionally feeds the opt-in staggering
            # spacing gate. Scoped to this symbol so each portfolio has its own daily budget.
            todays_entries, last_entry_min = _profile_day_stats(name, snapshot["date"], db_path, symbol=symbol)
            entered, reason, chosen = evaluate_entry(snapshot, params, still_open,
                                                     account_open_count=symbol_open,
                                                     todays_entry_count=todays_entries,
                                                     last_entry_min=last_entry_min)
            if entered:
                row = synthetic_entry_fill(snapshot, name, chosen, params, execution_mode)
                save_result = _save_trade(row, db_path)
                actions.append({"entry": "filled", "ic_order_id": row["ic_order_id"],
                                 "net_credit": row["net_credit"], "save_result": save_result})
            else:
                actions.append({"entry": "skipped", "reason": reason})
        else:
            actions.append({"entry": "skipped", "reason": "max_concurrent_ics_reached"})

        results[name] = actions

    return {"ok": True, "symbol": symbol, "results": results}


def _apply_exit_decision(trade: dict, decision: dict, symbol: str, db_path: str) -> None:
    action = decision["action"]
    now = str(_now_et())
    ic_order_id = trade["ic_order_id"]
    mult = 100

    if action == "hold":
        return

    if action == "expire":
        # Cash-settled 'left to expire': settle each still-open side at its intrinsic value
        # (already capped at the wing width). No fees — expiration is not a transaction.
        existing_pnl = trade.get("pnl") or 0
        delta_pnl = 0
        if decision["put_open"]:
            put_pnl = round((trade["put_credit"] - decision["put_exit_price"]) * mult, 2)
            delta_pnl += put_pnl
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "put",
                 "--status", "expired", "--exit_time", now, "--exit_reason", "expired_settlement",
                 "--exit_price", str(decision["put_exit_price"]), "--pnl", str(put_pnl)], db_path)
        if decision["call_open"]:
            call_pnl = round((trade["call_credit"] - decision["call_exit_price"]) * mult, 2)
            delta_pnl += call_pnl
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "call",
                 "--status", "expired", "--exit_time", now, "--exit_reason", "expired_settlement",
                 "--exit_price", str(decision["call_exit_price"]), "--pnl", str(call_pnl)], db_path)
        # If a side was already stopped intraday, the IC ends as 'stopped' (not 'expired') so stop
        # vs. expiry stays distinguishable at the IC level; a clean both-sides-expire is 'expired'.
        was_stopped = trade.get("put_stop_cost") is not None or trade.get("call_stop_cost") is not None
        _update_trade(ic_order_id, {
            "status": "stopped" if was_stopped else "expired", "exit_time": now,
            "exit_reason": "stopped+expired_settlement" if was_stopped else "expired_settlement",
            "pnl": existing_pnl + delta_pnl,
        }, db_path)
        return

    if action in ("stop_call", "stop_put", "stop_both"):
        fee = close_fees_one_side(symbol) if action != "stop_both" else close_fees_full_ic(symbol)
        updates = {"fees": (trade.get("fees") or 0) + fee}
        if action in ("stop_call", "stop_both"):
            call_pnl = round((trade["call_credit"] - decision["call_exit_price"]) * mult, 2)
            updates["call_stop_cost"] = decision["call_exit_price"]
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "call",
                 "--status", "stopped", "--exit_time", now, "--exit_reason", "per_side_stop",
                 "--exit_price", str(decision["call_exit_price"]), "--pnl", str(call_pnl)], db_path)
        if action in ("stop_put", "stop_both"):
            put_pnl = round((trade["put_credit"] - decision["put_exit_price"]) * mult, 2)
            updates["put_stop_cost"] = decision["put_exit_price"]
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "put",
                 "--status", "stopped", "--exit_time", now, "--exit_reason", "per_side_stop",
                 "--exit_price", str(decision["put_exit_price"]), "--pnl", str(put_pnl)], db_path)
        updates["status"] = "stopped" if action == "stop_both" else "partial"
        _update_trade(ic_order_id, updates, db_path)
        # Accumulate realized pnl for stopped legs; final pnl reconciled when the other
        # side (if any) later resolves via force_close or a subsequent stop.
        existing_pnl = trade.get("pnl") or 0
        delta_pnl = 0
        if action in ("stop_call", "stop_both"):
            delta_pnl += round((trade["call_credit"] - decision["call_exit_price"]) * mult, 2)
        if action in ("stop_put", "stop_both"):
            delta_pnl += round((trade["put_credit"] - decision["put_exit_price"]) * mult, 2)
        _update_trade(ic_order_id, {"pnl": existing_pnl + delta_pnl}, db_path)
        return

    if action == "force_close":
        fee = close_fees_full_ic(symbol) if (decision["put_open"] and decision["call_open"]) \
            else close_fees_one_side(symbol)
        reason = decision.get("reason") or "force_close_eod"
        existing_pnl = trade.get("pnl") or 0
        delta_pnl = 0
        if decision["put_open"]:
            put_pnl = round((trade["put_credit"] - decision["put_exit_price"]) * mult, 2)
            delta_pnl += put_pnl
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "put",
                 "--status", "force_closed", "--exit_time", now, "--exit_reason", reason,
                 "--exit_price", str(decision["put_exit_price"]), "--pnl", str(put_pnl)], db_path)
        if decision["call_open"]:
            call_pnl = round((trade["call_credit"] - decision["call_exit_price"]) * mult, 2)
            delta_pnl += call_pnl
            _db(["record_leg_exit", "--ic_order_id", ic_order_id, "--side", "call",
                 "--status", "force_closed", "--exit_time", now, "--exit_reason", reason,
                 "--exit_price", str(decision["call_exit_price"]), "--pnl", str(call_pnl)], db_path)
        # Preserve a prior per-side stop in the IC-level status (stop vs. force-close stays legible).
        was_stopped = trade.get("put_stop_cost") is not None or trade.get("call_stop_cost") is not None
        _update_trade(ic_order_id, {
            "status": "stopped" if was_stopped else "force_closed", "exit_time": now,
            "exit_reason": (reason + "+prior_stop") if was_stopped else reason,
            "pnl": existing_pnl + delta_pnl, "fees": (trade.get("fees") or 0) + fee,
        }, db_path)
        return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MEICAgent paper-trading engine")
    parser.add_argument("--db", default=str(_paths.paper_db_path()))
    sub = parser.add_subparsers(dest="command")

    p_proc = sub.add_parser("process_symbol",
                             help="Evaluate all four profiles' exits + entries for one symbol "
                                  "against a pre-fetched market snapshot")
    p_proc.add_argument("--snapshot", required=True, help="Snapshot JSON (or @path to a file)")
    p_proc.add_argument("--execution_mode", default="paper", choices=["paper", "replay"])
    p_proc.add_argument("--profiles", default=None,
                         help="Comma-separated subset of profiles; omit for all four")

    p_fees = sub.add_parser("fees", help="Compute the tastytrade fee stack for one action")
    p_fees.add_argument("--symbol", required=True)
    p_fees.add_argument("--action", required=True, choices=["open", "close_full", "close_side", "expire"])
    p_fees.add_argument("--quantity", type=int, default=1)

    args = parser.parse_args()

    if args.command == "process_symbol":
        raw = args.snapshot
        if raw.startswith("@"):
            with open(raw[1:]) as f:
                raw = f.read()
        snapshot = json.loads(raw)
        profiles_filter = args.profiles.split(",") if args.profiles else None
        result = process_symbol(snapshot, args.db, args.execution_mode, profiles_filter)
        print(json.dumps(result, default=str))
    elif args.command == "fees":
        fn = {
            "open": open_fees, "close_full": close_fees_full_ic,
            "close_side": close_fees_one_side, "expire": lambda s, q: expire_fees(),
        }[args.action]
        fee = fn(args.symbol, args.quantity)
        print(json.dumps({"ok": True, "symbol": args.symbol, "action": args.action, "fee": fee}))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
