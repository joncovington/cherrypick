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
  /** The wheel rules' notes, one per wheel cycle holding this trade's legs (`trade.wheel`); null on a
   *  trade no cycle holds, and on an evaluation written before the wheel rules (2026-10-20). */
  wheel: ManageWheelNote[] | null;
  /** The PMCC rules' summary of the PMCC whose short this trade holds (`trade.pmcc`), or the error
   *  that stopped them; null on any other trade and on an evaluation written before the PMCC view. */
  pmcc: ManagePmccNote | null;
}

/* ------------------------------------------------------------------ the PMCC view (pmcc.py) */

/** What the PMCC rules read, as the trade holding the short carries it. */
export interface ManagePmccNote {
  underlying: string | null;
  long: string | null;
  short: string | null;
  recognized: boolean | null;
  recognizedBy: string | null;
  reconciled: boolean | null;
  widthPasses: boolean | null;
  pnlIfAssigned: number | null;
  assignmentExposed: boolean | null;
  rollLong: string[];
  error: string | null;
}

/** The long call standing in for the shares. Prices per share. */
export interface ManagePmccLong {
  symbol: string;
  leg: string;
  strike: number | null;
  expiration: string | null;
  dte: number | null;
  quantity: number | null;
  mid: number | null;
  opened: string | null;
  daysAtEntry: number | null;
  deltaAtEntry: number | null;
  /** Why the delta at entry is unknown, when it is. */
  deltaAtEntryWhy: string | null;
  entryFills: Array<{ date: string | null; price: number | null; close: number | null; divYield: number | null; iv: number | null; delta: number | null }>;
  delta: number | null;
  intrinsic: number | null;
  extrinsic: number | null;
  /** Extrinsic as a fraction of the long's mid. */
  extrinsicShare: number | null;
  /** Why manage says roll the long (days left, delta floor); empty when it does not. */
  roll: string[];
  rollDeltaUnknown: boolean | null;
}

/** The short call written against the long. Prices per share. */
export interface ManagePmccShort {
  symbol: string;
  leg: string;
  strike: number | null;
  expiration: string | null;
  dte: number | null;
  quantity: number | null;
  mid: number | null;
  openPrice: number | null;
  tested: boolean | null;
  intrinsic: number | null;
  extrinsic: number | null;
  /** N(d2): the chance of finishing in the money. */
  probItm: number | null;
  band: ManageWheelBand | null;
  bandMissing: string | null;
  beyondBand: { fixed: boolean | null; byExpiry: boolean | null } | null;
  assignment: {
    nearZero: boolean | null;
    dividend: boolean | null;
    exposed: boolean | null;
    why: string | null;
    exDividendDate: string | null;
    dividendAmount: number | null;
  };
  earnings: { date: string | null; inside: boolean | null; why: string | null };
}

/** The strategy's width check, as manage judged it. Money is whole-position dollars. */
export interface ManagePmccWidth {
  /** Short strike less long strike, per share. */
  width: number | null;
  netCost: number | null;
  netCostPerShare: number | null;
  /** "linked orders", or "open_legs" when the orders do not reconcile. */
  basis: string | null;
  passes: boolean | null;
  pnlIfAssigned: number | null;
  pnlIfAssignedNet: number | null;
  assumes: string | null;
}

/** One call sold against the long, with its cash (signed, + received) and how it ended. */
export interface ManagePmccShortSold {
  symbol: string;
  leg: string;
  sold: string | null;
  soldPrice: number | null;
  cash: number | null;
  fees: number | null;
  status: string | null;
}

/** One roll of the short, priced on a quoted expiry and judged by check-roll. Prices per share. */
export interface ManagePmccRoll {
  expiration: string | null;
  open: ManageWheelQuote | null;
  delta: number | null;
  closePrice: number | null;
  /** Per share, signed: + a net credit. */
  net: number | null;
  legs: string[];
  check: ManageWheelRollCheck | null;
  checkError: string | null;
}

