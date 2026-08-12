import type { FastifyInstance } from "fastify";
import {
  type Leg,
  payoffCurve,
  payoffAt,
  breakevens,
  maxProfit,
  maxLoss,
  netGreeks,
  slopeBelow,
  slopeAbove,
} from "../analytics/payoff.js";
import { pop, expectedMove } from "../analytics/pop.js";
import {
  annualizedReturn,
  bsGreeks,
  checklist,
  checklistDirectional,
  comboSpreadPct,
  direction,
  greeksExplanation,
  hasWeeklyCadence,
  probWorthless,
  probableRisk2sd,
  projectedYield12m,
  rawReturn,
  score,
  strategyExplanation,
  type QuotedLeg,
} from "../analytics/describe.js";
import type { TrendGrade } from "../analytics/trend.js";

interface PayoffRequest {
  legs?: unknown;
  spot?: unknown;
  /** Annualized IV as a decimal (0.25 = 25%). */
  sigma?: unknown;
  /** Days to expiry. */
  dte?: unknown;
  /** Risk-free rate; defaults to 4%. */
  r?: unknown;
  /** Symbol, expiration and context used only for the human-readable half of the response. */
  symbol?: unknown;
  expiration?: unknown;
  /** Per-leg two-sided quotes, for the NET combo spread the liquidity row grades. */
  quotedLegs?: unknown;
  /** Every expiration the chain offers, so weekly cadence can be checked honestly. */
  expirations?: unknown;
  /** Whether an earnings report lands inside the expiration. Null when unknown — which warns. */
  earningsInside?: unknown;
  /** The stock's and the market's 1M trend, for the directional checklist. */
  stockTrend1m?: unknown;
  marketTrend1m?: unknown;
  /** Trailing dividend yield as a fraction, for the covered-call projected yield. */
  dividendYield?: unknown;
}

const TREND_GRADES = new Set([
  "bullish",
  "mildly_bullish",
  "neutral",
  "mildly_bearish",
  "bearish",
]);

function asTrend(v: unknown): TrendGrade | null {
  return typeof v === "string" && TREND_GRADES.has(v) ? (v as TrendGrade) : null;
}

function asQuotedLegs(raw: unknown): QuotedLeg[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  return raw.map((item) => {
    const l = item as Record<string, unknown>;
    const n = (k: string): number | null => {
      const v = l[k];
      return typeof v === "number" && Number.isFinite(v) ? v : null;
    };
    return { quantity: n("quantity"), bid: n("bid"), ask: n("ask") };
  });
}

function asBool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function parseLegs(raw: unknown): Leg[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const legs: Leg[] = [];
  for (const item of raw) {
    const l = item as Record<string, unknown>;
    const kind = l["kind"];
    if (kind !== "call" && kind !== "put" && kind !== "stock") return null;
    const quantity = Number(l["quantity"]);
    const price = Number(l["price"]);
    if (!Number.isFinite(quantity) || quantity === 0 || !Number.isFinite(price)) return null;
    const strike = l["strike"] == null ? null : Number(l["strike"]);
    if (kind !== "stock" && (strike === null || !Number.isFinite(strike) || strike <= 0)) return null;
    const greek = (k: string): number | null => {
      const v = l[k];
      return typeof v === "number" && Number.isFinite(v) ? v : null;
    };
    legs.push({
      kind,
      quantity,
      price,
      strike,
      delta: greek("delta"),
      gamma: greek("gamma"),
      theta: greek("theta"),
      vega: greek("vega"),
    });
  }
  return legs;
}

