/**
 * **Preserved evidence, 2026-08-31.** The research/screener section this backed was retired, but
 * this module and its test suite are kept: each of the 43 cases across `describe.test.ts` and
 * `narrative.test.ts` replays an observed reference-platform card, and together they are the
 * evidence that justified these formulae. That evidence is irreplaceable and costs nothing to keep,
 * where re-deriving it would mean re-observing a platform we no longer run against.
 *
 * Nothing in the console calls this now. It is a reference implementation, not a live path.
 */
/**
 * Port of scout's analytics/describe.py: strategy-card math and human-readable strategy text —
 * annualized return, probability of worthless, model (Black-Scholes) greeks, a plain-language
 * strategy explanation, the short-put "consider selling..." suggestion, and a pass/warn/fail
 * checklist. Pure, no I/O, like the rest of `analytics/`.
 *
 * The thresholds and formulae below are *empirically calibrated*, not invented, and the evidence is
 * the reason they can be trusted — so it travels with the code rather than staying in the package
 * that is being deleted:
 *
 * **Annualized return** was reverse-engineered from a reference platform's own displayed pairs and
 * verified against two independent examples before being written down: raw = credit / max_risk,
 * annualized = (1 + raw) ** (365 / dte) - 1 — COMPOUNDED, not linear (a linear raw * 365/dte
 * reproduces neither example). $150/$900/25 DTE → 16.67% raw → 849.3%; $113/$987/25 → 11.45% →
 * 386.7%. Both match the reference display to rounding. The compounding assumption — that the same
 * trade could be repeated back-to-back all year at the same return — is optimistic by construction:
 * this is a comparison metric, not a forecast, and the UI must keep the asterisk.
 *
 * **Model greeks** are Black-Scholes analytics from strike/spot/IV/T/r. The console has a live
 * greeks source (DXLink `Greeks` events); these are the clearly-labelled FALLBACK for legs that
 * arrived without live values, because a labelled model greek beats a silently absent one. They
 * assume one flat IV across legs.
 *
 * **Checklist thresholds** are calibrated against observed reference-platform gradings (2026-08-03,
 * five cards spanning the full range), not published numbers:
 *   - POW: 53.54% graded red, 55.55/58.28/65.66% yellow, 75.69% and 81.39% green → warn ≥ 55%,
 *     pass ≥ 70%. The pass bound is bracketed to (65.66, 75.69]; 70 is used, with 75 not yet
 *     excluded — one observed card in the 67–74% band would settle it.
 *   - Annualized: 6.30% already graded green → pass ≥ 5%, warn ≥ 2%. No yellow/red example was
 *     observed; the fail zone is extrapolated.
 *   - Spread: 1.0% of mid green, 7.5% yellow, 19.7%+ red → pass ≤ 5%, warn ≤ 15%.
 *
 * **Score** reduces, for DEFINED-RISK spreads, to `100 * pop * (reward + risk) / risk` — fit from
 * six independent points by least squares (R² = 0.9997) and reconfirmed on five more including two
 * iron condors: eleven defined-risk points across two underlyings/days, typical error under half a
 * point on a scale spanning 84–144. It does NOT hold for a naked single option (predicted ~450
 * against an actual 99), which is why `score` requires two option legs.
 *
 * For **undefined** risk the reference platform's number resisted roughly twenty data points of
 * reverse engineering — price scale, IV Rank and absolute IV all appeared to matter without any one
 * explaining the set. Rather than keep guessing, that branch uses `probableRisk2sd` — a real,
 * honestly computed figure — as the denominator in the same formula shape. It is scout's own
 * extension, not a replica, and the caller must mark it estimated so the two can never be confused.
 */
import { type Leg, breakevens, maxLoss, maxProfit, payoffAt, type Extremum } from "./payoff.js";
import { expectedMove, normCdf, probBelow } from "./pop.js";
import type { TrendGrade } from "./trend.js";

export const DAYS_PER_YEAR = 365;