/** One name whose calls have a PMCC's shape, recognised or not (`account.pmcc.positions[]`). */
export interface ManagePmccPosition {
  underlying: string;
  recognized: boolean | null;
  recognizedBy: string | null;
  intent: string | null;
  reconciled: boolean | null;
  problems: string[];
  spot: number | null;
  long: ManagePmccLong;
  short: ManagePmccShort;
  width: ManagePmccWidth;
  shorts: ManagePmccShortSold[];
  orders: string[];
  netCash: number | null;
  fees: number | null;
  longCost: number | null;
  premiums: number | null;
  skateYield: { value: number | null; capped: boolean | null; basis: string | null } | null;
  /** Priced only for a recognised, reconciled PMCC whose width fails or whose short is exposed. */
  rolls: { candidates: ManagePmccRoll[]; why: string | null; quotedExpiries: string[]; quotedAt: string | null } | null;
  tradeIds: string[];
}

/** The account's PMCC view (`account.pmcc`). */
export interface ManagePmcc {
  positions: ManagePmccPosition[];
  underlyingHistoryError: string | null;
  bandsMissing: string | null;
  /** Why the account has no view (not the designated account, no history, the view failed). */
  error: string | null;
}

/* ------------------------------------------------------------------ the wheel view (wheel.py) */

/** One quoted strike on the roll's later expiry, per share. */
export interface ManageWheelQuote {
  strike: number | null;
  bid: number | null;
  ask: number | null;
  mid: number | null;
}

/** check-roll's verdict on one wheel roll (packages/manage `rollcheck.check`). */
export interface ManageWheelRollCheck {
  verdict: string | null;
  /** Whole-position dollars, signed: + received. */
  credit: number | null;
  maxLossBefore: number | null;
  maxLossAfter: number | null;
  shapeBefore: string | null;
  shapeAfter: string | null;
  problems: string[];
  notes: string[];
  after: string[];
}

/** One priced roll: buy the short back at `closePrice`, sell `open` on `expiration`. Prices per share. */
export interface ManageWheelRoll {
  closePrice: number | null;
  open: ManageWheelQuote | null;
  /** Per share, signed: + a net credit. */
  net: number | null;
  expiration: string | null;
  legs: string[];
  check: ManageWheelRollCheck | null;
  checkError: string | null;
}

/** A tested short's two rolls out in time, each null with why when the chain cannot price it. */
export interface ManageWheelRolls {
  leg: string;
  expiration: string | null;
  quotedAt: string | null;
  why: string | null;
  netZero: ManageWheelRoll | null;
  netZeroWhy: string | null;
  premiumGambit: ManageWheelRoll | null;
  premiumGambitWhy: string | null;
}

/** After an assignment, the covered call the skate call band names. */
export interface ManageWheelNextLeg {
  strike: number | null;
  band: number | null;
  bandKind: string | null;
  expiration: string | null;
  why: string | null;
  quote: ManageWheelQuote | null;
  contracts: number | null;
  belowWheelBasis: boolean | null;
}

/** What the wheel rules read for one cycle on one trade. An unreconciled cycle carries `problems`
 *  and no take profit, rolls or next leg: no wheel rule judged it. */
export interface ManageWheelNote {
  underlying: string;
  start: string | null;
  status: string | null;
  problems: string[] | null;
  takeProfit: Array<{ leg: string; mid: number | null; btcPrice: number | null; source: string | null; hit: boolean | null }> | null;
  rolls: ManageWheelRolls[] | null;
  nextLeg: ManageWheelNextLeg | null;
}

/** The skate band for a short's expiry, moved to today's price. Levels are per share. */
export interface ManageWheelBand {
  day: string | null;
  shift: number | null;
  fixed: number | null;
  byExpiry: number | null;
}

export interface ManageWheelResting {
  orderId: string | null;
  status: string | null;
  price: number | null;
  quantity: number | null;
  timeInForce: string | null;
  legs: number | null;
}

