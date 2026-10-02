// --------------------------------------------------------------------------- Morning report
// Shapes mirror `packages/overview`'s fact pack (data/overview/morning-<session>.json). The console
// renders that artifact and derives nothing from it — the phase, the gate verdicts and the
// strongest/weakest sectors are all precomputed by the pack's writer, and this page displays them.
//
// Null is never zero anywhere in here: an unmeasured reading renders as an em dash, because a VIX
// that was not captured and a VIX of 0 are different facts.

/** One market reading on the scorecard — value plus the provenance the page must show beside it. */
export interface MorningReading {
  value: number | null;
  /** "live" = captured pre-open this session; "prior" = the previous session's close stood in. */
  basis: string | null;
  /** The session the value actually belongs to — matters most when basis is "prior". */
  session: string | null;
  asOf: string | null;
  source: string | null;
  label: string | null;
  priorClose: number | null;
  priorChangePct: number | null;
}

export interface MorningLevels {
  symbol: string | null;
  referencePrice: number | null;
  referenceBasis: string | null;
  zeroGamma: number | null;
  callWall: number | null;
  putWall: number | null;
  netGex: number | null;
  session: string | null;
  asOf: string | null;
  source: string | null;
}

export interface MorningSectorRow {
  symbol: string;
  sector: string | null;
  changePct: number | null;
  close: number | null;
  session: string | null;
}

export interface MorningSectors {
  board: MorningSectorRow[];
  /** Precomputed by the pack — never re-derived from the board here. */
  strongest: MorningSectorRow | null;
  weakest: MorningSectorRow | null;
  measured: number | null;
}

export interface MorningGate {
  id: string;
  label: string;
  /** met | not_met | unknown — anything unfamiliar is read as unknown, never as met. */
  status: string;
  value: number | null;
  threshold: number | null;
  detail: string | null;
}

export interface MorningPhase {
  /** green | yellow | red, as the pack computed it. */
  phase: string;
  reason: string | null;
  gatesTotal: number | null;
  gatesMeasured: number | null;
  gatesMet: number | null;
}

/** One macro signal feeding the deployment score, already scored 0–100 by the pack. */
export interface MorningSignal {
  id: string;
  label: string;
  /** measured | unknown — anything unfamiliar reads as unknown, never as measured. */
  status: string;
  /** The signal's own 0–100 contribution, or null when it could not be measured. */
  score: number | null;
  /** The raw quantity behind the score (a ratio, a z, a percentage) — units vary by signal. */
  value: number | null;
  /** Declared blend weight as a fraction, e.g. 0.25. */
  weight: number | null;
  detail: string | null;
}

/**
 * The deployment score block. **Record-only** — it gates nothing, sizes nothing, and the page must
 * never present it as an instruction. The session phase beside it is the operative verdict, and the
 * two are free to disagree.
 */
export interface MorningDeployment {
  /** 0–100, or null when too few signals were measured to blend one honestly. */
  score: number | null;
  /** full | reduced | defensive, as the pack computed it; null when there is no score. */
  zone: string | null;
  signals: MorningSignal[];
  signalsMeasured: number | null;
  signalsTotal: number | null;
  /** True when the blend renormalized its weights over fewer than all signals. */
  weightsRenormalized: boolean | null;
  /** Signals declared but not yet built — shown so an absent input is visible, not invisible. */
  deferred: string[];
  /** Why there is no score, when there is none. */
  reason: string | null;
  /** The pack's own statement that this governs nothing. Rendered, never paraphrased. */
  note: string | null;
}

/** One point on the vol term structure. `dte` is nominal and is what the slopes are quoted against. */
export interface MorningVolCurvePoint {
  point: string;
  symbol: string;
  dte: number | null;
  value: number | null;
  basis: string | null;
}

/**
 * Where one reading sits in its own trailing range. `percentile` is null whenever the pack refused
 * it, and `reason` says which refusal it was: `reading_unmeasured` (the feed served no value) or
 * `too_few_closes` (it did, and there is not enough history to place it). Different facts, and the
 * page must not collapse them — `samples` is rendered so a thin history is visible rather than
 * merely absent.
 */
export interface MorningVolPercentile {
  value: number | null;
  samples: number | null;
  percentile: number | null;
  reason: string | null;
  /** Where the value came from: `stream_cache`, `cboe_file`, `cboe_file_prior_close` (fact v4). */
  source: string | null;
}

export interface MorningVolSeasonality {
  month: number | null;
  /** Mean VIX close for this calendar month across every year on file. */
  norm: number | null;
  /** How many distinct years fed the norm. Refused below three — one August is not a norm. */
  years: number | null;
  reason: string | null;
  vixVsNormPct: number | null;
}