export type CheckStatus = "pass" | "warn" | "fail";
export interface CheckItem {
  name: string;
  status: CheckStatus;
}
export interface ModelGreeks {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export function rawReturn(credit: number, maxRisk: number): number | null {
  if (!Number.isFinite(credit) || !Number.isFinite(maxRisk) || maxRisk <= 0) return null;
  return credit / maxRisk;
}

/** Compounded annualization of credit/max_risk over the trade's own holding period. */
export function annualizedReturn(credit: number, maxRisk: number, dte: number): number | null {
  const raw = rawReturn(credit, maxRisk);
  if (raw === null || raw <= -1 || !Number.isFinite(dte) || dte <= 0) return null;
  return Math.pow(1 + raw, DAYS_PER_YEAR / dte) - 1;
}

/**
 * The covered-call "12M Projected Yield": annualized option return plus the position's trailing
 * dividend yield. Simple addition, not compounding — the option side already carries its own
 * compounding assumption and the dividend side is a trailing yield, so stacking two already-labelled
 * estimates needs no further modelling (KWEB 2026-08-03: 22.93% + 7.36% = 30.29%, matching exactly).
 * Null when either side is unknown, since a partial total understates the real figure silently.
 */
export function projectedYield12m(
  annualized: number | null,
  dividendYield: number | null,
): number | null {
  if (annualized === null || dividendYield === null) return null;
  return annualized + dividendYield;
}

/**
 * A POP-weighted reward/risk metric. Requires ≥ 2 option legs and a finite reward — an inapplicable
 * score is absent, never wrong. `probableRisk` opts into the undefined-risk branch; omitting it
 * keeps this returning null for unbounded baskets.
 */
export function score(
  popValue: number | null,
  legs: Leg[],
  maxReward: Extremum,
  loss: Extremum,
  probableRisk: number | null = null,
): number | null {
  const optionLegs = legs.filter((l) => l.kind !== "stock");
  if (optionLegs.length < 2 || popValue === null) return null;
  if (maxReward.unbounded) return null;
  const rewardValue = maxReward.value;
  if (rewardValue === null || rewardValue === undefined) return null;

  let risk: number;
  if (loss.unbounded) {
    if (probableRisk === null || probableRisk <= 0) return null;
    risk = probableRisk;
  } else {
    if (loss.value === null || loss.value === undefined) return null;
    risk = Math.abs(loss.value);
    if (risk <= 0) return null;
  }
  return (100 * popValue * (rewardValue + risk)) / risk;
}

/**
 * The reference platform's own disclosed methodology for an unlimited-risk basket: "probable risk
 * based on a wide (2 SD) move against you". Computed directly — the position's dollar loss (0 if
 * actually profitable) at spot ∓ 2 standard deviations, whichever side is worse.
 *
 * This is a genuinely different number from `score`'s undefined-risk denominator: independently
 * computed for three same-day GOOG strangles it gave ~$6,900–7,100, while inverting Score for those
 * same positions implies a risk five to six times smaller. Two unrelated calculations.
 */
export function probableRisk2sd(
  legs: Leg[],
  spot: number,
  sigma: number,
  t: number,
): number | null {
  if (!Number.isFinite(sigma) || !Number.isFinite(t) || sigma <= 0 || t <= 0) return null;
  const em = expectedMove(spot, sigma, t);
  const down = payoffAt(legs, Math.max(0, spot - 2 * em));
  const up = payoffAt(legs, spot + 2 * em);
  const worst = Math.min(down, up);
  return worst < 0 ? -worst : 0;
}

/**
 * P(every SHORT option in the basket expires worthless) — the premium-seller's "POW". Null when
 * there is no short option, because the metric does not apply to a pure debit position.
 */
export function probWorthless(
  legs: Leg[],
  spot: number,
  sigma: number,
  t: number,
  r: number,
): number | null {
  const shortPuts = legs
    .filter((l) => l.kind === "put" && l.quantity < 0 && l.strike)
    .map((l) => l.strike as number);
  const shortCalls = legs
    .filter((l) => l.kind === "call" && l.quantity < 0 && l.strike)
    .map((l) => l.strike as number);
  if (shortPuts.length === 0 && shortCalls.length === 0) return null;
  const lo = shortPuts.length > 0 ? Math.max(...shortPuts) : null;
  const hi = shortCalls.length > 0 ? Math.min(...shortCalls) : null;
  const pHi = hi !== null ? probBelow(spot, hi, sigma, t, r) : 1;
  const pLo = lo !== null ? probBelow(spot, lo, sigma, t, r) : 0;
  return Math.max(0, pHi - pLo);
}

function d1(spot: number, strike: number, sigma: number, t: number, r: number): number | null {
  if (spot <= 0 || strike <= 0 || sigma <= 0 || t <= 0) return null;
  return (Math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * Math.sqrt(t));
}

function phi(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

/**
 * Position-level model greeks (whole basket, 100 shares/contract): delta and gamma in $ P/L per $1
 * underlying move, theta in $ per DAY, vega in $ per one percentage point of IV. A leg without a
 * strike (stock) contributes delta only.
 */
export function bsGreeks(
  legs: Leg[],
  spot: number,
  sigma: number,
  t: number,
  r: number,
): ModelGreeks {
  const totals = { delta: 0, gamma: 0, theta: 0, vega: 0 };
  let anyOption = false;
  for (const leg of legs) {
    const mult = leg.quantity * 100;
    if (leg.kind === "stock") {
      totals.delta += 1 * mult;
      continue;
    }
    const a = d1(spot, leg.strike ?? 0, sigma, t, r);
    if (a === null) continue;
    anyOption = true;
    const d2 = a - sigma * Math.sqrt(t);
    const delta = leg.kind === "call" ? normCdf(a) : normCdf(a) - 1;
    const gamma = phi(a) / (spot * sigma * Math.sqrt(t));
    let thetaYear = -(spot * phi(a) * sigma) / (2 * Math.sqrt(t));
    if (leg.kind === "call") {
      thetaYear -= r * (leg.strike ?? 0) * Math.exp(-r * t) * normCdf(d2);
    } else {
      thetaYear += r * (leg.strike ?? 0) * Math.exp(-r * t) * normCdf(-d2);
    }
    const vega = (spot * phi(a) * Math.sqrt(t)) / 100;
    totals.delta += delta * mult;
    totals.gamma += gamma * mult;
    totals.theta += (thetaYear / DAYS_PER_YEAR) * mult;
    totals.vega += vega * mult;
  }
  if (!anyOption && totals.delta === 0) {
    return { delta: null, gamma: null, theta: null, vega: null };
  }
  const round2 = (v: number): number => Math.round(v * 100) / 100;
  return {
    delta: round2(totals.delta),
    gamma: round2(totals.gamma),
    theta: round2(totals.theta),
    vega: round2(totals.vega),
  };
}

/**
 * Which tail the position prefers, from the payoff engine's own numbers. Probes at ±40% — wide
 * enough to reach past the strikes of a normal OTM structure. A first draft probed ±10%, which
 * landed BOTH probes inside an OTM put spread's max-profit plateau and called the spread "neutral";
 * that was caught live when a bullish vertical's market-trend row warned against a bullish read.
 */
export function direction(legs: Leg[], spot: number): "bullish" | "bearish" | "neutral" {
  const up = payoffAt(legs, spot * 1.4);
  const down = payoffAt(legs, spot * 0.6);
  if (up > down) return "bullish";
  if (down > up) return "bearish";
  return "neutral";
}

export interface QuotedLeg {
  quantity: number | null;
  bid: number | null;
  ask: number | null;
}

/**
 * Bid/ask spread of the NET strategy price as a fraction of its mid — what the reference platform's
 * Spread & Liquidity row actually grades (per the observed CSX card: combo bid $0.00 / ask $1.30,
 * not per-leg widths). Conservative fill = sell at bid / buy at ask; generous = the reverse.
 * Null when any leg lacks a two-sided quote: an ungraded spread must warn, not pass.
 */
export function comboSpreadPct(quotedLegs: QuotedLeg[]): number | null {
  let conservative = 0;
  let generous = 0;
  for (const leg of quotedLegs) {
    const { quantity: qty, bid, ask } = leg;
    if (qty === null || bid === null || ask === null) return null;
    if (qty < 0) {
      conservative += -qty * bid;
      generous += -qty * ask;
    } else {
      conservative -= qty * ask;
      generous -= qty * bid;
    }
  }
  const spread = Math.abs(generous - conservative);
  const mid = (generous + conservative) / 2;
  if (mid === 0) return null;
  return spread / Math.abs(mid);
}

function money(v: number): string {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * The "This is a bullish strategy with limited risk of $X…" paragraph, from the payoff engine's own
 * numbers — every claim traceable to a computed quantity.
 */
export function strategyExplanation(
  legs: Leg[],
  spot: number,
  popValue: number | null,
  expiration: string | null,
): string {
  const dir = direction(legs, spot);
  const loss = maxLoss(legs);
  const profit = maxProfit(legs);
  const riskPart = loss.unbounded
    ? "unlimited risk"
    : `limited risk of $${money(Math.abs(loss.value ?? 0))}`;
  const rewardPart = profit.unbounded
    ? "unlimited potential reward"
    : `limited potential reward of $${money(profit.value ?? 0)}`;
  const sentences = [`This is a ${dir} strategy with ${riskPart} and ${rewardPart}.`];

  const breaks = breakevens(legs);
  const by = expiration ? ` by ${expiration}` : "";
  if (breaks.length === 1) {
    const side = payoffAt(legs, breaks[0]! * 1.01) > 0 ? "above" : "below";
    sentences.push(`It profits if the stock closes ${side} $${money(breaks[0]!)}${by}.`);
  } else if (breaks.length >= 2) {
    const mid = (breaks[0]! + breaks[breaks.length - 1]!) / 2;
    const word = payoffAt(legs, mid) > 0 ? "between" : "outside";
    sentences.push(
      `It profits if the stock closes ${word} $${money(breaks[0]!)} and $${money(breaks[breaks.length - 1]!)}${by}.`,
    );
  }
  if (popValue !== null) {
    sentences.push(`There is a ${(popValue * 100).toFixed(1)}% model probability of that happening.`);
  }
  return sentences.join(" ");
}

/** The greeks read aloud: what a $1 move, a day of decay and a vol point do in dollars. */
export function greeksExplanation(symbol: string, greeks: ModelGreeks): string | null {
  const { delta, theta, vega } = greeks;
  if (delta === null) return null;
  const parts = [
    delta >= 0
      ? `For every $1 ${symbol} rises, this position makes about $${money(delta)}`
      : `For every $1 ${symbol} rises, this position loses about $${money(Math.abs(delta))}`,
  ];
  if (theta !== null) {
    parts.push(`time decay ${theta >= 0 ? "adds" : "costs"} about $${money(Math.abs(theta))} per day`);
  }
  if (vega !== null) {
    parts.push(
      `a one-point IV ${vega >= 0 ? "rise adds" : "rise costs"} about $${money(Math.abs(vega))}`,
    );
  }
  return parts.join("; ") + ". Model greeks (Black-Scholes, flat IV), not a live feed.";
}

/** The wheel-style framing: the assignment case as stock acquisition at a discount. */
export function shortPutSuggestion(
  symbol: string,
  strike: number,
  expiration: string,
  creditDollars: number,
  spot: number,
): string {
  const net = strike - creditDollars / 100;
  const discount = spot > 0 ? (spot - net) / spot : 0;
  return (
    `Consider selling the ${expiration} $${money(strike)} put on ${symbol} to ` +
    `potentially acquire the stock at a ${(discount * 100).toFixed(1)}% discount. You collect ` +
    `$${money(creditDollars)} in premium per contract and take on the obligation to buy 100 ` +
    `shares at a net price of $${money(net)} if the stock closes below $${money(strike)} and the ` +
    `put is exercised.`
  );
}

/**
 * True when the chain has a genuine weekly expiration cadence — a gap of ≤ 10 days between two
 * consecutive expirations somewhere in the chain (the earnings module's own rule). A monthly-only
 * name can coincidentally have a near expiration without running weeklies; cadence is the honest
 * check.
 */
export function hasWeeklyCadence(expirationIsos: string[]): boolean {
  const times: number[] = [];
  for (const iso of expirationIsos) {
    const t = Date.parse(`${iso}T00:00:00Z`);
    if (Number.isNaN(t)) return false;
    times.push(t);
  }
  times.sort((a, b) => a - b);
  const DAY = 86_400_000;
  for (let i = 1; i < times.length; i++) {
    if ((times[i]! - times[i - 1]!) / DAY <= 10) return true;
  }
  return false;
}

/**
 * Spread & Liquidity grade. Suite rule: HIGH liquidity must always have weekly expirations
 * available — so a pass requires a tight spread AND confirmed weekly cadence; a tight spread on a
 * monthly-only chain caps at warn. `hasWeeklies === null` means the caller did not evaluate cadence,
 * and the spread grade then stands alone.
 */
export function spreadStatus(
  spreadPct: number | null,
  hasWeeklies: boolean | null = null,
): CheckStatus {
  if (spreadPct === null) return "warn";
  if (spreadPct <= 0.05) return hasWeeklies === false ? "warn" : "pass";
  if (spreadPct <= 0.15) return "warn";
  return "fail";
}

const TREND_SIDE: Partial<Record<TrendGrade, number>> = {
  bullish: 1,
  mildly_bullish: 1,
  mildly_bearish: -1,
  bearish: -1,
};

/**
 * The credit-spread (directional-strategy) checklist, calibrated from four observed reference
 * cards: Stock Trend and Market Trend grade the strategy's direction against the stock's and the
 * S&P 500's 1M trend — aligned passes, opposed fails, neutral or unknown warns. The reference's own
 * Score row is omitted until there is a score analog: a missing row beats a fabricated one.
 */
export function checklistDirectional(
  strategyDirection: string,
  stockTrend1m: TrendGrade | null,
  marketTrend1m: TrendGrade | null,
  earningsInside: boolean | null,
  spreadPct: number | null,
  hasWeeklies: boolean | null = null,
): CheckItem[] {
  const want = strategyDirection === "bullish" ? 1 : strategyDirection === "bearish" ? -1 : 0;
  const trendStatus = (label: TrendGrade | null): CheckStatus => {
    if (want === 0 || label === null) return "warn";
    const side = TREND_SIDE[label];
    if (side === undefined) return "warn";
    return side === want ? "pass" : "fail";
  };
  return [
    { name: "Stock trend", status: trendStatus(stockTrend1m) },
    { name: "Market trend", status: trendStatus(marketTrend1m) },
    {
      name: "Earnings date",
      status: earningsInside === null ? "warn" : earningsInside ? "warn" : "pass",
    },
    { name: "Spread & liquidity", status: spreadStatus(spreadPct, hasWeeklies) },
  ];
}

/**
 * Pass/warn/fail per criterion. An unknowable input warns rather than passing — absence of data is
 * not a green light.
 */
export function checklist(
  powValue: number | null,
  annualized: number | null,
  earningsInside: boolean | null,
  spreadPct: number | null,
  hasWeeklies: boolean | null = null,
): CheckItem[] {
  const grade = (value: number | null, passAt: number, warnAt: number): CheckStatus =>
    value === null ? "warn" : value >= passAt ? "pass" : value >= warnAt ? "warn" : "fail";
  return [
    { name: "Probability of worthless", status: grade(powValue, 0.7, 0.55) },
    { name: "Annualized return", status: grade(annualized, 0.05, 0.02) },
    {
      name: "Earnings date",
      status: earningsInside === null ? "warn" : earningsInside ? "warn" : "pass",
    },
    { name: "Spread & liquidity", status: spreadStatus(spreadPct, hasWeeklies) },
  ];
}
