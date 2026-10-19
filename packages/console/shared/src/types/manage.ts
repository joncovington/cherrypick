/**
 * The daily position-management evaluation (packages/manage), as the Live page's `manage` tab reads
 * it. Every verdict, level and confidence here is computed by the package; the console only shows
 * them. Money is whole-position dollars, signed cash flow, and every P&L is a mark P&L GROSS of fees.
 */

export type ManageAction =
  | "ROLL_FOR_CREDIT"
  | "CLOSE"
  | "TAKE_PROFIT"
  | "ROLL_OR_CLOSE"
  | "CLOSE_LEGS"
  | "REVIEW"
  | "SETTLES"
  | "HOLD";

export interface ManageFired {
  rule: string;
  action: ManageAction;
  confidence: number | null;
  reason: string;
  legs: string[] | null;
}

export interface ManageLeg {
  label: string;
  quantity: number;
  expiration: string | null;
  dte: number | null;
  openPrice: number | null;
  mid: number | null;
  /** Short leg: share of its premium given up. Long leg: its return. */
  capture: number | null;
  delta: number | null;
  iv: number | null;
}

export interface ManageOrder {
  legs: Array<{ side: string; quantity: number; leg: string; mid: number | null }>;
  /** Signed cash flow of the close: + received, - paid. */
  net: number | null;
  netPrice: number | null;
  netSide: "cr" | "db" | null;
}

/** One order in a trade's broker order chain: the opening, then every roll or adjustment. */
export interface ManageChainOrder {
  date: string;
  role: "open" | "roll" | "close" | "add" | string;
  /** Signed cash flow: + received. */
  net: number | null;
  netPrice: number | null;
  netSide: "cr" | "db" | null;
  fees: number | null;
  legs: string[];
}

export interface ManageLevel {
  /** The trade's whole-position value at which the rule fires. */
  closeValue: number | null;
  netPrice: number | null;
  netSide: "cr" | "db" | null;
  basis?: string | null;
}

export interface ManageTrade {
  tradeId: string;
  underlying: string;
  spot: number | null;
  /** `directional`: opened near even money (a super bear or bull, a risk reversal), judged on risk. */
  kind: "credit" | "debit" | "directional" | "risk-judged" | null;
  /** The trade's shape, named by packages/manage (super bear, jade lizard, diagonal, ...). */
  shape: string | null;
  intent: string | null;
  note: string | null;
  cost: number | null;
  value: number | null;
  pnlGross: number | null;
  /** Gross less the chain's recorded fees; null when the basis came from the open legs (no fee record). */
  pnlNet: number | null;
  fees: number | null;
  /** `order_chain`: the broker's chain set the basis. `open_legs`: no reconciled chain, open legs only. */
  basis: "order_chain" | "open_legs" | null;
  chain: { orders: ManageChainOrder[]; cash: number | null; fees: number | null; rolls: number } | null;
  /** Credit trade: share of the credit kept. Debit trade: return on the debit. */
  capture: number | null;
  nearestShortExpiry: string | null;
  nearestShortDte: number | null;
  action: ManageAction;
  confidence: number | null;
  rule: string | null;
  reason: string;
  /** The verdict in plain English, written by packages/manage from the same numbers. */
  explanation: string | null;
  agreeing: string[];
  conflicts: string[];
  /** For a credit roll: the close rules it is offered in place of. */
  replaces: string[];
  /** A proposed credit roll (naked short puts). The credit is MODELLED; confirm it on the live quote. */
  roll: { expiration: string; strike: number; credit: number | null; net: number | null; legs: string[] } | null;
  fired: ManageFired[];
  legs: ManageLeg[];
  /** What the trade was opened for: signed cash flow (a credit received is +) and the net price. */
  opening: { net: number | null; netPrice: number | null; netSide: "cr" | "db" | null } | null;
  order: ManageOrder | null;
  takeProfit: ManageLevel | null;
  stop: ManageLevel | null;
  timeExit: string | null;
  /** `days` is null when the evaluation did not count them: never a made-up zero. */
  earnings: { date: string; days: number | null; timeOfDay: string | null; beforeNearestShortExpiry: boolean } | null;
  exDividend: { date: string; days: number | null } | null;
  /** Modelled at the nearest expiry (Black-Scholes at each leg's own IV); never a tradable price. */
  profile: {
    horizon: string | null;
    maxLossFromOpen: number | null;
    downsideFromNow: number | null;
    upsideFromNow: number | null;
    unboundedLow: boolean;
  } | null;
  /** An earnings trade (packages/manage `earnings_trade`): put on to hold through a report. */
  earningsTrade: { report: string; timeOfDay: string | null; leadDays: number | null; passed: boolean } | null;
  /** Before the call, the rules that would have acted and are waiting for it. */
  heldForEarnings: ManageFired[];
}

