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

interface PayoffRequest {
  legs?: unknown;
  spot?: unknown;
  /** Annualized IV as a decimal (0.25 = 25%). */
  sigma?: unknown;
  /** Days to expiry. */
  dte?: unknown;
  /** Risk-free rate; defaults to 4%. */
  r?: unknown;
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

    return {
      curve,
      breakevens: breaks,
      maxProfit: maxProfit(legs),
      maxLoss: maxLoss(legs),
      netGreeks: netGreeks(legs),
      slopes: { below: slopeBelow(legs), above: slopeAbove(legs) },
      pnlAtSpot: Number.isFinite(spot) ? payoffAt(legs, spot) : null,
      pop: hasPop ? pop(legs, spot, sigma, t, r) : null,
      expectedMove: hasPop ? expectedMove(spot, sigma, t) : null,
    };
  });
}
