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
  // A `long_vertical` row is debit_first's OPENING trade, priced against its own strikes
  // (+1 (K-w)/-1 K call, or +1 (K+w)/-1 K put) -- exactly what fly.position_pnl does. It is NOT
  // the mirror of a short vertical: that ramps from K outward, this ramps from the far strike INTO
  // K, a full wing width apart. Pinned by test/flies-mirror.test.ts.
  else if (p.kind === "long_vertical") payoff = debitVerticalPayoff(p.side, p.center, w, s);
  else if (p.kind === "iron_fly") payoff = ironFlyPayoff(p.center, w, s);
  else if (p.kind === "bwb") payoff = bwbPayoff(p.side, p.center, w, p.farWidth ?? w, s);
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

/** One position's own payoff on the book's price grid, so a flat book sum can be read back into its parts. */
export interface StructureCurve {
  kind: string;
  side: string;
  center: number;
  /** Not yet converted into a fly: a legged short vertical or a debit_first long vertical still waiting on its second leg. */
  stranded: boolean;
  pnl: number[];
}

export interface BookFloor {
  worst: number;
  /**
   * Where the worst case sits. On a flat tail this is the INNER end of the flat run (the last
   * price before the book climbs), with `worstTail` saying which way the run extends, so the
   * sentence can say "at or below 7655" instead of naming an arbitrary grid point.
   */
  worstAt: number | null;
  worstTail: "below" | "above" | null;
  floorHolds: boolean;
  /** The book cannot move: worst equals best at every price. A distinct state worth naming. */
  locked: boolean;
  band: [number, number] | null;
  /** Whether `band` runs off the scan grid on that side -- an open side, not a boundary. */
  bandOpen: { below: boolean; above: boolean };
  /** Every contiguous non-negative zone, low to high (the forest's zones). */
  bands: Array<[number, number]>;
  unboundedBelow: boolean;
}

export interface PayoffCurve {
  empty: boolean;
  positions: number;
  prices: number[];
  pnl: number[];
  structures: StructureCurve[];
  centers: number[];
  /** The widest wing in the book (a bwb's far wing counts), so a renderer can pad by a wing. */
  wing: number;
  floor: BookFloor;
}

const EMPTY_FLOOR: BookFloor = {
  worst: 0,
  worstAt: null,
  worstTail: null,
  floorHolds: true,
  locked: false,
  band: null,
  bandOpen: { below: false, above: false },
  bands: [],
  unboundedBelow: false,
};

/**
 * Port of fly._scan_prices: a grid spanning every strike, padded a full strike span (at least four
 * steps) beyond the outermost, plus a point one cent EITHER side of every strike. The payoff is
 * piecewise-linear with kinks only at strikes, but the assignment fee is a step function that
 * jumps the instant a leg crosses its strike, so the true worst dollar point sits just past a
 * strike, not on it -- a bare display grid lands the "worst" reading on the wrong side of every
 * jump. Never used for drawing; only for the floor.
 */
export function scanPrices(positions: FlyPosition[], step: number): number[] {
  const eps = 0.01;
  const strikes: number[] = [];
  for (const p of positions) {
    const w = p.wingWidth;
    if (p.kind === "fly" || p.kind === "short_vertical" || p.kind === "long_vertical" || p.kind === "iron_fly") {
      strikes.push(p.center - w, p.center, p.center + w);
    } else if (p.kind === "bwb") {
      const f = p.farWidth ?? w;
      if (p.side === "put") strikes.push(p.center + w, p.center, p.center - f);
      else strikes.push(p.center - w, p.center, p.center + f);
    }
  }
  if (strikes.length === 0) return [];
  const lo = Math.min(...strikes);
  const hi = Math.max(...strikes);
  const pad = Math.max(hi - lo, step * 4);
  const out = new Set<number>();
  for (let x = lo - pad; x <= hi + pad + 1e-9; x += step) out.add(Math.round(x * 1e4) / 1e4);
  for (const s of strikes) {
    out.add(Math.round((s - eps) * 1e4) / 1e4);
    out.add(Math.round((s + eps) * 1e4) / 1e4);
  }
  return [...out].sort((a, b) => a - b);
}

/**
 * Port of fly.book_floor over the scan grid above: the book's worst case and the contiguous
 * non-negative zones, with `band` the zone containing the payoff maximum (a single honest range
 * that can understate coverage, never overstate it). Pinned against the module itself by
 * test/flies-mirror.test.ts.
 */
export function bookFloor(positions: FlyPosition[], step = 1): BookFloor {
  const prices = scanPrices(positions, step);
  if (prices.length === 0) return EMPTY_FLOOR;
  const pnls = prices.map((x) => bookPnl(positions, x));
  const worstRaw = Math.min(...pnls);
  const bestRaw = Math.max(...pnls);
  const near = (v: number, w: number) => Math.abs(v - w) < 0.005;

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
  const bestAt = prices[pnls.indexOf(bestRaw)]!;
  const band = zones.find((z) => z[0] <= bestAt && bestAt <= z[1]) ?? null;

  // A flat tail: report the inner end of the run and which way it extends.
  let worstAt = prices[pnls.indexOf(worstRaw)]!;
  let worstTail: BookFloor["worstTail"] = null;
  if (near(pnls[0]!, worstRaw)) {
    let i = 0;
    while (i + 1 < pnls.length && near(pnls[i + 1]!, worstRaw)) i++;
    worstAt = prices[i]!;
    worstTail = "below";
  } else if (near(pnls[pnls.length - 1]!, worstRaw)) {
    let i = pnls.length - 1;
    while (i > 0 && near(pnls[i - 1]!, worstRaw)) i--;
    worstAt = prices[i]!;
    worstTail = "above";
  }

  return {
    worst: Math.round(worstRaw * 100) / 100,
    worstAt,
    worstTail,
    floorHolds: worstRaw >= 0,
    locked: near(worstRaw, bestRaw),
    band,
    bandOpen: {
      below: band !== null && band[0] === prices[0],
      above: band !== null && band[1] === prices[prices.length - 1],
    },
    bands: zones,
    unboundedBelow: pnls[0]! < 0 || pnls[pnls.length - 1]! < 0,
  };
}

/**
 * Port of analytics.payoff_curve: book P&L across a DISPLAY grid, plus the floor computed on the
 * module's own strike-anchored scan (see bookFloor) rather than on the display grid.
 */
export function payoffCurve(positions: FlyPosition[], step = 1, points = 120): PayoffCurve {
  if (positions.length === 0) {
    return { empty: true, positions: 0, prices: [], pnl: [], structures: [], centers: [], wing: 0, floor: EMPTY_FLOOR };
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
  const structures: StructureCurve[] = positions.map((p) => ({
    kind: p.kind,
    side: p.side,
    center: p.center,
    stranded: p.kind === "short_vertical" || p.kind === "long_vertical",
    pnl: [],
  }));
  for (let x = lo; x <= hi + 1e-9; x += gridStep) {
    prices.push(Math.round(x * 100) / 100);
    pnls.push(Math.round(bookPnl(positions, x) * 100) / 100);
    positions.forEach((p, i) => structures[i]!.pnl.push(Math.round(positionPnl(p, x) * 100) / 100));
  }

  return {
    empty: false,
    positions: positions.length,
    prices,
    pnl: pnls,
    structures,
    centers: [...new Set(centers)].sort((a, b) => a - b),
    wing: width,
    floor: bookFloor(positions, gridStep),
  };
}
