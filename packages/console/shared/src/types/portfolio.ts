// ------------------------------------------------------------------- the portfolio reading (shared)
// Compare's portfolio tab (`GET /api/portfolio`): how a chosen set of arms -- one per module --
// behaves held together, from `python -m cherrypick.core.portfolio` (docs/strategy-analysis-plan.md
// Phase 5). Field names are core's own, snake_case, passed through.

import type { TradingMode } from "./status.js";

export interface PortfolioArm {
  /** `<module>:<group tag>`. */
  key: string;
  periods_traded: number;
  /** Its first close: the arm did not exist before it and contributes nothing. */
  first: string | null;
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
  failures: Array<{ key: string | null; error: string }>;
}
