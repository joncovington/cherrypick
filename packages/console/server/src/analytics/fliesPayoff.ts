/**
 * Port of cherrypick.flies' fly.py payoff core + analytics.payoff_curve —
 * the profit forest. Sign convention: positive = credit received. P&L is net
 * of recorded fees AND the $5-per-ITM-symbol overnight assignment fee the
 * settlement price would trigger (honesty rule: a cost that would happen is
 * shown, per-settlement-event, never scaled by quantity).
 */

export const CONTRACT_MULTIPLIER = 100;
const ASSIGNMENT_FEE_PER_EVENT = 5;

export interface FlyPosition {
  kind: string; // fly | short_vertical | long_vertical | iron_fly | bwb
  side: string; // put | call
  center: number;
  wingWidth: number;
  farWidth: number | null;
  net: number;
  quantity: number;
  fees: number;
  status: string | null;
}

export function flyPayoff(center: number, w: number, s: number): number {
  return Math.max(0, w - Math.abs(s - center));
}

function ironFlyPayoff(center: number, w: number, s: number): number {
  return flyPayoff(center, w, s) - w;
}

function shortVerticalPayoff(side: string, k: number, w: number, s: number): number {
  if (side === "put") return -Math.max(0, Math.min(w, k - s));
  return -Math.max(0, Math.min(w, s - k));
}

function debitVerticalPayoff(side: string, center: number, w: number, s: number): number {
  if (side === "call") return Math.max(0, Math.min(w, s - (center - w)));
  return Math.max(0, Math.min(w, center + w - s));
}

function bwbPayoff(side: string, k: number, w: number, f: number, s: number): number {
  if (side === "put") return Math.max(0, k + w - s) - 2 * Math.max(0, k - s) + Math.max(0, k - f - s);
  return Math.max(0, s - (k - w)) - 2 * Math.max(0, s - k) + Math.max(0, s - (k + f));
}

function itmLegsAtSettlement(p: FlyPosition, s: number): number {
  const { center, wingWidth: w } = p;
  if (p.kind === "iron_fly") {
    const putLegs = s < center - w ? 2 : s < center ? 1 : 0;
    const callLegs = s > center + w ? 2 : s > center ? 1 : 0;
    return putLegs + callLegs;
  }
  const itm = (strike: number) => (p.side === "put" ? s < strike : s > strike);
  let strikes: number[];
  if (p.kind === "fly") strikes = [center - w, center, center + w];
  else if (p.kind === "short_vertical") strikes = [center, p.side === "put" ? center - w : center + w];
  else if (p.kind === "long_vertical") strikes = [center, p.side === "call" ? center - w : center + w];
  else if (p.kind === "bwb") {
    const f = p.farWidth ?? w;
    strikes =
      p.side === "put" ? [center + w, center, center - f] : [center - w, center, center + f];
  } else return 0;
  return strikes.filter(itm).length;
}

export function positionPnl(p: FlyPosition, s: number): number {
  const w = p.wingWidth;
  let payoff: number;
  if (p.kind === "fly") payoff = flyPayoff(p.center, w, s);
  else if (p.kind === "short_vertical") payoff = shortVerticalPayoff(p.side, p.center, w, s);
  else if (p.kind === "long_vertical") payoff = -shortVerticalPayoff(p.side, p.center, w, s);
  else if (p.kind === "iron_fly") payoff = ironFlyPayoff(p.center, w, s);
  else if (p.kind === "bwb") payoff = bwbPayoff(p.side, p.center, w, p.farWidth ?? w, s);
  else if (p.kind === "debit_vertical") payoff = debitVerticalPayoff(p.side, p.center, w, s);
  else payoff = 0;
  const cash = p.net + payoff;
  let fees = p.fees;
  if (p.status !== "settled") fees += ASSIGNMENT_FEE_PER_EVENT * itmLegsAtSettlement(p, s);
  return cash * CONTRACT_MULTIPLIER * p.quantity - fees;
}

/** Worst-case ITM settlement events per kind, at each kind own worst price. */
const WORST_CASE_ITM_LEGS: Record<string, number> = { fly: 3, short_vertical: 2, long_vertical: 1, iron_fly: 2, bwb: 3 };

/**
 * Port of fly.position_floor: this position worst-case dollar outcome GOING
 * FORWARD, net of recorded fees AND the assignment fee it would owe at that
 * worst price. A fly bottoms at 0 payoff (a genuine guarantee); a short
 * vertical bottoms at -W, full defined risk, and calling that risk-free is
 * the lie the module exists to avoid.
 */
export function positionFloor(p: FlyPosition): number {
  let worstPayoff: number;
  if (p.kind === "fly" || p.kind === "long_vertical") worstPayoff = 0;
  else if (p.kind === "short_vertical" || p.kind === "iron_fly") worstPayoff = -p.wingWidth;
  else if (p.kind === "bwb") worstPayoff = -((p.farWidth ?? p.wingWidth) - p.wingWidth);
  else return 0;
  const reserve = ASSIGNMENT_FEE_PER_EVENT * (WORST_CASE_ITM_LEGS[p.kind] ?? 0);
  return (p.net + worstPayoff) * CONTRACT_MULTIPLIER * p.quantity - p.fees - reserve;
}

export function bookPnl(positions: FlyPosition[], s: number): number {
  return positions.reduce((sum, p) => sum + positionPnl(p, s), 0);
}

