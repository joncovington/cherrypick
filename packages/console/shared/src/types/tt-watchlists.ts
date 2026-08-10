/** Read-only mirrors of tastytrade watchlists shown as tabs on the watchlist page. */

export interface TtWatchlistTab {
  key: string; // "tt:<name>" | "public:<name>"
  kind: "user" | "public";
  name: string;
  count: number;
  fetchedAt: string; // ISO
  stale: boolean;
}

export interface TtWatchlistIndex {
  tabs: TtWatchlistTab[];
  available: string[]; // public allowlist for the picker
  pins: string[];
  credential: boolean;
}

export interface TtWatchlistRow {
  symbol: string;
  last: number | null;
  bid: number | null;
  ask: number | null;
  eodClose: number | null;
  eodChangePct: number | null;
  candleFresh: boolean;
  ivRank: number | null; // 0..100 for display
  ivIndex: number | null; // 0..100 (%) for display
  volume: number | null; // last daily bar's volume
  marketCap: number | null;
  yearHigh: number | null; // 252-bar high
  yearLow: number | null; // 252-bar low
}

export interface TtWatchlistPayload {
  tab: TtWatchlistTab;
  rows: TtWatchlistRow[];
  skipped: string[]; // symbols dropped by validation, reported not hidden
  live: boolean; // small list — client may subscribe live quotes
}

/** The builder's symbol summary card: quote + EOD context + metrics. */
export interface SymbolCardPayload {
  symbol: string;
  last: number | null;
  eodClose: number | null;
  eodChangePct: number | null;
  yearHigh: number | null;
  yearLow: number | null;
  volume: number | null;
  ivRank: number | null; // 0..100
  ivIndex: number | null; // 0..100 (%)
  liquidity: number | null; // 0..4
  pe: number | null;
  divYield: number | null; // 0..100 (%)
  marketCap: number | null;
  earningsDate: string | null;
  trend1m: string | null;
  trend6m: string | null;
}

export interface CandleWarmResult {
  requested: number;
  warmed: number;
  skippedFresh: number;
  failed: string[];
  tookMs: number;
}