/**
 * The vol term structure. **Record-only** — it feeds no gate and governs nothing, and the page must
 * never present it as an instruction.
 *
 * `shape` is NOT the slope sign. It is VIX/VIX3M against the same `contango_max` the curve module
 * gates on, so the console and that module cannot tell a reader two different stories about whether
 * today is contango.
 */
export interface MorningVolRegime {
  curve: MorningVolCurvePoint[];
  /** Front = event pricing (9D vs 30D); mid = the classic read; back = the structural carry. */
  slope: {
    front9d30dPct: number | null;
    mid30d3mPct: number | null;
    back9d1yPct: number | null;
  };
  vixVix3mRatio: number | null;
  /** contango | backwardation, as the pack computed it; null when the ratio was unmeasurable. */
  shape: string | null;
  shapeReason: string | null;
  /** Keyed by reading id (vix9d, vix, vix3m, vix6m, vix1y, vvix, skew). */
  percentiles: Record<string, MorningVolPercentile>;
  seasonality: MorningVolSeasonality | null;
  /** 30-day 25-delta SPX risk reversal from the prior session's chain (fact v4); null when absent. */
  riskReversal: MorningRiskReversal | null;
  measuredPoints: number | null;
  totalPoints: number | null;
  recordOnly: boolean | null;
}

/**
 * Call 25-delta IV minus put 25-delta IV, interpolated to a 30-day tenor between the two bracketing
 * expirations. Negative is the normal put skew; the sign is the pack's, not recomputed here.
 */
export interface MorningRiskReversal {
  session: string | null;
  spot: number | null;
  targetDte: number | null;
  call25dIvPct: number | null;
  put25dIvPct: number | null;
  rrVolPts: number | null;
  expirations: { expiration: string; dte: number | null }[];
  source: string | null;
}

/** One scheduled economic release. `timeEt` is null where the source publishes no time (FRED). */
export interface MorningRelease {
  name: string;
  date: string;
  timeEt: string | null;
  source: string | null;
  today: boolean | null;
}

/** One announcement with the move its post-event straddle implies (0.85 x the ATM straddle). */
export interface MorningEarningsRow {
  symbol: string;
  date: string;
  /** "Before market open" | "After market close" | null — the calendar's own words. */
  when: string | null;
  expiration: string | null;
  spot: number | null;
  strike: number | null;
  straddle: number | null;
  expectedMove: number | null;
  expectedMovePct: number | null;
  /** Why the move is missing; the row is listed, never dropped. */
  reason: string | null;
}

export interface MorningEarningsWeek {
  rows: MorningEarningsRow[];
  /** When the straddles were priced — the prior session's close, not this morning. */
  asOf: string | null;
  reason: string | null;
}

export interface MorningCalendar {
  isFomcDay: boolean | null;
  nextFomc: string | null;
  fomcYearKnown: boolean | null;
  isTripleWitching: boolean | null;
  isQuarterlyExpiry: boolean | null;
  nextTradingDay: string | null;
  /** Absent before fact version 4 — null, so the page omits the table rather than saying "none". */
  releases: MorningRelease[] | null;
  earnings: MorningEarningsWeek | null;
}

/**
 * A futures leg: a reading plus its settle. The change is measured against a SETTLE only — a
 * change against the last trade of an overnight book read 0.00% all weekend — so it is null with
 * `changeReason` whenever there is no live print to compare.
 */
export interface MorningFuture extends MorningReading {
  product: string | null;
  priorSettle: number | null;
  priorSettleSession: string | null;
  changeVsPriorClosePct: number | null;
  changeReason: string | null;
}

export interface MorningPremarket {
  futures: Record<string, MorningFuture>;
  /** NDX, DJX, IWM (a proxy for the Russell, labelled as one) at their last print. */
  indexes: Record<string, MorningReading>;
  measuredFutures: number | null;
  recordOnly: boolean | null;
}

export interface MorningMoves {
  /** VIX / sqrt(52): the week's one-sigma move implied by the 30-day vol. */
  weeklyExpectedMove: {
    pct: number | null;
    points: number | null;
    vix: number | null;
    basis: string | null;
    reason: string | null;
  };
  realizedVol: { pct: number | null; sessions: number | null; through: string | null; reason: string | null };
  vixMinusRealized: number | null;
}

/** The Treasury par curve at the prior session. Yields in percent; changes and spreads in bp. */
export interface MorningYields {
  session: string | null;
  source: string | null;
  yields: Record<string, number | null>;
  changeBp: Record<string, number | null>;
  spread2s10sBp: number | null;
  spread3m10yBp: number | null;
  reason: string | null;
}