// --- tastytrade fee schedule (port of core fees: the IC open stack) ---
const COMMISSION_OPEN = 1.0;
const CLEARING = 0.1;
const ORF = 0.02;
const TAF_SELL = 0.00329;
const INDEX_EXCHANGE: Record<string, number> = { SPX: 0.6, XSP: 0.0, NDX: 0.25, RUT: 0.18 };

function icOpenFee(symbol: string, quantity: number, legs: number, sellLegs: number): number {
  const exch = INDEX_EXCHANGE[symbol.toUpperCase()] ?? 0;
  const perContract = COMMISSION_OPEN + CLEARING + ORF + exch;
  return Math.round((legs * quantity * perContract + sellLegs * quantity * TAF_SELL) * 1e4) / 1e4;
}

export function verticalOpenFee(symbol: string, quantity: number): number {
  return icOpenFee(symbol, quantity, 2, 1);
}

export function flyOpenFee(symbol: string, quantity: number): number {
  return icOpenFee(symbol, quantity, 4, 2);
}

export interface FlyRow extends FlyPosition {
  symbol: string;
  entryTime: string | null;
  completedAt: string | null;
  entryMode: string | null;
  credit: number | null;
  debit: number | null;
}

/**
 * Port of analytics._state_at: this position as it stood at `when`, or null if
 * not on the book yet. A legged entry is a SHORT VERTICAL until it completes;
 * debit_first is a LONG VERTICAL; bwb_roll is a bwb on its opening credit. The
 * rewind is exact: pre-completion net is the recorded credit/debit and the fee
 * is the 2-leg vertical open fee — recorded values, never inferred.
 */
export function stateAt(row: FlyRow, when: string): FlyPosition | null {
  if (row.entryTime === null || when < row.entryTime) return null;
  const state: FlyPosition = {
    kind: row.kind,
    side: row.side,
    center: row.center,
    wingWidth: row.wingWidth,
    farWidth: row.farWidth,
    net: row.net,
    quantity: row.quantity,
    fees: row.fees,
    status: row.status,
  };
  if (row.completedAt !== null && when < row.completedAt) {
    if (row.entryMode === "legged" && row.credit !== null) {
      state.kind = "short_vertical";
      state.net = row.credit;
      state.fees = verticalOpenFee(row.symbol, state.quantity);
    } else if (row.entryMode === "debit_first" && row.debit !== null) {
      state.kind = "long_vertical";
      state.net = -row.debit;
      state.fees = verticalOpenFee(row.symbol, state.quantity);
    } else if (row.entryMode === "bwb_roll" && row.farWidth !== null) {
      state.kind = "bwb";
      state.net = row.credit ?? row.net;
      state.fees = flyOpenFee(row.symbol, state.quantity);
    }
  }
  return state;
}

export interface PayoffCurve {
  empty: boolean;
  positions: number;
  prices: number[];
  pnl: number[];
  centers: number[];
  floor: {
    worst: number;
    worstAt: number | null;
    floorHolds: boolean;
    band: [number, number] | null;
    unboundedBelow: boolean;
  };
}

/** Book P&L across a price grid plus the floor and the band it holds over. */
export function payoffCurve(positions: FlyPosition[], step = 1, points = 120): PayoffCurve {
  if (positions.length === 0) {
    return {
      empty: true,
      positions: 0,
      prices: [],
      pnl: [],
      centers: [],
      floor: { worst: 0, worstAt: null, floorHolds: true, band: null, unboundedBelow: false },
    };
  }
  const centers = positions.map((p) => p.center);
  // A bwb's negative tail sits beyond the far wing — never clip it.
  const width = Math.max(...positions.map((p) => p.farWidth ?? p.wingWidth));
  const lo = Math.min(...centers) - 3 * width;
  const hi = Math.max(...centers) + 3 * width;
  const span = hi - lo;
  const gridStep = span > 0 ? Math.max(step, span / points) : step;

  const prices: number[] = [];
  const pnls: number[] = [];
  for (let x = lo; x <= hi + 1e-9; x += gridStep) {
    prices.push(Math.round(x * 100) / 100);
    pnls.push(Math.round(bookPnl(positions, x) * 100) / 100);
  }

  const worst = Math.min(...pnls);
  const worstAt = prices[pnls.indexOf(worst)] ?? null;

  // Contiguous non-negative zones; band = the zone containing the payoff max.
  const zones: Array<[number, number]> = [];
  let runStart: number | null = null;
  let runEnd: number | null = null;
  for (let i = 0; i < prices.length; i++) {
    if (pnls[i]! >= 0) {
      if (runStart === null) runStart = prices[i]!;
      runEnd = prices[i]!;
    } else if (runStart !== null) {
      zones.push([runStart, runEnd!]);
      runStart = runEnd = null;
    }
  }
  if (runStart !== null) zones.push([runStart, runEnd!]);
  const bestAt = prices[pnls.indexOf(Math.max(...pnls))]!;
  const band = zones.find((z) => z[0] <= bestAt && bestAt <= z[1]) ?? null;

  return {
    empty: false,
    positions: positions.length,
    prices,
    pnl: pnls,
    centers: [...new Set(centers)].sort((a, b) => a - b),
    floor: {
      worst,
      worstAt,
      floorHolds: pnls.every((v) => v >= 0),
      band,
      unboundedBelow: pnls[0]! < 0 || pnls[pnls.length - 1]! < 0,
    },
  };
}