/** Today's check of one short a running cycle holds. `tested` is manage's flag, never re-derived. */
export interface ManageWheelCheck {
  leg: string;
  symbol: string;
  right: string | null;
  strike: number | null;
  expiration: string | null;
  dte: number | null;
  quantity: number | null;
  spot: number | null;
  mid: number | null;
  openPrice: number | null;
  wide: boolean | null;
  tested: boolean | null;
  intrinsic: number | null;
  extrinsic: number | null;
  /** Whole-position dollars of extrinsic value left. */
  extrinsicDollars: number | null;
  band: ManageWheelBand | null;
  bandMissing: string | null;
  beyondBand: { fixed: boolean | null; byExpiry: boolean | null } | null;
  restingBtc: ManageWheelResting[];
  btcPrice: number | null;
  markAtOrUnderBtc: boolean | null;
}

/** One wheel cycle (packages/manage `wheel._accounting`). Cash is whole-position dollars, signed (+
 *  received); bases and breakeven are per share; fees are signed as the broker writes them (−). */
export interface ManageWheelCycle {
  underlying: string;
  status: "open" | "complete" | "unreconciled" | string;
  reconciled: boolean | null;
  problems: string[];
  flat: boolean | null;
  start: string | null;
  /** False when shares predate the history: the cycle began earlier than `start`. */
  startKnown: boolean | null;
  end: string | null;
  days: number | null;
  assignments: number | null;
  calledAway: number | null;
  shares: number | null;
  /** False when shares predate the history: no tax basis, no wheel basis, no stock P&L. */
  basisKnown: boolean | null;
  lots: Array<{ date: string | null; shares: number | null; price: number | null; source: string | null }>;
  openOptions: Array<{ symbol: string; leg: string; quantity: number | null }>;
  credits: number | null;
  debits: number | null;
  netCash: number | null;
  fees: number | null;
  netPremium: number | null;
  dividends: number | null;
  taxBasis: number | null;
  wheelBasis: number | null;
  breakeven: { price: number | null; basis: string | null } | null;
  optionPnl: { realised: number | null; unrealised: number | null };
  stockPnl: { realised: number | null; unrealised: number | null };
  pnlNet: number | null;
  skateYield: { value: number | null; capped: boolean | null; capital: number | null; basis: string | null } | null;
  events: Array<{ date: string | null; what: string; legs: string[]; cash: number | null; fees: number | null }>;
  today: ManageWheelCheck[];
  /** The evaluated trades holding this cycle's legs (a finished cycle holds none). */
  tradeIds: string[];
}