/** One underlying in the hot-options ranking. Contracts are OCC's sides halved (each trade is two). */
export interface MorningHotOptionsRow {
  symbol: string;
  /** Overall rank by contracts across every underlying; null when it did not trade. */
  rank: number | null;
  contracts: number | null;
  calls: number | null;
  puts: number | null;
  putCall: number | null;
  /** Customer share of OCC's sides — not of contracts: a customer-to-customer trade is two sides. */
  customerSidePct: number | null;
  avgContracts: number | null;
  relativeVolume: number | null;
}

/** OCC's cleared option volume for the newest session before the pack, ranked the way the Hot
 *  Options Report ranks it: the five index products, then single-name equities and funds. */
export interface MorningHotOptions {
  session: string | null;
  /** Sessions this file is behind the pack's prior session (OCC publishes the next day). */
  lagSessions: number | null;
  listingsAsOf: string | null;
  totalContracts: number | null;
  totalPutCall: number | null;
  underlyings: number | null;
  baselineSessions: number | null;
  indexes: MorningHotOptionsRow[];
  /** null when no stock/fund directory was on file — unranked, never guessed. */
  equities: MorningHotOptionsRow[] | null;
  funds: MorningHotOptionsRow[] | null;
  unclassified: string[];
  classification: string | null;
  reason: string | null;
}

export interface MorningPack {
  session: string;
  factVersion: number | null;
  generatedAt: string | null;
  /** Keyed by the pack's own reading ids (spx, vix, vix3m, …) — unfamiliar keys pass through. */
  readings: Record<string, MorningReading>;
  levels: MorningLevels | null;
  sectors: MorningSectors | null;
  gates: MorningGate[];
  phase: MorningPhase | null;
  /** Absent on packs written before fact version 2 — the page renders nothing rather than zeros. */
  deployment: MorningDeployment | null;
  /** Absent before fact version 3; null so the page omits the panel rather than drawing an empty curve. */
  volRegime: MorningVolRegime | null;
  calendar: MorningCalendar | null;
  /** Absent before fact version 4, like the two below. */
  premarket: MorningPremarket | null;
  moves: MorningMoves | null;
  yields: MorningYields | null;
  /** Absent before fact version 5. */
  hotOptions: MorningHotOptions | null;
}

// --------------------------------------------------------------------------- Technicals report
// Shapes mirror `packages/technicals`' report artifact (data/technicals/report-<session>.json):
// stages, breadth, rotation, scan-rule signals and leaders, all computed by that package's engines.
// Record-only, like everything on this page — it feeds no gate, no sizing and no order.

export interface TechnicalsBreadthDay {
  session: string;
  leaders: number | null;
  laggards: number | null;
  net: number | null;
  /** leaders / (leaders + laggards); null when neither side has a member. */
  bullishShare: number | null;
}

export interface TechnicalsStageMember {
  symbol: string;
  /** early | building | confirmed */
  stage: string | null;
}

export interface TechnicalsSectorStages {
  sector: string;
  net: number | null;
  leaders: TechnicalsStageMember[];
  laggards: TechnicalsStageMember[];
}

export interface TechnicalsLeader {
  symbol: string;
  sector: string | null;
  /** The 1–10 decile of the 125-session return. */
  rank: number | null;
  return6mPct: number | null;
  trendShort: string | null;
  trendLong: string | null;
  /** "leader/confirmed" and the like; null when the name has no stage today. */
  stage: string | null;
}

/** One of the session's largest single-stock moves. */
export interface TechnicalsMover {
  symbol: string;
  sector: string | null;
  changePct: number | null;
  close: number | null;
  /** The session's volume over the name's own prior 50-session average. */
  volumeRatio: number | null;
  stage: string | null;
}

export interface TechnicalsReport {
  session: string;
  reportVersion: number | null;
  generatedAt: string | null;
  universe: number | null;
  rules: Record<string, string>;
  breadth: TechnicalsBreadthDay[];
  stages: TechnicalsSectorStages[];
  /** leading | improving | weakening | lagging | none -> fund symbols. */
  rotation: Record<string, string[]>;
  /** Scan-rule name -> the symbols it matched on the session. */
  signals: Record<string, string[]>;
  leaders: TechnicalsLeader[];
  /** Report version 2 and on; empty lists on an older report, never invented. */
  movers: { gainers: TechnicalsMover[]; losers: TechnicalsMover[] };
}

export interface MorningPayload {
  sessions: string[];
  current: MorningPack | null;
  /** The AI-written narrative, if one has been written. Interpretation — never mixed into the facts. */
  note: string | null;
  /**
   * The technicals report for the last session BEFORE the pack's — what the morning could know.
   * Null when none is on file; a report dated the pack's own day was written after that open.
   */
  technicals: TechnicalsReport | null;
}
