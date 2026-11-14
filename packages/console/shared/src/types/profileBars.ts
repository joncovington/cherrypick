import type { CandleBar } from "./ws-protocol.js";

/** The symbols `scripts/fetch_profile_bars.py` records, in the order the Profile tab offers them. */
export const PROFILE_SYMBOLS = ["SPX", "SPY", "NDX", "QQQ", "/ES", "/NQ"] as const;
export type ProfileSymbol = (typeof PROFILE_SYMBOLS)[number];

/**
 * The recorded regular-session bars of one symbol's newest sessions, for the Profile tab.
 *
 * The store holds 1-minute bars; the reader sends them as 5-minute bars (open of the first, highest
 * high, lowest low, close of the last, volume summed). Five minutes divides the profile's 30-minute
 * period, so every period's rows are the same as from the minutes, and a month of sessions is a few
 * thousand bars rather than eight thousand. The profiles themselves are built in the browser by
 * `lib/marketProfile` (the mirror of `cherrypick.core.marketprofile`).
 */
export interface ProfileBarsPayload {
  symbol: ProfileSymbol;
  /** False when the store is missing or holds nothing for the symbol; `reason` says which. */
  available: boolean;
  reason: string | null;
  /** The sessions sent, oldest first (ET dates). */
  sessions: string[];
  /** The width of each bar in `bars`, seconds. */
  barSeconds: number;
  /** Regular session only (09:30-16:00 ET), oldest first; `t` is the bar's start in epoch seconds. */
  bars: CandleBar[];
  /** The contract each session's bars came from (a future's month; a cash symbol is itself). */
  contracts: Record<string, string>;
  /** The newest session the store holds, and when the recorder last wrote. */
  newestSession: string | null;
  fetchedAt: string | null;
}