/** The account's wheel view (`account.wheel`). */
export interface ManageWheel {
  cycles: ManageWheelCycle[];
  /** Deliveries no option explains, and assignments with no share row. */
  flagged: Array<{ underlying: string; date: string | null; kind: string; row: string | null }>;
  /** Per name, history rows that moved outside any cycle. */
  outside: Record<string, string[]>;
  bandsDay: string | null;
  bandsMissing: string | null;
  /** Why the account has no view (not the designated account, no history, the view failed). */
  error: string | null;
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

/**
 * What a guidance driver's figure measures, so the page states it in its own unit: `bwd` in benchmark
 * shares, `theta` in dollars a day, and the two shares of the book as fractions (0.30 = 30%).
 */
export type ManageDriverKind = "bwd" | "theta" | "bwd_share" | "theta_share";

export interface ManageGuidanceFigure {
  kind: ManageDriverKind;
  value: number | null;
}

export interface ManageGuidance {
  rule: string;
  confidence: number | null;
  reason: string;
  /** What drives it, largest first: an underlying and its figures, each with its kind. A
   *  concentration driver carries both shares, since either one can be what breached the cap. */
  drivers: Array<{ underlying: string; figures: ManageGuidanceFigure[] }>;
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
  /** Theta without the option legs `frontDte` days or less from expiry: what the band, concentration
   *  and the candidates read. Null on an evaluation from before the split (the band then read `theta`). */
  thetaExFront: number | null;
  frontDte: number | null;
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
  /** The lowest and highest confidence the evaluation's ruleset gives the portfolio rules (theta,
   *  delta, break-even, buying power, concentration); null where the evaluation embeds no ruleset. */
  ruleConfidence: { low: number; high: number } | null;
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
  /** A waiting candidate's size once the book has room (buying power and the theta ceiling set
   *  aside), and where taking it today would leave the account. Null on every other row. */
  ifRoom: ManageCandidateRoom | null;
  /** `leveraged`, `inverse` or `volatility` when the underlying is a declared daily-reset fund (from the
   *  session's income lists); null otherwise. */
  leveraged: string | null;
  /** The account already holds an option on this name with this expiry (they settle together, and
   *  can make one structure no rule judged). False on an evaluation from before the badge. */
  heldSameExpiry: boolean;
  /** A credit spread's odds at entry (`candidates.spread_odds`): the chance of any loss and of the max
   *  loss, read from the legs' deltas, the break-even, and what getting in costs over the mid. Null
   *  for anything but a spread, and on an evaluation from before 2026-10-22. */
  odds: {
    pLoss: number | null;
    pMaxLoss: number | null;
    breakEven: number | null;
    entryCost: number | null;
    entryCostShare: number | null;
  } | null;
}

/** `ok`: under the VIX-scaled buying-power cap; `over`: within `bpWarnOver` of it; `far_over`: past that. */
export type ManageBpLevel = "ok" | "over" | "far_over";

export interface ManageCandidateRoom {
  units: number;
  /** The cap the next unit breaks once there is room (a cap on the trade, never on the book). */
  binding: string | null;
  bpAfterPct: number | null;
  bpCapPct: number | null;
  bpLevel: ManageBpLevel | null;
  thetaAfterPct: number | null;
  thetaCeilingPct: number | null;
  thetaOver: boolean;
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
  /** Stopped only by how full the book is (buying power, the theta ceiling), best first, each with
   *  its `ifRoom`. Empty from an evaluation written before the list existed. */
  waiting: ManageCandidate[];
  waitingTotal: number;
  bpUsedPct: number | null;
  bpCapPct: number | null;
  bpWarnOver: number | null;
  error: string | null;
  /** The sizing caps from the evaluation's ruleset (`portfolio.candidates`), as fractions of net liq
   *  (the theta share of the theta the account may carry). The notional cap is the one `size` applies
   *  to this account's basis. Null where the evaluation embeds no ruleset. */
  caps: {
    perTradeRiskPct: number | null;
    notionalPerNamePct: number | null;
    maxNameThetaShare: number | null;
  } | null;
  /** The same list sized as if the earnings trades' buying power were already free (they close after
   *  the call). Null when the account holds no earnings trade, and on an evaluation from before it. */
  exEarnings: ManageExEarnings | null;
}

export interface ManageExEarnings {
  /** The earnings trades set aside, by trade id. */
  trades: string[];
  /** Earnings trades with no bounded max loss; any one makes `dollars` null and `list` absent. */
  unpriced: string[];
  /** Their buying power: each one's defined-risk max loss from open (modelled for a diagonal). */
  dollars: number | null;
  error: string | null;
  list: ManageCandidates | null;
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
  earnings: {
    trades: string[];
    theta: number | null;
    thetaShare: number | null;
    downside: number | null;
    /** The same trades by report date; empty on an evaluation from before it was split out. */
    byDate: Array<{ date: string; trades: string[]; downside: number | null }>;
  };
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
  /** The wheel view; null on an evaluation written before it existed (no `wheel` key). */
  wheel: ManageWheel | null;
  /** The PMCC view; null on an evaluation written before it existed (no `pmcc` key). */
  pmcc: ManagePmcc | null;
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
  /** The watchdog's `manage.snapshot_version` WARN, verbatim, while the newest snapshot is older than
   *  the installed code (views needing the newer fields read empty until the next run); null when it
   *  is current, unchecked, or the session shown is not the newest. */
  snapshotStale: string | null;
  /** Set when the snapshot was taken before 09:30 New York time on its own session (manage's
   *  `pre_open`): spot and marks are the last session's, and today's expiring legs are unpriced. */
  preOpen: { takenAt: string | null; expiringUnpriced: string[] } | null;
}
