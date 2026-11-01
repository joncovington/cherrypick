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
  /** Whether the vendor's own chart page draws it (the two nearest of each side, no gaps); null on
   *  a chart file older than version 4. */
  vendorView: boolean | null;
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

/** One simulated long position of a setup; an open one has no exit yet. Prices are closes. */
export interface TechnicalsSetupTrade {
  entryDate: string;
  entryPrice: number | null;
  exitDate: string | null;
  exitPrice: number | null;
  /** What ended it, in the setup's own words ("21 EMA", "target", "stop", "supertrend"); null while open. */
  reason: string | null;
  /** The pullback's target (the prior 20-session high), where it set one. */
  target: number | null;
}

/** An entry/exit setup as `packages/technicals/setups.py` declares and walks it. */
export interface TechnicalsSetup {
  id: string;
  name: string;
  /** The long setup a short mirrors; a long is its own family. */
  family: string;
  side: "long" | "short";
  /** The rule in words, written by the package; the page shows it rather than restating it. */
  rule: string;
  /** Keys into `TechnicalsChart.setupLines`: the series the rule reads. */
  lines: string[];
  /** Every trade with an arrow in the bars drawn (entered there, exited there, or still open). */
  trades: TechnicalsSetupTrade[];
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
  /** Empty on a chart file older than version 3. */
  setups: TechnicalsSetup[];
  /** Our own support and resistance (swing points in our bars, nearest two per side); empty before version 5. */
  ourLevels: { kind: "support" | "resistance"; value: number; date: string | null }[];
  /** ema9, ema21, ema50, bb_upper, bb_mid, bb_lower, supertrend — aligned with `bars`. */
  setupLines: Record<string, (number | null)[]>;
  /** Null when the setups read the name's own volume; the stand-in's symbol when not (SPX: SPY). */
  volumeSource: string | null;
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

// --------------------------------------------------------------------------- Setups watchlist
// `packages/technicals`' setups-index.json: one row per setup position, open or recently traded,
// with the context it is read against. Built from the chart files, so it cannot disagree with them.

export interface TechnicalsLevelNear {
  value: number;
  /** Distance from the last close, in percent. */
  pct: number | null;
}

/** A level as the read states it: distance in percent, in ATR(14)s and in sigmas of the expected move. */
export interface SetupReadLevel {
  value: number;
  pct: number | null;
  atr: number | null;
  sigma: number | null;
}

/**
 * The read behind a row (`setup_read.py`), built nightly with the watchlist: why the setup fired,
 * what its rule earned in the historical study, the direction and the volatility, the levels, and
 * the kind of option trade the read points to. The console lays it out and decides nothing.
 */
export interface SetupRead {
  setup: { rule: string | null; change: string | null; medianHold: number | null };
  /** The historical study's result for this setup and side (R per ATR, net of costs, stock fills). */
  record: {
    entries: number | null;
    netR: number | null;
    edgeR: number | null;
    t: number | null;
    verdict: string;
    hit: number | null;
    medianHold: number | null;
  } | null;
  direction: {
    verdict: "agrees" | "conflicts" | "mixed" | "unread";
    /** Mean reversion fades the move by design: its trend is shown but does not vote. */
    counterTrend: boolean;
    trendAgrees: boolean | null;
    trendAgainst: boolean | null;
    board: { side: "bullish" | "bearish" | null; count: number | null; bullish: number | null; bearish: number | null; tags: string[] } | null;
  };
  volatility: {
    /** Implied vol in percent (the broker's 30-day IV index). */
    iv: number | null;
    ivRank: number | null;
    ivPct: number | null;
    hv30: number | null;
    ivHv: number | null;
    regime: "rich" | "cheap" | "middling" | "unread";
    cycle: { expiry: string; days: number } | null;
    expectedMove: { dollars: number; pct: number } | null;
    earnings: string | null;
    earningsInCycle: boolean | null;
    exDividend: string | null;
  };
  levels: { atr14: number | null; support: SetupReadLevel | null; resistance: SetupReadLevel | null; target: SetupReadLevel | null };
  lean: { structure: string | null; why: string; legs: string[]; cautions: string[]; expiry: string | null };
}

export interface TechnicalsWatchlistRow {
  symbol: string;
  /** The chart's own last session; behind the index's session when the name was not landed. */
  session: string;
  setup: string;
  setupName: string;
  family: string;
  side: "long" | "short";
  /**
   * The historical study's confirmed rule this row comes from (e.g. "mr-300m"), or null for a row
   * from the setup itself. A rule re-walks its setup with one change, so most of its trades are
   * also a setup row: a reader shows one kind or the other, never both.
   */
  tested: string | null;
  /** Both trend scores on the trade's side of zero; null until both are defined. */
  trendAgrees: boolean | null;
  entryDate: string;
  entryPrice: number | null;
  /** Sessions since entry; null when it was before the 250 drawn. */
  entryAgo: number | null;
  exitDate: string | null;
  exitPrice: number | null;
  exitAgo: number | null;
  reason: string | null;
  target: number | null;
  /** The 50-session median of close × volume to the session before the entry, in dollars. */
  dollarVolume: number | null;
  status: "open" | "closed";
  lastClose: number | null;
  /** Close to close in the trade's direction (a fall is positive for a short). Not P&L. */
  movePct: number | null;
  trend1m: number | null;
  trend6m: number | null;
  /** Our five-step label for each score (Bullish, Mildly Bullish, Neutral, Mildly Bearish, Bearish). */
  trend1mLabel: string | null;
  trend6mLabel: string | null;
  /** Our 1-10 rank, the vendor's "Relative Strength": a whole-market decile, not a comparison with SPY. */
  rs: number | null;
  /** The 21-session return less SPY's over the same sessions, in points. */
  vsSpy1m: number | null;
  /** The nearest of our own levels below and above the last close. */
  support: TechnicalsLevelNear | null;
  resistance: TechnicalsLevelNear | null;
  /** Weeklies and a tastytrade liquidity rating of 3+ on the label's day; null with no label. */
  optionsTradable: boolean | null;
  /** The read behind the row; null for an index written before the reads existed. */
  read: SetupRead | null;
}

export interface TechnicalsWatchlist {
  session: string | null;
  generatedAt: string | null;
  /** Sessions of entries and exits the file keeps beside every open position. */
  window: number | null;
  /** The day the options-tradable label was read from; null when there is none. */
  optionsLabelDay: string | null;
  rows: TechnicalsWatchlistRow[];
}
