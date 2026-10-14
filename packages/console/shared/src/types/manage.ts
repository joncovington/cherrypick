/**
 * The daily position-management evaluation (packages/manage), as the Live page's `manage` tab reads
 * it. Every verdict, level and confidence here is computed by the package; the console only shows
 * them. Money is whole-position dollars, signed cash flow, and every P&L is a mark P&L GROSS of fees.
 */

export type ManageAction = "CLOSE" | "TAKE_PROFIT" | "ROLL_OR_CLOSE" | "CLOSE_LEGS" | "REVIEW" | "SETTLES" | "HOLD";

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
  kind: "credit" | "debit" | null;
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
  fired: ManageFired[];
  legs: ManageLeg[];
  /** What the trade was opened for: signed cash flow (a credit received is +) and the net price. */
  opening: { net: number | null; netPrice: number | null; netSide: "cr" | "db" | null } | null;
  order: ManageOrder | null;
  takeProfit: ManageLevel | null;
  stop: ManageLevel | null;
  timeExit: string | null;
  earnings: { date: string; days: number; timeOfDay: string | null; beforeNearestShortExpiry: boolean } | null;
  exDividend: { date: string; days: number } | null;
  /** Modelled at the nearest expiry (Black-Scholes at each leg's own IV); never a tradable price. */
  profile: {
    horizon: string | null;
    maxLossFromOpen: number | null;
    downsideFromNow: number | null;
    upsideFromNow: number | null;
    unboundedLow: boolean;
  } | null;
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
