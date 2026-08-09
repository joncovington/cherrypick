/**
 * Port of scout's analytics/strategies.py: strategy leg-generators over an
 * already-fetched chain (strikes + quotes for one expiration). Pure — the
 * screener service owns all I/O. Short strikes are picked nearest-OTM-by-
 * expected-move (scout's documented fallback; an honest proxy, upgradeable to
 * true delta targeting now that greeks exist in the stream cache). Credit is
 * priced at the mid with a slippage haircut.
 */

import { type Leg, breakevens, maxLoss } from "./payoff.js";

const SLIPPAGE_HAIRCUT = 0.9;

export interface ChainOption {
  strike: number;
  optionType: "C" | "P";
  mid: number | null;
  occSymbol: string | null;
}

export interface Candidate {
  strategy: string;
  legs: Array<{ kind: "call" | "put" | "stock"; quantity: number; price: number; strike: number | null; occSymbol?: string | null }>;
  credit: number;
  maxRisk: number | null;
  breakevens: number[];
  dte: number;
  expiration: string;
}

function nearestByTarget(options: ChainOption[], target: number): ChainOption | null {
  if (options.length === 0) return null;
  return options.reduce((best, o) => (Math.abs(o.strike - target) < Math.abs(best.strike - target) ? o : best));
}

function otmOptions(options: ChainOption[], spot: number, side: "call" | "put"): ChainOption[] {
  if (side === "call") return options.filter((o) => o.optionType === "C" && o.strike > spot);
  return options.filter((o) => o.optionType === "P" && o.strike < spot);
}

function shortStrike(options: ChainOption[], spot: number, expectedMove: number, side: "call" | "put"): ChainOption | null {
  const target = side === "call" ? spot + expectedMove : spot - expectedMove;
  return nearestByTarget(otmOptions(options, spot, side), target);
}

function wingStrike(options: ChainOption[], short: number, width: number, side: "call" | "put"): ChainOption | null {
  const target = side === "call" ? short + width : short - width;
  const candidates =
    side === "call"
      ? options.filter((o) => o.optionType === "C" && o.strike > short)
      : options.filter((o) => o.optionType === "P" && o.strike < short);
  return nearestByTarget(candidates, target);
}

function pack(legs: Leg[], occ: Array<string | null>, credit: number, expiration: string, dte: number, strategy: string): Candidate {
  const loss = maxLoss(legs);
  return {
    strategy,
    legs: legs.map((l, i) => ({
      kind: l.kind,
      quantity: l.quantity,
      price: l.price,
      strike: l.strike ?? null,
      occSymbol: occ[i] ?? null,
    })),
    credit,
    maxRisk: loss.unbounded ? null : Math.abs(loss.value!),
    breakevens: breakevens(legs),
    dte,
    expiration,
  };
}

function creditSpread(
  options: ChainOption[],
  spot: number,
  expectedMove: number,
  wingWidthPct: number,
  expiration: string,
  dte: number,
  side: "call" | "put",
): Candidate | null {
  const short = shortStrike(options, spot, expectedMove, side);
  if (short === null) return null;
  const long = wingStrike(options, short.strike, short.strike * wingWidthPct, side);
  if (long === null || short.mid === null || long.mid === null) return null;
  const credit = (short.mid - long.mid) * SLIPPAGE_HAIRCUT;
  if (credit <= 0) return null;
  const legs: Leg[] = [
    { kind: side, quantity: -1, price: short.mid, strike: short.strike },
    { kind: side, quantity: 1, price: long.mid, strike: long.strike },
  ];
  return pack(legs, [short.occSymbol, long.occSymbol], credit * 100, expiration, dte, `${side}_credit_spread`);
}

export function putCreditSpread(options: ChainOption[], spot: number, em: number, wingPct: number, exp: string, dte: number) {
  return creditSpread(options, spot, em, wingPct, exp, dte, "put");
}

export function callCreditSpread(options: ChainOption[], spot: number, em: number, wingPct: number, exp: string, dte: number) {
  return creditSpread(options, spot, em, wingPct, exp, dte, "call");
}

export function shortPut(options: ChainOption[], spot: number, em: number, exp: string, dte: number): Candidate | null {
  const short = shortStrike(options, spot, em, "put");
  if (short === null || short.mid === null || short.mid <= 0) return null;
  const credit = short.mid * SLIPPAGE_HAIRCUT;
  const legs: Leg[] = [{ kind: "put", quantity: -1, price: credit, strike: short.strike }];
  return pack(legs, [short.occSymbol], credit * 100, exp, dte, "short_put");
}

/** OTM call mid minus OTM put mid at ~one expected move — the chain's own directional pricing. */
export function directionalEdge(options: ChainOption[], spot: number, em: number): number | null {
  const call = shortStrike(options, spot, em, "call");
  const put = shortStrike(options, spot, em, "put");
  if (call?.mid == null || put?.mid == null) return null;
  return call.mid - put.mid;
}

/** Multiplicative composite so a strong return-on-risk isn't diluted; secondary factors floored. */
export function compositeScore(returnOnRisk: number, pop: number, ivRankFrac: number, liquidityRating: number | null): number {
  const liquidityFactor = Math.min(1, (liquidityRating ?? 0) / 4);
  return returnOnRisk * Math.max(pop, 0.05) * Math.max(ivRankFrac, 0.05) * Math.max(liquidityFactor, 0.1);
}
