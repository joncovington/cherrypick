// ------------------------------------------------------------------- bwb evidence (bridged)
// bwb's own reads, through `server/src/services/bwbEvidenceBridge.ts`: `run.py live-gap`,
// `entry-context`, `regime-split` and `iv-premium` (`cherrypick.bwb.analytics.live_gap` /
// `entry_context`, `entry_regime.split`, `iv_premium.run`). Field names stay the module's own
// snake_case, verbatim; nothing here is derived.

import type { BridgedResult } from "./bridged.js";

export interface BwbLiveGapSession {
  session: string;
  cancelled: number;
  live_position: string | null;
  paper_position: string | null;
  same_structure: boolean;
  live_status: string | null;
  entry_fill_status: string | null;
  live_credit: number | null;
  live_limit: number | null;
  live_mid_at_submit: number | null;
  paper_credit: number | null;
  credit_gap: number | null;
  addon: "both" | "live_only" | "paper_only" | "neither" | null;
  addon_match: boolean | null;
  live_addon_fired_at: string | null;
  paper_addon_fired_at: string | null;
  live_addon_credit: number | null;
  paper_addon_credit: number | null;
  fees_source: string | null;
  live_fees: number | null;
  modeled_fees: number | null;
  fee_variance: number | null;
  live_gross: number | null;
  modeled_gross: number | null;
  paper_fees: number | null;
  live_net: number | null;
  paper_net: number | null;
  net_gap: number | null;
}

export interface BwbLiveRefusal {
  attempts: number;
  sessions: number;
  first: string;
  last: string;
  /** The configured gate this refusal belongs to (`live.vix9d_vix_max`), or null. */
  gate: string | null;
}

export interface BwbLiveGap {
  ok: boolean;
  ledger: "ok" | "absent";
  arm: string | null;
  sessions: BwbLiveGapSession[];
  totals?: {
    sessions: number;
    closed_pairs: number;
    live_net: number | null;
    paper_net: number | null;
    net_gap: number | null;
    credit_gap: number | null;
    addon_matched: number;
    addon_judged: number;
    reconciled: number;
  };
  refusals: Record<string, BwbLiveRefusal>;
}

export interface BwbEntryContextRow {
  position_id: string;
  arm: string;
  entry_session: string;
  expiration: string;
  status: string;
  entry_vix9d: number | null;
  entry_vix9d_age: number | null;
  entry_vix: number | null;
  entry_vix_age: number | null;
  entry_vix3m: number | null;
  entry_vix3m_age: number | null;
  entry_regime_reason: string | null;
  vix9d_vix: number | null;
  vix_vix3m: number | null;
  gate_state: "open" | "shut" | "unmeasured";
  entry_iv_vol: number | null;
  /** 1 complete, 0 a lower bound (the cache's strike window cut a wing), null not taken. */
  entry_iv_complete: number | null;
  entry_iv_reason: string | null;
  net_pnl: number | null;
}

export interface BwbRegimeSplitCell {
  positions: number;
  net_pnl: number;
  fired: number;
  net_per_trade: number | null;
  first_session: string;
  last_session: string;
}

export interface BwbRegimeSplit {
  ok: boolean;
  ledger: "ok" | "absent";
  gate?: string;
  arms: Record<string, Partial<Record<"open" | "shut" | "unmeasured", BwbRegimeSplitCell>>>;
}

export interface BwbIvStats {
  windows: number;
  mean_implied_vol?: number;
  mean_realised_vol?: number;
  mean_premium_vol?: number;
  median_premium_vol?: number;
  implied_above_realised?: number;
}

export interface BwbIvWindow {
  entry_session: string;
  expiration: string;
  position_id: string;
  returns: number;
  max_gap_min: number | null;
  coverage: string | null;
  realised_vol: number | null;
  implied_vol: number | null;
  implied_complete: number | null;
  implied_reason: string | null;
  premium_vol: number | null;
}

export interface BwbIvPremium {
  ok: boolean;
  ledger: "ok" | "absent";
  sample_seconds?: number;
  summary?: {
    settled_windows: number;
    realised_measured: number;
    refused: Record<string, number>;
    with_implied: BwbIvStats;
    with_complete_implied: BwbIvStats;
  };
  windows?: BwbIvWindow[];
}

export interface BwbEntryContext {
  ok: boolean;
  ledger: "ok" | "absent";
  positions: BwbEntryContextRow[];
}

/** The bwb entry-vol page's three reads, each with its own envelope so one failing names itself. */
export interface BwbVolEvidence {
  entryContext: BridgedResult<BwbEntryContext>;
  regimeSplit: BridgedResult<BwbRegimeSplit>;
  ivPremium: BridgedResult<BwbIvPremium>;
}
