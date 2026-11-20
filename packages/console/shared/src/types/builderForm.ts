/** One key's type, from the module (`fieldtypes`): which input the form draws and what it refuses. A
 *  `default` is what the engine reads when the key is absent; the form shows it and never writes it. */
export interface BuilderFieldType {
  type: "int" | "number" | "time" | "bool" | "enum" | "leg" | "json" | "text";
  min?: number;
  max?: number;
  /** An exclusive lower bound. */
  above?: number;
  /** Words an int also takes ("session"). */
  or?: string[];
  options?: string[];
  default?: unknown;
  /** A signal parameter the kind needs (core's `KINDS`); its optional ones and the rules say false. */
  required?: boolean;
}

/** Every typed key, grouped as the spec holds them. */
export interface BuilderTypes {
  expiry: Record<string, BuilderFieldType>;
  entry: Record<string, Record<string, BuilderFieldType>>;
  trigger: Record<string, BuilderFieldType>;
  signals: { kinds: Record<string, Record<string, BuilderFieldType>>; rules: Record<string, BuilderFieldType> };
  strike: Record<string, BuilderFieldType>;
  gates: Record<string, Record<string, BuilderFieldType>>;
  exit: Record<string, BuilderFieldType>;
  size: Record<string, BuilderFieldType>;
  limits: Record<string, BuilderFieldType>;
}

/** The Builder form's choices, from the engine's own tables (`cli builder-schema`). {key: required}. */
export interface BuilderSchema {
  /** Each key's type; absent from an older module, where every field is typed text. */
  types?: BuilderTypes;
  blocks: Record<string, string[]>;
  entry: Record<string, Record<string, boolean>>;
  orb_directions: string[];
  trigger_forms: Record<string, Record<string, boolean>>;
  /** Each core signal kind's parameter names. Their types are core's validators: the check says. */
  signals: Record<string, string[]>;
  signal_rules: string[];
  weekdays: number[];
  event_labels: string[];
  /** Each event label's name, when it falls in words, and its next day (null where the calendar does
   *  not say: a symbol's own dates, or past what a data label covers). Absent from an older module. */
  event_about?: Record<string, { name: string; when: string; next: string | null }>;
  expiry: Record<string, Record<string, boolean>>;
  /** The far expiry's rules (#1318): every expiry rule and `after_near`; absent from an older module. */
  far_expiry?: Record<string, Record<string, boolean>>;
  /** Which expiry a leg may sit on: near, far. */
  leg_expiries?: string[];
  /** The expiry keys that are true or false, drawn as checkboxes. */
  expiry_flags?: string[];
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
  /** The credit-trade exits, in the engine's own keys. */
  credit_rules: Record<string, number>;
  /** The strategy selector's choices (`strategies.schema`); absent from an older module. */
  strategy?: BuilderStrategySchema;
  /** The entry presets and filters (`entrypresets.schema`); absent from an older module. */
  entry_presets?: BuilderEntrySchema;
  /** The indicators a signal's `a` or `b` can be, each with its keys; absent from an older module. */
  sources?: Record<string, string[]>;
  /** How each choice reads as a sentence (`words.schema`); absent from an older module. */
  words?: BuilderWords;
}

/** One choice in words: its name, list heading and line, with a `{key}` slot where each control sits
 *  (`{range}` a filter's min and max together, `{symbol}` the underlying in a strategy's line). */
export interface BuilderChoiceWords {
  label?: string;
  group?: string;
  about?: string;
  sentence: string;
  /** The keys the sentence leaves out, each with its own phrase, under "More options". */
  more?: Record<string, string>;
  /** How a value reads where its own spelling would not: an enum's options, a flag's "true" and
   *  "false", a nullable key's "null" and "n". */
  values?: Record<string, Record<string, string>>;
  /** What the form writes for a required key that "More options" shows. */
  start?: Record<string, unknown>;
}

export interface BuilderWords {
  entry: Record<string, BuilderChoiceWords>;
  trigger: Record<string, BuilderChoiceWords>;
  signal_rules: Record<string, BuilderChoiceWords>;
  signals: Record<string, BuilderChoiceWords>;
  sources: Record<string, BuilderChoiceWords>;
  gates: Record<string, BuilderChoiceWords>;
  /** How `{range}` reads with both bounds, only `min` or only `max`. */
  range: { both: string; min: string; max: string };
  /** When a filter is read, by its stage: the tag beside its line. */
  stages?: Record<string, string>;
  /** The exit groups given a row each, in order; the rest sit under "More options". */
  exit_rows?: string[];
  /** The exits a trade can start from: the credit-trade rules, or none (held to expiry). */
  exit_starts?: Record<string, BuilderChoiceWords>;
  exits: Record<string, BuilderChoiceWords>;
  /** The nested keys of an exit that is an object ({exit: {key: required}}). */
  exit_parts: Record<string, Record<string, boolean>>;
  strikes: Record<string, BuilderChoiceWords>;
  /** How a strike's `match` reads, for a rule aimed at a price and one aimed at the option's value. */
  matches: { price: Record<string, string>; value: Record<string, string> };
  expiry: Record<string, BuilderChoiceWords>;
  sizes: Record<string, BuilderChoiceWords>;
  progression: BuilderChoiceWords;
  limits: Record<string, BuilderChoiceWords>;
  strategies: Record<string, BuilderChoiceWords>;
  strategy_values: Record<string, Record<string, string>>;
  /** A strategy's settings its expiry line shows rather than its own sentence. */
  strategy_expiry_settings: string[];
  strategy_groups: string[];
}

