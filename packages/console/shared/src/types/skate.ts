/**
 * The skate screen (2026-10-19; docs/skate-plan.md step 3): short calls above a name's recent range
 * and short puts below it, quoted in regular hours by `fetch_screener_greeks.py skate` and scored by
 * `cherrypick.technicals.skate` (`score`, `best_expiries`) into
 * `market-report/screener-greeks/skate/<session>/<HHMM>.json`. The console reads that file and
 * re-derives nothing: every rate, gap, yield, pass/fail and best expiry is the file's.
 */

/** The two range bands: a fixed 0.75 haircut, and the by-expiry schedule. */
export type SkateBand = "fixed" | "byExpiry";

export type SkateSide = "C" | "P";

export interface SkateQuote {
  /** Per share, as quoted. */
  bid: number | null;
  ask: number | null;
  volume: number | null;
  openInterest: number | null;
}

/** One quoted strike judged against one band (`skate.score_leg`). */
export interface SkateLeg {
  expiry: string;
  /** Calendar days left when quoted. */
  days: number;
  /** `pick` is the band's own strike; `closer` and `further` its listed neighbours. */
  role: string;
  /** The OCC symbol. */
  option: string;
  /** Both bands land on the same price at this expiry. */
  sameBand: boolean;
  strike: number;
  /** The strike's distance past the band, as a fraction (negative = inside it). */
  offset: number | null;
  /** The offset lies past the history grid, so the rate is the grid's end value. */
  outsideGrid: boolean;
  quote: SkateQuote;
  /** Per share. */
  mid: number | null;
  iv: number | null;
  delta: number | null;
  /** The model's chance of finishing out of the money, 1 - N(d2) for a call, 1 - N(-d2) for a put. */
  impliedSkate: number | null;
  /** Share of past days on which the band, at this offset, held to expiry: 11 years, and the last 3. */
  skate: number | null;
  skateRecent: number | null;
  /** History minus implied, in rate units (0.05 = five points). */
  gap: number | null;
  /** Skate yield, annualised simple: premium ÷ capital × 365 ÷ days, on the bid and on the mid. */
  yieldBid: number | null;
  yieldMid: number | null;
  /** Dollars per contract per day, on the bid. */
  perDayBid: number | null;
  testedPerCycle: number | null;
  testedPerMonth: number | null;
  /** The hard filters (open interest, the spread): a leg that fails is never shown. */
  hard: boolean;
  /** The soft thresholds, a marker only. */
  soft: { skate: boolean; skateRecent: boolean; yield: boolean; all: boolean };
}

/** `skate.best_expiries` for one band: the hard-passing pick paying the most a day on the bid. */
export interface SkateBest {
  expiry: string;
  strike: number;
  perDayBid: number | null;
  skate: number | null;
  monthly: string | null;
  /** Its premium a day over the monthly pick's; null when the monthly pick does not pass. */
  vsMonthly: number | null;
}

export interface SkateBandRead {
  best: SkateBest | null;
  /** The band's pick leg at the best expiry, as the file holds it; null when there is no best. */
  pick: SkateLeg | null;
}

/** One name-side. */
export interface SkateRow {
  symbol: string;
  side: SkateSide;
  spot: number | null;
  fund: boolean;
  /** The next announcement; null for a fund or none known. */
  earnings: string | null;
  divYield: number | null;
  /** 1: one expiry quoted (the cycle monthly, or the last before a report); 2: every weekly to 63 days. */
  stage: number | null;
  monthly: string | null;
  bands: Record<SkateBand, SkateBandRead>;
}

export interface SkateConfig {
  haircut: number | null;
  /** [days or fewer, haircut] steps; beyond the last, `haircut`. */
  byExpiry: [number, number][];
  hard: { minOpenInterest: number | null; maxSpreadPct: number | null; maxSpreadAbs: number | null };
  soft: { skate: number | null; skateRecent: number | null; yield: number | null };
  stageTwo: number | null;
  monthDays: number | null;
}

export interface SkatePayload {
  /**
   * `ok`: a run was read. `none`: no run written for the session. `unreadable`: the newest run's file
   * would not parse. Never an empty table standing in for "no trades".
   */
  status: "ok" | "none" | "unreadable";
  /** The session shown; null when no run has been written. */
  day: string | null;
  /** Every session with a run, newest first. */
  days: string[];
  /** The run file shown (`1505`), the newest of the session's; trial runs are never read. */
  run: string | null;
  /** Every run of the session, newest first. */
  runs: string[];
  /** UTC ISO. */
  measuredAt: string | null;
  /** The close the bands were built on. */
  bandDay: string | null;
  /** False when the run stopped part-way. */
  complete: boolean;
  /** The run's own statement of what went wrong, verbatim. */
  problem: string | null;
  config: SkateConfig | null;
  rows: SkateRow[];
}

/**
 * The skate screen's forward record (docs/skate-plan.md step 4): `technicals.skate_record` keeps each
 * session's shown rows, settles them at expiry and writes its reading as
 * `data/technicals/skate/record.json`. The console passes that reading through and decides nothing:
 * the counts, rates, z scores, `decision_grade` and each band's `passes` are the file's.
 */

/** One group of settled rows against its own implied odds. */
export interface SkateRecordGroup {
  n: number | null;
  /** Rows that skated, tested ones included, as the skate rate counts them. */
  skated: number | null;
  rate: number | null;
  /** The group's mean implied skate rate. */
  implied: number | null;
  /** Skates over the summed implied odds, in Poisson-binomial standard deviations. */
  z: number | null;
}

export interface SkateRecordBand {
  /** Rows with history minus implied above the test's high gap. */
  highGap: SkateRecordGroup | null;
  /** Rows with history minus implied below the test's low gap. */
  belowZero: SkateRecordGroup | null;
  /** Two-proportion z of the high-gap rate over the below-zero rate. */
  zHighOverLow: number | null;
  /** The file's verdict: null until decision-grade, and null when the file states none. */
  passes: boolean | null;
  /** Settled rows that skated but touched the strike on the way. */
  tested: number | null;
}

/** The declared test's thresholds, as the file states them. */
export interface SkateRecordTest {
  /** Rate units: 0.05 is five points. */
  gapHigh: number | null;
  gapLow: number | null;
  minExpiries: number | null;
  zOneSided: number | null;
}

export interface SkateRecordPayload {
  /**
   * `ok`: the reading was read. `none`: no `record.json` written yet. `unreadable`: the file would
   * not parse, or holds no verdict. Never zeros standing in for a missing record.
   */
  status: "ok" | "none" | "unreadable";
  /** The file's modification time, UTC ISO. */
  writtenAt: string | null;
  /** Every row recorded so far, settled or not. */
  recorded: number | null;
  /** Rows added by the last run, per session. */
  added: Record<string, number>;
  /** What the last run settled. */
  settledNow: { skated: number | null; tested: number | null; through: number | null; waiting: number | null } | null;
  /** Settled rows the test reads. */
  settled: number | null;
  /** Distinct settled expiry dates. */
  expiries: number | null;
  /** The file's own flag; false when it is absent. */
  decisionGrade: boolean;
  bands: Record<SkateBand, SkateRecordBand | null>;
  test: SkateRecordTest | null;
}