/**
 * A band and the reading's place in it, as packages/manage stated them (`portfolio.bands`). The
 * console draws these and never works out a band or "inside" itself; `inside` is null where there
 * was no figure to place.
 */
export interface ManageBand {
  low: number | null;
  high: number | null;
  value: number | null;
  inside: boolean | null;
}

export interface ManageGuidance {
  rule: string;
  confidence: number | null;
  reason: string;
  /** What drives it, largest first: an underlying and its figure (`bwd`, `theta`, shares). */
  drivers: Array<{ underlying: string; value: number | null }>;
}

export interface ManagePortfolioUnderlying {
  underlying: string;
  beta: number | null;
  bwd: number | null;
  theta: number | null;
  vega30: number | null;
  bwdShare: number | null;
  thetaShare: number | null;
  betaDoubt: string | null;
}

export interface ManageHedgeLeg {
  label: string;
  quantity: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  delta: number | null;
}

export interface ManageHedgeSuggestion {
  kind: string;
  structure: string | null;
  error: string | null;
  units: number | null;
  legs: ManageHedgeLeg[];
  priceLabel: string | null;
  natural: number | null;
  cash: number | null;
  maxLoss: number | null;
  bwdChange: number | null;
  thetaChange: number | null;
  /** A debit hedge offered past the buying-power cap, which it is exempt from. */
  pastCap: boolean;
  after: {
    bwd: number | null;
    inBand: boolean | null;
    theta: number | null;
    thetaPctNetLiq: number | null;
    breakevenPct: number | null;
    buyingPowerUsedPct: number | null;
  } | null;
}

export interface ManageHedges {
  target: number | null;
  need: number | null;
  root: string | null;
  expiration: string | null;
  dte: number | null;
  error: string | null;
  suggestions: ManageHedgeSuggestion[];
}

/**
 * Beta-weighted delta and theta summed on our model and on the broker's own greeks, over the same legs
 * with the same betas (`portfolio.broker_check`, snapshot v9). A gap is ours less the broker's.
 */
export interface ManageBrokerCheck {
  legsCompared: number;
  /** Legs only one model priced, left out of both sums. */
  legsLeftOut: string[];
  bwdOurs: number | null;
  bwdBroker: number | null;
  bwdGap: number | null;
  thetaOurs: number | null;
  thetaBroker: number | null;
  thetaGap: number | null;
  underlyings: Array<{ underlying: string; legs: number; bwdGap: number | null; thetaGap: number | null }>;
}

