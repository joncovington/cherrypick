/**
 * QuikOptions' Hot Options Report as the capture saved it (`scripts/fetch_quikoptions.py`,
 * `docs/quikoptions-plan.md`): the site's six tables for one session, plus what the capture derived
 * from them. Every derived field is the capture's, so the console shows it and never re-derives it.
 *
 * Numbers are the site's own (contracts, trades, premium in whole-position dollars), null when a
 * cell did not read. `shown` keeps the text the site printed where a number was rounded for
 * display (`421.8K`), so a reader can see the precision the figure really has.
 */

/** The site's own call on a trade, from its side dot's tooltip: never ours. */
export interface FlowSide {
  /** `Bullish` / `Bearish` / `Neutral`, as the site words it. */
  sentiment: string;
  /** Where the fill sat: `On Ask`, `On Bid`, `Mid Market`, ... */
  fill: string;
  edge: number | null;
}

export interface FlowBirdseyeRow {
  symbol: string;
  name: string | null;
  /** Trade counts by trade size (`1s` … `=>1K`), as the site groups them. */
  buckets: Record<string, number | null>;
  /** The capture's four bands over those buckets: `1`, `2-10`, `11-99`, `100+`. */
  bands: Record<string, number | null>;
  calls: number | null;
  puts: number | null;
  total: number | null;
  callShare: number | null;
  /** The text each count was printed as (`421.8K`): the site rounds for display. */
  shown: Record<string, string>;
}

/** One single-leg trade: an outright or a sweep. */
export interface FlowTrade {
  symbol: string;
  name: string | null;
  /** `HH:MM:SS.mmm` ET; outrights only (the sweeps table prints no time). */
  timeEt: string | null;
  size: number | null;
  expires: string | null;
  strike: number | null;
  cp: "call" | "put" | null;
  price: number | null;
  /** Whole-position dollars. On outrights the capture derives it (size × price × 100). */
  premium: number | null;
  premiumDerived: boolean;
  side: FlowSide | null;
}

export interface FlowSpread {
  symbol: string;
  name: string | null;
  timeEt: string | null;
  size: number | null;
  expires: string | null;
  /** The site's structure code, e.g. `CS` (call spread), `CSCAL` (call spread calendar). */
  type: string | null;
  cp: "call" | "put" | null;
  /** The site's leg description, e.g. `261009 11/11.5 CS`. */
  spread: string | null;
  /** The site's net price, with its sign. */
  price: number | null;
  delta: number | null;
  /** Whole-position dollars, with the site's sign. */
  premium: number | null;
  /** The capture's reading of the site's signs: `bought`, `sold`, or null when they disagree. */
  direction: "bought" | "sold" | null;
  /** Rows printed together (same symbol, time and size, e.g. a roll) share a group; null alone. */
  group: string | null;
  underlying: { last: number | null; bid: number | null; ask: number | null } | null;
  exchange: string | null;
}

export interface FlowVolOi {
  symbol: string;
  name: string | null;
  volume: number | null;
  oi: number | null;
  vOi: number | null;
  expires: string | null;
  strike: number | null;
  cp: "call" | "put" | null;
}

/** A name that appears in more than one of the six tables. */
export interface FlowName {
  symbol: string;
  name: string | null;
  /** The tables it is in: `birdseye`, `outrights`, `sweeps`, `spreads`, `voloi`, `openings`. */
  tables: string[];
}

export interface OptionsFlowDay {
  session: string;
  /** When the capture saved it (UTC ISO). */
  savedAt: string | null;
  birdseye: FlowBirdseyeRow[];
  outrights: FlowTrade[];
  sweeps: FlowTrade[];
  spreads: FlowSpread[];
  voloi: FlowVolOi[];
  openings: FlowVolOi[];
  names: FlowName[];
  /** Outright and sweep premium summed by the site's own sentiment, and how many trades each. */
  premiumBySide: Record<string, number>;
  tradesBySide: Record<string, number>;
  /** The day's largest outright, sweep or spread by size of premium; the capture's pick. */
  largestTrade: { table: string; symbol: string | null; premium: number } | null;
}

export interface OptionsFlowPayload {
  /** Every captured session, oldest first. */
  sessions: string[];
  current: OptionsFlowDay | null;
  /** Why there is nothing to show, when there is nothing: never captured, or a capture unreadable. */
  degraded?: { reason: string };
}

/** The flow names an OCC session shares with the Morning report's hot-options card. */
export interface MorningFlowMarks {
  session: string;
  /** Symbol → the flow tables it appears in, for every name in any of the six tables. */
  tables: Record<string, string[]>;
}