export function registerPayoffRoutes(app: FastifyInstance): void {
  // Pure computation — POST for the JSON body, but nothing is stored and no broker call is made.
  app.post("/api/payoff", async (req, reply) => {
    const body = req.body as PayoffRequest;
    const legs = parseLegs(body.legs);
    if (legs === null) {
      return reply.code(400).send({ error: "legs must be a non-empty array of {kind, quantity, price, strike}" });
    }
    const spot = Number(body.spot);
    const sigma = Number(body.sigma);
    const dte = Number(body.dte);
    const r = body.r == null ? 0.04 : Number(body.r);
    const t = Number.isFinite(dte) ? Math.max(dte, 0) / 365 : NaN;

    const curve = payoffCurve(legs);
    const breaks = breakevens(legs);
    const hasPop = Number.isFinite(spot) && spot > 0 && Number.isFinite(sigma) && sigma > 0 && Number.isFinite(t);

    const profit = maxProfit(legs);
    const loss = maxLoss(legs);
    const popValue = hasPop ? pop(legs, spot, sigma, t, r) : null;

    // --- the describe.py half: the strategy-card numbers and their prose.
    const symbol = typeof body.symbol === "string" ? body.symbol.toUpperCase() : null;
    const expiration = typeof body.expiration === "string" ? body.expiration : null;
    const credit = -legs.reduce((sum, l) => sum + l.price * l.quantity * 100, 0);
    const maxRisk = loss.unbounded ? null : Math.abs(loss.value ?? 0);
    const annualized =
      maxRisk !== null && Number.isFinite(dte) ? annualizedReturn(credit, maxRisk, dte) : null;
    const probableRisk = hasPop ? probableRisk2sd(legs, spot, sigma, t) : null;
    const modelGreeks = hasPop ? bsGreeks(legs, spot, sigma, t, r) : null;
    const pow = hasPop ? probWorthless(legs, spot, sigma, t, r) : null;
    const quoted = asQuotedLegs(body.quotedLegs);
    const spreadPct = quoted ? comboSpreadPct(quoted) : null;
    const expirations = Array.isArray(body.expirations)
      ? body.expirations.filter((e): e is string => typeof e === "string")
      : null;
    // null, not false: "we did not check cadence" grades differently from "there are no weeklies".
    const weeklies = expirations && expirations.length > 0 ? hasWeeklyCadence(expirations) : null;
    const earningsInside = asBool(body.earningsInside);
    const dir = Number.isFinite(spot) ? direction(legs, spot) : null;
    const dividendYield =
      typeof body.dividendYield === "number" && Number.isFinite(body.dividendYield)
        ? body.dividendYield
        : null;

    return {
      curve,
      breakevens: breaks,
      maxProfit: profit,
      maxLoss: loss,
      netGreeks: netGreeks(legs),
      slopes: { below: slopeBelow(legs), above: slopeAbove(legs) },
      pnlAtSpot: Number.isFinite(spot) ? payoffAt(legs, spot) : null,
      pop: popValue,
      expectedMove: hasPop ? expectedMove(spot, sigma, t) : null,

      direction: dir,
      credit,
      rawReturn: maxRisk !== null ? rawReturn(credit, maxRisk) : null,
      annualizedReturn: annualized,
      projectedYield12m: projectedYield12m(annualized, dividendYield),
      probWorthless: pow,
      probableRisk2sd: probableRisk,
      score: score(popValue, legs, profit, loss, probableRisk),
      // A defined-risk score is externally validated; an undefined-risk one is the console's own
      // estimate. The UI must never let the two read as the same number.
      scoreIsEstimated: loss.unbounded,
      modelGreeks,
      comboSpreadPct: spreadPct,
      hasWeeklyCadence: weeklies,
      explanation: Number.isFinite(spot)
        ? strategyExplanation(legs, spot, popValue, expiration)
        : null,
      greeksText: symbol && modelGreeks ? greeksExplanation(symbol, modelGreeks) : null,
      checklist: checklist(pow, annualized, earningsInside, spreadPct, weeklies),
      checklistDirectional:
        dir === null
          ? null
          : checklistDirectional(
              dir,
              asTrend(body.stockTrend1m),
              asTrend(body.marketTrend1m),
              earningsInside,
              spreadPct,
              weeklies,
            ),
    };
  });
}
