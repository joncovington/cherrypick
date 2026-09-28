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
  /** Whether our grid can produce it; null for gap levels, which the grid is not asked about. */
  onOurGrid: boolean | null;
}

export interface TechnicalsVendorChart {
  /** The edition date the capture was filed under; the vendor's levels are as of `through`. */
  capture: string;
  fetchedAt: string | null;
  through: string | null;
  levels: TechnicalsVendorLevel[];
  rank: number | null;
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
