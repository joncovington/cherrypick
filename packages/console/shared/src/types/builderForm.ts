/** The Builder form's choices, from the engine's own tables (`cli builder-schema`). {key: required}. */
export interface BuilderSchema {
  blocks: Record<string, string[]>;
  entry: Record<string, Record<string, boolean>>;
  orb_directions: string[];
  trigger_forms: Record<string, Record<string, boolean>>;
  /** Each core signal kind's parameter names. Their types are core's validators: the check says. */
  signals: Record<string, string[]>;
  signal_rules: string[];
  weekdays: number[];
  event_labels: string[];
  expiry: Record<string, Record<string, boolean>>;
  strike_rules: Record<string, { keys: Record<string, boolean>; matches: string[]; default_match: string | null }>;
  gates: Array<{
    gate: string;
    stage: string;
    runs: string;
    params: Array<{ name: string; required: boolean; default: unknown }>;
  }>;
  exits: string[];
  limits: string[];
  sizes: Record<string, string[]>;
  progression: string[];
}

/** `GET /api/automations/builder/schema`. */
export interface BuilderSchemaResult {
  schema: BuilderSchema | null;
  error: string | null;
}

/** One check the engine read deciding an automation now. */
export interface BuilderTodayCheck {
  check: string;
  passed: boolean;
  value: unknown;
}

/** `POST /api/automations/builder/today`: the engine's decision now, as paper would make it. */
export interface BuilderToday {
  name: string;
  state: "would_enter" | "would_skip" | "no_snapshot" | "no_symbol" | "no_ledger" | "invalid" | "not_decided";
  at?: string;
  window?: [string, string];
  in_window?: boolean;
  reason?: string | null;
  detail?: unknown;
  problems?: string[];
  spot?: number | null;
  checks?: BuilderTodayCheck[];
  entry?: {
    expiry: string;
    quantity: number;
    legs: Array<{ name: string; right: string; side: number; strike: number }>;
    prices: Record<string, number>;
    max_loss: number;
    max_profit: number | null;
  };
  error?: string;
}
