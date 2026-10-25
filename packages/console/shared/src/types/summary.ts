import type { TradingMode } from "./status.js";

/**
 * A module's simplified-view summary (`GET /api/summary/:module`): the MEIC summary's pattern,
 * generalised as a MECHANISM. What fills it is each module's own — its reader picks its KPIs, its
 * gauges and its breakdowns in its own vocabulary (capture means nothing to a legged flies book,
 * cash deployed is contango's alone), so there is still no central summary page. MEIC keeps its
 * own richer slide (`MeicSummarySlice`).
 *
 * Money is the suite's one layout, computed server-side from the module's own history reader so
 * the summary and the module's history table cannot disagree: net = gross − every cost line, each
 * cost line labelled as the module records it (fees; settlement; slippage where it is CHARGED as
 * a cost). A cost a ledger never recorded is `null` (n/r), never zero.
 */

export type SummaryRange = "day" | "week" | "month" | "year" | "all";

/** A figure's unit, so the component formats it and the reader never sends a string. */
export type SummaryUnit = "usd" | "pct" | "count" | "ratio" | "price" | "days";

export interface SummaryCost {
  /** As the module names it: "fees", "settlement", "slippage (charged)". */
  label: string;
  /** Positive = a cost; null = not recorded (n/r). */
  amount: number | null;
  note?: string;
}

/**
 * The shape of the window's per-trade nets, for the cards' second faces: a total says how much, this
 * says how it was made (555 small wins and one large one read alike as a total). Null = no trade on
 * that side (no loss: no average loss, and no win ÷ loss ratio).
 */
export interface SummaryShape {
  median: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  /** The single worst trade's net (a loss when any lost). */
  worst: number | null;
  /*
   * How sure the window's figures are (2026-10-24; docs/trading-book-review-plan.md, B2): a figure
   * shown without its uncertainty lets a short sample read as proof. All optional, so a page served by
   * an older server still renders and simply shows no interval.
   */
  /** Settled trades the shape is read from. */
  n?: number;
  /** Net per trade (the mean), its sample standard deviation, and the 95% interval of the mean
   *  (Student's t below 30 trades). Null with too few trades to say. */
  mean?: number | null;
  sd?: number | null;
  ci95?: [number, number] | null;
  /** mean ÷ (sd ÷ √n). It assumes independent trades; positions open at once make it read high. */
  t?: number | null;
  /** The win rate's 95% interval (Wilson), as fractions. */
  winRateCi95?: [number, number] | null;
  /** The per-trade nets' skewness, and a flag past ±1: "left" = rare large losses carry the risk
   *  (premium selling's shape, the median above the mean); "right" = rare large wins carry the result.
   *  Null below 3 trades or with no spread. */
  skewness?: number | null;
  skew?: "left" | "right" | null;
  /** Results in R (docs/trading-book-review-plan.md B3): net over the trade's defined max loss at
   *  entry, averaged over the trades whose risk was recorded (`rN` of them). Null where the module
   *  has no defined risk (an undefined-risk structure has no R) or recorded none. */
  meanR?: number | null;
  rN?: number;
  rCi95?: [number, number] | null;
  /** B2's overlap flag: sessions that closed more than one trade, and the most in one. Trades that
   *  share a session share a tape, so the interval (which treats them as independent) reads narrower
   *  than it is. Counted by closing session only: a multi-day overlap is not seen here. */
  overlap?: { sessions: number; of: number; maxPerSession: number; heldTogether?: number | null } | null;
}

export interface SummaryHero {
  net: number;
  gross: number;
  /** Every cost subtracted from gross to reach net, once each. */
  costs: SummaryCost[];
  /** Settled (resolved) trades in the window, and how many of them netted positive. */
  trades: number;
  wins: number;
  sessions: number;
  /** Sessions whose summed net was positive / negative. */
  daysWon: number;
  daysLost: number;
  /** Positions still open in the window's scope (none of their money is in the figures above). */
  open: number;
  /** Optional premium line under the hero ("premium collected", "debit paid", "cash deployed"). */
  basis?: { label: string; amount: number } | null;
  /** Optional so a page served by an older server still renders (its second faces say n/r). */
  shape?: SummaryShape;
}

export interface SummaryTile {
  label: string;
  value: number | null;
  unit: SummaryUnit;
  /** One line under the value: the denominator, the sample, the caveat. */
  note?: string;
  /** Tone follows the value's sign only; set false for a figure whose sign is not good/bad. */
  signed?: boolean;
}

export interface SummaryGauge {
  title: string;
  /** The unclamped percentage for the label; null = n/r. */
  rawPct: number | null;
  /** The needle's domain, e.g. -50..50 for capture, -100..100 for return on risk. */
  min: number;
  max: number;
  caption: string;
  /**
   * The gauge's dollar face: the numerator and denominator its percentage divides, so a click shows
   * "$91.7k of $95.5k premium sold" rather than 96%. Absent where the gauge is not a money ratio
   * (flies' completion rate). `worst` rides on the return gauges: the capital at risk beside the
   * worst single trade against it.
   */
  dollars?: { amount: number; of: number | null; ofLabel: string; worst?: number | null } | null;
}

export interface SummaryBreakdownRow {
  bucket: string;
  trades: number;
  net: number;
  winPct: number | null;
  avgNet: number | null;
}

export interface SummaryBreakdown {
  title: string;
  /** The bucket column's heading ("entry hour", "strategy", "symbol"). */
  column: string;
  rows: SummaryBreakdownRow[];
}

export interface SummaryArmRow {
  arm: string;
  trades: number;
  net: number;
  winPct: number | null;
  /** True when the module's config retires this arm (the console's retired-arms rule). */
  retired?: boolean;
}

export interface ModuleSummarySlice {
  module: string;
  mode: TradingMode;
  /** The window the numbers answer for, as summaryWindow resolved it. */
  window: { from: string | null; to: string | null; label: string };
  /** The session the page is on, and its neighbours with rows, for the day arrows. */
  session: { day: string | null; prev: string | null; next: string | null };
  /** The module's one-line description of what a "trade" is here ("a legged fly", "a weekly double calendar"). */
  unit: string;
  hero: SummaryHero;
  tiles: SummaryTile[];
  gauges: SummaryGauge[];
  breakdowns: SummaryBreakdown[];
  arms: SummaryArmRow[];
  /** Plain-language caveats the module wants stated (e.g. "paper fills at mid; slippage charged"). */
  notes: string[];
}
