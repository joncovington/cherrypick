/** The earnings strategy verdict (`strategy_report --json`, `cherrypick.earnings.verdict`), as the
 *  console serves it. Field names are the module's own snake_case: this is its document, passed through. */

export interface VerdictCi {
  events: number;
  /** 90% interval of net per trade, dollars; null under two events. */
  expectancy: [number, number] | null;
  /** 90% interval of net ÷ capital at risk, percent; null under two events. */
  ror_pct: [number, number] | null;
}

export interface VerdictTercile {
  lo: number;
  hi: number;
  n: number;
  net: number;
  ror_pct: number | null;
}

export interface VerdictArm {
  arm: string;
  strategy: string;
  retired: string | null;
  started: string | null;
  n: number;
  wins: number;
  win_pct: number | null;
  gross: number;
  costs: number;
  net: number;
  capital_at_risk: number;
  ror_pct: number | null;
  expectancy: number | null;
  ci: VerdictCi;
  cost_drag_pct: number | null;
  by_exit_reason: Array<{ reason: string; n: number; net: number }>;
  concentration: { worst_events: Array<{ symbol: string; opened: string | null; net: number }>; net_ex_worst3: number };
  stops: { n: number; net: number; green_first: number; green_first_net: number };
  covariates: Record<string, { n: number; buckets: VerdictTercile[] }>;
  trades: Array<{
    order_id: string;
    symbol: string;
    opened: string | null;
    exit_reason: string | null;
    net: number;
    capital_at_risk: number | null;
    max_unrealized_pnl: number | null;
  }>;
  cumulative: Array<{ i: number; opened: string | null; net: number; cumulative: number }>;
}

export interface VerdictTwin {
  arm: string;
  control_arm: string;
  strategy: string;
  pairs: number;
  twin_net: number;
  control_net: number;
  diff: number;
  twin_better: number;
}

export interface Verdict {
  since: string | null;
  include_flagged: boolean;
  ci_level: number;
  bootstrap: { seed: number; draws: number; cluster: string };
  flag_rules: Array<{ key: string; opened_from: string; opened_to: string; note: string }>;
  excluded: Array<{ rule: string; n: number; net: number }>;
  flagged: Array<{ order_id: string; arm: string | null; rule: string; net: number }>;
  breaks: Array<{ date: string; key: string; note: string | null }>;
  arms: VerdictArm[];
  twins: VerdictTwin[];
}

export interface VerdictResult {
  ok: boolean;
  verdict: Verdict | null;
  error: string | null;
}

