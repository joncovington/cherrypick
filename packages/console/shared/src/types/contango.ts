/**
 * contango's read surface (packages/contango): the VIX/VIX3M switch held in shares. A position is
 * one holding stint; an arm's daily NAV row (`contango_sessions`) is the series it is judged on.
 * Python field names are re-spelled once, in `readers/contango.ts`.
 */

export interface ContangoArmParams {
  arm: string;
  enabled: boolean;
  enterBelow: number;
  exitAtOrAbove: number;
  riskSymbol: string;
  cashSymbol: string;
  startingCapital: number;
}

export interface ContangoParams {
  arms: ContangoArmParams[];
  decisionMinutesBeforeClose: number;
  decisionWindowMinutes: number;
  slippageFloorBps: number;
  maxSpreadBps: number;
}

export interface ContangoRegimeRow {
  tradeDate: string;
  ratio: number | null;
  vix: number | null;
  vix3m: number | null;
  usable: boolean;
  refusal: string | null;
}

/** One arm's session: what it held, what its rule chose, what happened, its NAV at the decision. */
export interface ContangoSessionRow {
  tradeDate: string;
  arm: string;
  ratio: number | null;
  stateBefore: string | null;
  stateAfter: string | null;
  /** hold / switch / missed */
  action: string | null;
  refusal: string | null;
  holdingSymbol: string | null;
  shares: number | null;
  mark: number | null;
  cash: number | null;
  nav: number | null;
}

/** A holding stint in the money layout: entry (negative) + exit + distributions = gross;
 *  gross - fees - slippage = net. Open stints carry their entry side and a mark. */
export interface ContangoStint {
  positionId: string;
  arm: string;
  symbol: string;
  role: string;
  shares: number;
  status: string;
  entrySession: string;
  entryMid: number | null;
  entryValue: number | null;
  entryRatio: number | null;
  exitSession: string | null;
  exitMid: number | null;
  exitValue: number | null;
  exitRatio: number | null;
  exitReason: string | null;
  distributions: number;
  grossPnl: number | null;
  fees: number | null;
  slippage: number | null;
  netPnl: number | null;
  /** Open stints: the latest recorded mark and the unrealised P&L at it (net of entry costs). */
  mark: number | null;
  unrealisedNet: number | null;
}

/** One fill (a stint's entry or exit), for the costs page: slippage against the replay's 2 bps. */
export interface ContangoFill {
  session: string;
  arm: string;
  symbol: string;
  side: "buy" | "sell";
  shares: number;
  value: number;
  slippage: number;
  slippageBps: number | null;
  fees: number;
}

export interface ContangoDistribution {
  arm: string;
  symbol: string;
  exDate: string;
  perShare: number;
  shares: number;
  amount: number;
  creditedSession: string;
}

export interface ContangoArmState {
  arm: string;
  startingCapital: number | null;
  cash: number | null;
  openedSession: string | null;
  holding: { symbol: string; role: string; shares: number; entrySession: string; entryMid: number | null } | null;
  today: ContangoSessionRow | null;
  latestNav: number | null;
  switches: number;
  missed: number;
  distributionsTotal: number;
}

export interface ContangoPayload {
  session: string | null;
  dbPresent: boolean;
  params: ContangoParams;
  regimeSeries: ContangoRegimeRow[];
  arms: ContangoArmState[];
  sessions: ContangoSessionRow[];
  stints: ContangoStint[];
  fills: ContangoFill[];
  distributions: ContangoDistribution[];
  measurementBreaks: Array<{ date: string; key: string; note: string | null }>;
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}