/** The account read as one book (packages/manage `portfolio.py`). Every figure is modelled. */
export interface ManagePortfolio {
  benchmark: string | null;
  benchmarkSpot: number | null;
  complete: boolean;
  unmodelledLegs: string[];
  noBeta: string[];
  accountKind: { marginOrCash: string | null; typeName: string | null };
  netLiq: number | null;
  bwd: number | null;
  bwdDollarsPer1pct: number | null;
  theta: number | null;
  thetaPctNetLiq: number | null;
  vega30: number | null;
  breakeven: { kind: "short_gamma" | "long_gamma"; points: number | null; pct: number | null } | null;
  expectedMovePct: number | null;
  realisedMove: number | null;
  expectedMove: number | null;
  buyingPowerUsed: number | null;
  buyingPowerUsedPct: number | null;
  stress: Array<{ shock: number; pnl: number | null; pctNetLiq: number | null; leftOut: string[] }>;
  bands: {
    delta: ManageBand & { basis: string | null };
    theta: ManageBand;
    /** Buying power used against a cap set by VIX; null on an evaluation from before it existed. */
    buyingPower: (ManageBand & { vix: number | null }) | null;
  } | null;
  guidance: ManageGuidance[];
  underlyings: ManagePortfolioUnderlying[];
  hedges: ManageHedges | null;
  vix: number | null;
  scenarios: ManageScenarios | null;
  ladder: ManageLadder | null;
  candidates: ManageCandidates | null;
  /** Null on an evaluation from before snapshot v9, which carried no broker greeks. */
  brokerCheck: ManageBrokerCheck | null;
}

/** One sized income trade (packages/manage `candidates.py`). Every figure is the package's. */
export interface ManageCandidate {
  underlying: string;
  kind: string;
  /** Set on a narrowed version: the structure it was narrowed from. */
  alternativeTo: string | null;
  expiry: string | null;
  dte: number | null;
  legs: Array<{ strike: number; right: string; quantity: number; bid: number | null; ask: number | null; delta: number | null }>;
  units: number;
  /** The cap the next unit breaks; null when the unit count itself is the limit. */
  binding: string | null;
  perUnit: {
    credit: number | null;
    natural: number | null;
    cash: number | null;
    maxLoss: number | null;
    buyingPower: number | null;
    stressLoss: number | null;
    theta: number | null;
    bwd: number | null;
  };
  thetaPerBp: number | null;
  earningsDate: string | null;
  earningsUnknown: boolean;
  alternative: ManageCandidate | null;
}

export interface ManageCandidates {
  sourceSession: string | null;
  takenAt: string | null;
  accountBasis: string | null;
  names: number | null;
  excluded: Record<string, number>;
  fits: ManageCandidate[];
  fitsTotal: number;
  tooBig: ManageCandidate[];
  tooBigTotal: number;
  error: string | null;
}

/** The book repriced across benchmark moves (packages/manage `portfolio.scenarios`). */
export interface ManageScenarios {
  moves: number[];
  ivShiftWeighting: string | null;
  /** One row per IV shift (vol points as a fraction, 0.05 = five points), a P&L per move. */
  matrix: Array<{ ivShift: number; pnl: Array<number | null> }>;
  graph: { moves: number[]; curves: Array<{ horizonDays: number; pnl: Array<number | null> }> };
  leftOut: string[];
}

export interface ManageLadder {
  expiries: Array<{ expiration: string; trades: string[]; downside: number | null; downsideUnknown: number; theta: number | null }>;
  earnings: { trades: string[]; theta: number | null; thetaShare: number | null; downside: number | null };
}

export interface ManageAccount {
  account: string | null;
  netLiq: number | null;
  maintenance: number | null;
  buyingPower: number | null;
  riskBudget: number | null;
  pnlGross: number | null;
  counts: Partial<Record<ManageAction, number>>;
  trades: ManageTrade[];
  /** Legs the ruleset leaves out entirely (SPX 0DTE by default), per underlying. */
  excluded: Array<{ underlying: string; legs: number; reason: string }>;
  /** Null on an evaluation written before the portfolio reading existed (before 2026-10-18). */
  portfolio: ManagePortfolio | null;
}

export interface ManagePayload {
  /** The session shown, or null when no evaluation has been written yet. */
  session: string | null;
  sessions: string[];
  snapshotGeneratedAt: string | null;
  ok: boolean;
  error: string | null;
  metricsError: string | null;
  transactionsError: string | null;
  ruleset: string | null;
  rulesSource: string | null;
  ruleWarnings: string[];
  /** The effective ruleset the session was judged on, verbatim. */
  rules: Record<string, unknown> | null;
  accounts: ManageAccount[];
}
