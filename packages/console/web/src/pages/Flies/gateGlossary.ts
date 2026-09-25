/**
 * Plain-English one-liners for the decision journal's `reason` codes -- the gate/outcome names
 * `cherrypick.flies.engine`/`book.py` record verbatim (see their docstrings for the full
 * rationale; this is the console-side gloss, not a re-statement of the measurement behind each
 * one). Unmapped codes render with no tooltip rather than a guessed description.
 */
export const GATE_GLOSSARY: Record<string, string> = {
  // day-level gates, checked before any strike is picked
  no_0dte_expiration: "Not a 0DTE session for this symbol -- the module only trades same-day expiry.",
  before_open_gate: "Inside the post-open blackout (no_entry_before) -- a floor no arm's own window can override.",
  early_close_session: "An NYSE 13:00 early-close day -- no entry of any mode is taken.",
  triple_witching_no_new_entries: "Past 12:30 ET on a triple-witching Friday -- no new entries the rest of the day.",
  outside_entry_window: "Outside every entry window this arm is configured to trade.",
  max_positions_reached: "This arm already holds its configured max_positions for the day.",
  max_positions_this_window_reached: "This entry window already used up its own share of the position budget.",
  forecast_range_unavailable: "The containment gate needs a trailing-range forecast that isn't measured yet.",
  forecast_range_exceeds_cap: "The forecast session range is wider than this arm's max_forecast_range_points.",
  trend_bucket_refused: "The session's trend-from-open bucket matches this arm's refuse_trend_bucket.",
  miss_stop: "An earlier spread today has sat uncompleted past miss_stop_minutes -- no new entry until it resolves.",
  entry_cadence_wait: "This arm's min_seconds_between_entries hasn't elapsed since its last fill.",
  duplicate_structure: "The same (centre, wing, far-wing) structure is already open today for this arm.",
  sign_rule_conflict: "The proposed legs would sell into a strike this arm already holds the opposite side of.",
  center_already_occupied: "This centre strike already has an open structure (superseded by duplicate_structure).",

  // strike / quote plumbing
  missing_leg_quotes: "One or more legs have no live quote to price the trade against.",
  legs_beyond_strike_window:
    "A leg sits further from spot than the strikes flies trades (strike_window_pct, 1.5% by default) — where the arm aimed, not a gap in the data.",
  implausible_debit_quote: "The completing debit quote fails a sanity check (e.g. negative or inverted).",
  implausible_fly_quote: "The fly's all-in quote fails a sanity check.",
  not_a_credit_spread: "This position isn't an open short vertical -- nothing to complete this way.",
  not_a_debit_vertical: "This position isn't an open long vertical -- nothing to complete this way.",
  not_a_bwb: "This position isn't a broken-wing butterfly -- the bwb-specific rule doesn't apply.",
  not_funded_by_realized_credit: "The fly buy isn't covered by credit already realized -- would add net risk.",
  far_width_not_wider_than_wing: "A bwb's far wing must be wider than its near wing; this proposal isn't.",

  // credit / debit floors and ceilings
  credit_below_floor: "The opening credit is below this arm's min_credit_pct_of_width.",
  credit_above_ceiling_mostly_intrinsic: "The credit is so rich the short leg is mostly in-the-money -- a directional bet, not a pin bet.",
  credit_cannot_clear_fees: "The credit can't cover the round-trip fee stack on both legs -- no risk-free fly possible.",
  bwb_credit_below_floor: "The bwb's opening credit is below its configured floor.",
  bwb_credit_above_ceiling_mostly_intrinsic: "The bwb's credit is so rich the short leg is mostly in-the-money.",
  bwb_tail_risk_above_max: "The bwb's uncapped tail risk exceeds this arm's max.",
  iron_credit_too_low: "The iron completion's net credit is below its required gate.",
  completing_credit_too_low: "The completing sale's credit doesn't clear the gate needed to fund it risk-free.",
  completing_debit_too_high: "The completing purchase's debit is above the gate -- would erase the floor after fees.",
  debit_below_floor_completion_implausible: "The completion debit is implausibly low given where spot sits.",
  debit_above_ceiling_mostly_intrinsic: "The completion debit is so rich the resulting spread is mostly in-the-money.",
  debit_cannot_be_out_earned: "The debit already paid can't be recovered by the credit this completion would realize.",
  floor_below_minimum_after_fees: "The position's worst-case floor, after every fee, is below the arm's minimum.",
  roll_debit_too_high: "The bwb roll's debit exceeds the gate -- rolling would cost more than it protects.",
  fly_debit_above_max: "The all-in fly purchase debit exceeds this arm's configured maximum.",

  // moneyness / drift
  entry_mostly_intrinsic: "At entry the short strike is already this far in the money -- a directional bet, not a pin bet.",
  completion_against_drift: "Completing here would need spot to reverse a session drift this arm has committed to.",

  // centring (select_center) -- no centre, no trade
  no_underlying_price: "No spot price in the snapshot, so no centre could be chosen.",
  gex_unavailable_for_call_wall: "The call-wall rule has no GEX wall to centre on; it never degrades to ATM.",
  call_wall_not_above_spot: "The GEX call wall sits at or below spot -- a short there is a directional bet, not a wall bet.",
  no_delta_quotes_beyond_spot: "The chain has no fresh delta beyond spot to place a delta-targeted centre.",
  no_strike_near_delta_target: "No strike sits within debit_delta_tolerance of the target delta -- refused rather than centred on the wrong probability.",

  // live loop only
  incomplete_spread_blocks: "The live pilot holds one incomplete position at a time; an open spread blocks every further entry.",
  completion_cutoff_reached: "Past completion_cutoff (15:30 ET) -- the resting completion order is cancelled and not re-placed.",

  // outcomes (accepted rows)
  ok: "Gate passed -- this leg or structure was opened.",
  entered: "Entry taken -- the opening leg or structure was filled.",
  placed: "The completion order was placed and is resting at the max safe debit.",
  completed: "The open credit spread was completed into a butterfly.",
  rolled: "The broken-wing butterfly's far wing was rolled in, converting it to a symmetric fly.",
  cutoff_cancelled: "The resting completion order was cancelled at completion_cutoff.",
};

export function gateDescription(reason: string): string | undefined {
  return GATE_GLOSSARY[reason];
}
