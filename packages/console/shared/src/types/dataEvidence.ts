// ------------------------------------------------------------------- System › data (bridged)
// Two reads the console asks of `cherrypick.core` rather than of any table, through
// `server/src/services/dataEvidenceBridge.ts`: the gex recorder's quote-history coverage
// (`python -m cherrypick.core.quotehistory coverage`, which reads only through `session()`) and the
// implied-variance check against the published indices (`python -m cherrypick.core.impliedvar
// check`). Field names stay the module's own snake_case.

import type { BridgedResult } from "./bridged.js";

export interface QuoteHistoryGap {
  from: string;
  to: string;
  seconds: number;
}

export interface QuoteHistorySession {
  session: string;
  recorded: boolean;
  samples: number;
  first: string | null;
  last: string | null;
  gaps: QuoteHistoryGap[];
  max_gap_s: number | null;
  no_spot: number;
  no_spot_reasons: Record<string, number>;
  strikes_median: number | null;
}

export interface QuoteHistoryCoverage {
  ok: boolean;
  db: string;
  exists: boolean;
  recorded_from: string;
  since: string;
  until: string;
  gap_seconds?: number;
  symbols: Record<string, { sessions: QuoteHistorySession[]; recorded: number; missing: string[] }>;
}

/** One `impliedvar check` reading. `ok: false` carries `reason` (`not_bracketed`, `no_index_print`,
 * `term_unavailable`, ...); `ok: true` carries `computed`, `diff` and a `verdict` of `agrees`,
 * `disagrees` or `truncated` (a cut-off strip reads low by construction, so it is no verdict). */
export interface ImpliedVarCheck {
  ok: boolean;
  index: string;
  days: number;
  as_of: number;
  published: number | null;
  published_at: number | null;
  reason?: string;
  offered?: Array<[string, number]>;
  computed?: number;
  diff?: number;
  verdict?: "agrees" | "disagrees" | "truncated";
  near?: { expiration?: string; complete?: boolean; quoted?: number; missing?: number };
  next?: { expiration?: string; complete?: boolean; quoted?: number; missing?: number };
}

export interface ImpliedVarEvidence {
  checks: Record<string, BridgedResult<ImpliedVarCheck>>;
}