/** One setting an entry preset or a filter takes, from the module. */
export interface BuilderEntrySetting {
  type: "enum" | "int" | "number" | "time";
  options?: string[];
  default: string | number;
  min?: number;
  max?: number;
  label: string;
}

/** One entry preset or filter: its words, the settings it takes and its own defaults. */
export interface BuilderEntryPreset {
  label: string;
  group: string;
  about: string;
  suits: string;
  settings: string[];
  defaults: Record<string, string | number>;
  /** The strategies it suits: each a strategy and any settings it must have (`fits`); absent from an
   *  older module. */
  fits?: Array<{ strategy: string } & Record<string, string>>;
}

export interface BuilderEntrySchema {
  entries: Record<string, BuilderEntryPreset>;
  filters: Record<string, BuilderEntryPreset>;
  settings: Record<string, BuilderEntrySetting>;
}

/** `POST /api/automations/builder/entry`: the preset's entry and the stacked filters' gates, in
 *  order; or what it could not build from, in `problems`. */
export interface BuilderEntryResult {
  preset: string;
  settings?: Record<string, string | number>;
  entry?: Record<string, unknown>;
  filters?: Array<{ filter: string; settings: Record<string, string | number>; gates: Array<Record<string, unknown>> }>;
  gates?: Array<Record<string, unknown>>;
  problems: string[];
  error?: string;
}

/** One setting a strategy takes: its type, default and bounds, from the module. */
export interface BuilderStrategySetting {
  type: "enum" | "int" | "number";
  options?: string[];
  default: string | number;
  min?: number;
  max?: number;
  above?: number;
}

export interface BuilderStrategySchema {
  strategies: Record<string, { label: string; about: string; settings: string[]; available: boolean; why: string | null; defaults?: Record<string, string | number> }>;
  settings: Record<string, BuilderStrategySetting>;
  /** A sensible target for each way of placing the first strikes (delta 0.16, pct 2, sd 1, points 10). */
  target_defaults: Record<string, number>;
}

/** `POST /api/automations/builder/strategy`: the strategy's legs and expiry, and a whole automation
 *  from it for the wizard; or the setting it could not build from, in `problems`. */
export interface BuilderStrategyResult {
  strategy: string;
  settings?: Record<string, string | number>;
  legs?: Array<Record<string, unknown>>;
  expiry?: Record<string, unknown>;
  /** A calendar's or a diagonal's second expiry rule. */
  far_expiry?: Record<string, unknown>;
  credit?: boolean;
  spec?: Record<string, unknown>;
  problems: string[];
  error?: string;
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

/** One side of one strike in the chain example: the quote the structure was placed on. */
export interface BuilderChainQuote {
  bid: number | null;
  ask: number | null;
  delta: number | null;
}

/** `POST /api/automations/builder/payoff`: the structure the module's rules would take, its prices
 *  per share and its P&L at expiry for one contract (whole-position dollars, at the mid, before fees
 *  and slippage), placed on live quotes or the last session our own recorder holds. */
/** The generic shape, from the rule alone (`sketch.sketch`): each leg's position (spot 0) and label,
 *  the payoff at those positions in nominal units (never dollars), the ticks to label, and whether the
 *  spacing is to scale (`exact`: every leg placed in points). */
export interface BuilderSketch {
  legs: Array<{ name: string; right: string; side: number; at: number; label: string; exact: boolean; expiry?: "far" }>;
  curve: Array<[number, number]>;
  ticks: Array<{ at: number; label: string }>;
  exact: boolean;
  top: string | null;
  bottom: string | null;
  /** A far leg drawn at the near expiry with its time value by a model: the peak is the model's. */
  modeled?: boolean;
}

/** A payoff with a far leg: its peak at the near expiry by the model, and each far leg's entry vol. */
export interface BuilderModeled {
  peak: number;
  legs: Array<{ name: string; iv: number | null; days: number }>;
}

export interface BuilderPayoff {
  name: string;
  symbol: string;
  state: "placed" | "not_placed" | "invalid" | "no_ledger";
  /** Always with a resolved automation, placed or not. */
  sketch?: BuilderSketch;
  /** Whether the rule enters for a credit or a debit (`core.structures.entry_kind`), or null when it
   *  does not say; absent from an older module. */
  entry_kind?: "credit" | "debit" | null;
  source?: { kind: "live" | "recorded"; at: string; source?: string };
  spot?: number;
  /** The listed strike nearest the spot: the chain example's 0. */
  atm?: number;
  expiry?: string;
  legs?: Array<{ name: string; right: string; side: number; strike: number; offset: number; expiry?: "far" }>;
  /** The far legs' expiry, with `modeled`, when the structure has one. */
  far_expiry?: string;
  modeled?: BuilderModeled;
  prices?: Record<string, number>;
  max_profit?: number | null;
  max_loss?: number | null;
  /** [settle, P&L] at each end and each strike between: the payoff is straight between them. */
  curve?: Array<[number, number]>;
  breakevens?: number[];
  /** The listed strikes near the legs and the spot; `gap` where strikes between were left out. */
  chain?: Array<{ strike: number; offset: number; put: BuilderChainQuote | null; call: BuilderChainQuote | null; gap: boolean }>;
  /** Why it could not be placed on live quotes, and on the recorded session. */
  live?: string;
  recorded?: string;
  problems?: string[];
  reason?: string;
  error?: string;
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
