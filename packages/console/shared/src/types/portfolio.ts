// ------------------------------------------------------------------- the portfolio reading (shared)
// Compare's portfolio tab (`GET /api/portfolio`): how a chosen set of arms -- one per module --
// behaves held together, from `python -m cherrypick.core.portfolio` (packages/core/docs/module-map.md
// Phase 5). Field names are core's own, snake_case, passed through.

import type { TradingMode } from "./status.js";

/** A backtest arm's key: `bt:<source id>#<arm>`, the source id being its path under the backtest data folder. */
export const BACKTEST_ARM_PREFIX = "bt:";

/** `<arm> (backtest)` for a backtest arm's key, the key itself for a paper or live arm. */
export function portfolioArmLabel(key: string): string {
  if (!key.startsWith(BACKTEST_ARM_PREFIX)) return key;
  const cut = key.lastIndexOf("#");
  return cut > 0 ? `${key.slice(cut + 1)} (backtest)` : key;
}

/** One backtest result Compare's portfolio tab can hold (`read portfolio-sources`), its file path withheld. */
export interface PortfolioSource {
  /** Its path under the backtest data folder, without `.json`. */
  id: string;
  kind: "builder" | "study";
  label: string;
  arms: string[];
  /** The sessions the run covered, [first, last]. */
  window: [string, string] | null;
  /** Derived from licensed vendor data. */
  licensed: boolean;
  generated_at: string | null;
}

export interface PortfolioSources {
  ok: boolean;
  error?: string | null;
  sources: PortfolioSource[];
  unreadable: string[];
}

export interface PortfolioArm {
  /** `<module>:<group tag>`, or `bt:<source id>#<arm>` for a backtest arm. */
  key: string;
  periods_traded: number;
  /** Its first close. */
  first: string | null;
  /** Where its span starts: its first close, or a backtest's first session. Before it the arm contributes nothing. */
  span_from?: string | null;
  periods_in_span: number;
  net: number;
  max_drawdown: number;
  sd: number | null;
  /** The factor its nets were multiplied by under equal-risk weighting (1 when booked). */
  scale: number | null;
}

export interface PortfolioPair {
  a: string;
  b: string;
  /** Every period inside both spans, a period an arm closed nothing counted as zero. */
  zero_filled: number | null;
  zero_filled_periods: number;
  /** Only periods both arms closed something. */
  similarity: number | null;
  similarity_periods: number;
  thin: boolean;
}

/** Each session's peak of the summed max loss open at once, over the sessions anything was open. */
export interface PortfolioOpenRisk {
  sessions: number;
  peaks: { median: number; p95: number; max: number };
  account: number | null;
  of_account: { median: number; p95: number; max: number } | null;
  worst_sessions: Array<{ session: string; peak: number; by_arm: Record<string, number> }>;
  /** Trades per arm counted. */
  measured: Record<string, number>;
  /** Trades per arm with no recorded max loss: left out, so the sums are a floor. */
  unmeasured: Record<string, number>;
  /** Trades per arm whose source records no time, held for their whole sessions. */
  whole_session: Record<string, number>;
  floor: boolean;
}

export interface PortfolioReading {
  ok: boolean;
  error?: string | null;
  mode?: TradingMode;
  period: "day" | "week";
  weights: "equal_risk" | "booked";
  window: { start: string; end: string; periods: number; from: string | null; combined_periods: number };
  /** The window every chosen arm spans: where effective arms is measured. */
  shared: { from: string | null; periods: number; thin: boolean; arms?: string[]; too_new?: string[] };
  min_periods: number;
  arms: PortfolioArm[];
  /** Chosen arms with no closes in the window: named, never zero-filled. */
  no_closes: string[];
  correlation: PortfolioPair[];
  effective_arms: number | null;
  drawdown: {
    combined: number;
    sum_of_arms: number;
    benefit: number | null;
    by_arm: Record<string, number>;
    episode: { from: string | null; to: string } | null;
  };
  worst_periods: Array<{ period: string; net: number; arms_lost: number; arms_traded: number; by_arm: Record<string, number> }>;
  contribution: Array<{ key: string; net: number; share: number | null; in_worst_drawdown: number | null }>;
  combined_net: number;
  /** Max loss held at once across the arms (core `portfolio.openrisk`). */
  open_risk?: PortfolioOpenRisk;
  /** Where each arm was read from: a paper/live ledger or a backtest result. */
  sources?: Record<string, { kind: "ledger" | "backtest"; licensed: boolean; window: [string, string] | null }>;
  /** Any arm read was derived from licensed vendor data. */
  licensed?: boolean;
  failures: Array<{ key: string | null; error: string }>;
}
