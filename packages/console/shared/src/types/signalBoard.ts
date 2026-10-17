/**
 * The signal board (2026-10-17): each listed stock's count of methods agreeing on one side, as
 * `python -m cherrypick.technicals board` writes it each morning (`technicals/board/<session>.json`).
 * The console reads the file and re-derives nothing.
 */

export type SignalMethod = "stage" | "rotation" | "fundamentals" | "scan" | "revision";

export interface SignalTag {
  method: SignalMethod;
  /** In the board's words: "Early breakout", "Money rotating into the sector", ... */
  label: string;
  /** What fired it: "XLK leading", "Barclays downgrade", the scan rule's name; empty for a stage. */
  detail: string;
}

export interface SignalBoardRow {
  symbol: string;
  sector: string | null;
  /** The side with more tags; null on a tie, which lists no tags. */
  side: "bullish" | "bearish" | null;
  /** That side's count of agreeing methods. */
  count: number;
  bullish: number;
  bearish: number;
  /** The side's tags only, as the board lists them. */
  tags: SignalTag[];
}

export interface SignalBoardMethod {
  label: string;
  /** True where our engine is known to differ from the board it reproduces (rotation, scan). */
  approximate: boolean;
}

export interface SignalBoardPayload {
  /** The session shown; null when no board has been written. */
  day: string | null;
  /** Every saved session, newest first. */
  days: string[];
  /** Stocks read. */
  names: number;
  methods: Partial<Record<SignalMethod, SignalBoardMethod>>;
  /** The board's methods we do not count yet. */
  notCounted: string[];
  /** When the analyst revisions were last fetched (UTC ISO); null when never. */
  revisionsThrough: string | null;
  /** The weekly estimate snapshot the fundamentals score is read on; null when none is held. */
  fundamentalsSnapshot: string | null;
  rows: SignalBoardRow[];
}
