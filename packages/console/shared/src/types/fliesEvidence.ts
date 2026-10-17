// ------------------------------------------------------------------- flies evidence (bridged)
// Three reads the flies module computes and the console renders unchanged, through
// `server/src/services/fliesEvidenceBridge.ts`: `run.py live-gap`, `gate-sessions` and
// `selector-sessions` (`cherrypick.flies.analytics.live_paper_gap` / `gate_sessions` /
// `selector_sessions`). Field names stay the module's own snake_case, verbatim, as
// `ModulePerformanceGroup.reading` does: a hand mapping would be one more place to drift, and
// nothing here is derived.

export interface LiveGapSide {
  entries: number;
  completed: number;
  completion_rate: number | null;
  settled: number;
  open: number;
  /** Settled net; null until every row of the session has settled. */
  net: number | null;
  fees: number | null;
}

export interface LiveGapPair {
  live_position: string;
  paper_position: string;
  side: string;
  center: number;
  wing_width: number;
  gap_min: number;
  live_entry_time?: string | null;
  paper_entry_time?: string | null;
  live_credit: number | null;
  paper_credit: number | null;
  credit_gap: number | null;
  live_completed: boolean;
  paper_completed: boolean;
  live_debit: number | null;
  paper_debit: number | null;
  debit_gap: number | null;
  live_fees: number | null;
  paper_fees: number | null;
  fee_gap: number | null;
  live_fees_source: "broker" | "modelled";
  live_net: number | null;
  paper_net: number | null;
  net_gap: number | null;
  /** The largest dollar component of the gap: entry fill, completion fill, fees; `completion` when
   *  only one side completed; null while either side is unsettled. */
  category?: "entry fill" | "completion fill" | "fees" | "completion" | "none" | null;
}

export interface LiveGapSession {
  session: string;
  /** What paper's completion price meant that session: its modelled debit, or the live limit. */
  completion_rule: "modelled_debit" | "pays_limit";
  live: LiveGapSide;
  paper: LiveGapSide;
  net_gap: number | null;
  completion_gap: number | null;
  matched: number;
  unmatched_live: number;
  unmatched_paper: number;
  credit_gap_sum: number | null;
  debit_gap_sum: number | null;
  debit_pairs: number;
  fee_gap: number | null;
  matched_net_gap: number | null;
  broker_fees: number;
  pairs?: LiveGapPair[];
  /** Entries one book took and the other did not, each with the other side's reason. */
  unmatched?: LiveGapUnmatched[];
}

/**
 * One entry only one book took (`analytics._unmatched_row`). `other_side_reason` is the other book's
 * own reason -- live's refusal run covering that minute (or `entry_unfilled`: live's order on the
 * same structure never filled), paper's attempt outcome then -- and `category` its group: live
 * limit, timing, gate, data, fill, or other (the raw reason always beside it).
 */
export interface LiveGapUnmatched {
  which: "live only" | "paper only";
  position: string;
  entry_time: string | null;
  side: string;
  center: number;
  completed: boolean;
  net: number | null;
  other_side_reason: string;
  other_side_at: string | null;
  category: string;
}

/** Over an era: unmatched entries by "<which>: <category>" (with the net of the side that traded --
 *  for `paper only`, what each kind of live refusal cost or saved) and pairs by gap category. */
export interface LiveGapWhy {
  unmatched: Record<string, { entries: number; net: number | null; reasons: Record<string, number> }>;
  pairs: Record<string, { pairs: number; net_gap: number | null }>;
}

export interface LiveGapEra {
  sessions: number;
  net_gap: number | null;
  matched: number;
  credit_gap_sum: number | null;
  debit_gap_sum: number | null;
  debit_pairs: number;
  fee_gap: number | null;
  matched_net_gap: number | null;
  live_completion_rate: number | null;
  paper_completion_rate: number | null;
  why?: LiveGapWhy;
}

export interface FliesLiveGap {
  ok: boolean;
  ledger: "ok" | "absent";
  arm: string;
  match_window_minutes?: number;
  completion_rule_from?: string | null;
  sessions: LiveGapSession[];
  eras: Partial<Record<"modelled_debit" | "pays_limit", LiveGapEra>>;
}

export interface GateSessionRow {
  session: string;
  acted: boolean;
  refused_ticks: number;
  refusals: Record<string, number>;
  close_tags: number;
  restacks: number;
  arm_entries: number;
  control_entries: number;
  arm_net: number | null;
  control_net: number | null;
  diff: number | null;
  partial_session: boolean;
}

export interface GateBlock {
  sessions: number;
  scored: number;
  arm_net: number | null;
  control_net: number | null;
  diff: number | null;
  arm_ahead: number;
  arm_behind: number;
}

export interface GateArm {
  arm: string;
  base: string;
  window_start: string | null;
  window_break: { break_date: string; scope: string; kind: string; reason: string } | null;
  acts_by: string[];
  acted: GateBlock;
  quiet: GateBlock;
  all: GateBlock;
  sessions: GateSessionRow[];
}

export interface FliesGateSessions {
  ok: boolean;
  ledger: "ok" | "absent";
  as_of?: string;
  era_start?: string | null;
  era_break?: { break_date: string; kind: string; reason: string } | null;
  arms: GateArm[];
}

export interface SelectorCell {
  cell: string;
  source: string;
  sessions: number | null;
  entries: number | null;
  net_per_entry: number | null;
  interval: [number, number] | null;
  eligible: boolean;
  clear_above: boolean;
  clear_below: boolean;
}

export interface SelectorModelReadout {
  ok: boolean;
  reason: string;
  cells: SelectorCell[];
}

export interface SelectorLatestModel extends SelectorModelReadout {
  session: string | null;
  model_id: string | null;
  fitted_through: string | null;
  procedure_version: number | null;
  arm_starts: Record<string, string | null> | null;
}

export interface SelectorSessionRow {
  session: string;
  ticks: number;
  reasons: Record<string, number>;
  chosen: Record<string, number>;
  departing_ticks: number;
  departed: boolean;
  selected_from: Record<string, number>;
  selector_entries: number;
  control_entries: number;
  selector_net: number | null;
  control_net: number | null;
  diff: number | null;
  model_ids: string[];
  model: SelectorModelReadout;
}

export interface FliesSelectorSessions {
  ok: boolean;
  ledger: "ok" | "absent";
  arm?: string;
  base?: string;
  procedure_version?: number;
  features?: string[];
  latest_model?: SelectorLatestModel | null;
  sessions: SelectorSessionRow[];
  departing?: {
    sessions: number;
    first_read_at: number;
    due: boolean;
    ahead: number;
    behind: number;
    diff: number | null;
    first_read: { ahead: number; behind: number; sign_test_p: number | null } | null;
  };
}
