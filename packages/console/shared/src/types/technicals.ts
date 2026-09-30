// --------------------------------------------------------------------------- Per-name chart
// `packages/technicals`' chart file (data/technicals/charts/<SYMBOL>.json): our bars and readings,
// and — where the vendor's chart was captured — the vendor's levels beside them. Record-only.

export interface TechnicalsChartBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

/** The 250-session grid: every level the vendor draws is its high, its low, or low + k × step. */
export interface TechnicalsGrid {
  low: number;
  high: number;
  step: number;
  lowDate: string | null;
  highDate: string | null;
  window: number | null;
}

export interface TechnicalsVendorLevel {
  /** support | resistance | gapSupport | gapResistance */
  kind: string;
  value: number;
  date: string | null;
  /** Whether we can produce it: support/resistance on our grid, gap levels as one of our gap edges. */
  onOurGrid: boolean | null;
}

export interface TechnicalsVendorChart {
  /** The edition date the capture was filed under; the vendor's levels are as of `through`. */
  capture: string;
  fetchedAt: string | null;
  through: string | null;
  levels: TechnicalsVendorLevel[];
  rank: number | null;
  /** The vendor's overall label as captured: Bullish | Neutral | Bearish. */
  sentiment: string | null;
  /** The vendor's IV rank as captured, 0-100. */
  ivRank: number | null;
  trendShort: { date: string; value: number }[];
  trendLong: { date: string; value: number }[];
  barsCompared: number | null;
  barsAgree: number | null;
}

export interface TechnicalsChart {
  symbol: string;
  session: string;
  generatedAt: string | null;
  bars: TechnicalsChartBar[];
  grid: TechnicalsGrid | null;
  /** Aligned with `bars`; null until the indicator has enough history. */
  cci14: (number | null)[];
  cci5: (number | null)[];
  rsi14: (number | null)[];
  trendShort: (number | null)[];
  trendLong: (number | null)[];
  /** Our 1-10 rank on the session: a decile of the whole market; null before the landing stored cut-offs. */
  rank: number | null;
  /** Our overall label on the last session (close vs SMA 50 and WMA 200): Bullish | Neutral | Bearish. */
  sentiment: string | null;
  /** IV rank on or before the session, 0-100: ours from Dolt's IV, or tastytrade's where Dolt has none. */
  ivRank: { date: string; value: number; source: string } | null;
  signals: { date: string; rules: string[] }[];
  vendor: TechnicalsVendorChart | null;
}

export interface TechnicalsChartIndex {
  session: string | null;
  symbols: { symbol: string; vendor: boolean }[];
}

export interface TechnicalsChartPayload {
  index: TechnicalsChartIndex;
  /** Null when the symbol has no chart file, or none was asked for. */
  chart: TechnicalsChart | null;
}
