import type { FastifyInstance, FastifyReply } from "fastify";
import { isBacktestRunId, type BacktestOverview, type BacktestRunReport, type BridgedResult } from "@console/shared";
import { backtestOn, getFeatures } from "../services/featuresBridge.js";
import { readBacktestOverview, readBacktestRun } from "../services/backtestBridge.js";

/**
 * `GET /api/backtest/overview` and `GET /api/backtest/runs/:id` — the Backtest page.
 *
 * **Each route refuses on its own, fail closed.** Unless the orchestrator's features answer was read
 * and says `backtest: true` (`featuresBridge.backtestOn`), the answer is the same 404 an unknown API
 * path gets, before any subprocess runs; the browser hiding the page is never what keeps the data
 * in. The results come from licensed vendor history that must not leave this machine's console.
 *
 * A run id is checked against the runner's own shape before it is spawned; anything else is a 400
 * and runs nothing.
 */

function refuse(reply: FastifyReply): FastifyReply {
  // The body the production not-found handler sends for an unknown `/api/` path, so a closed gate
  // reads as no route at all.
  return reply.code(404).send({ error: "not found" });
}

export function registerBacktestRoutes(app: FastifyInstance): void {
  app.get("/api/backtest/overview", async (_req, reply): Promise<BridgedResult<BacktestOverview> | FastifyReply> => {
    if (!backtestOn(getFeatures())) return refuse(reply);
    return readBacktestOverview();
  });

  app.get<{ Params: { id: string } }>(
    "/api/backtest/runs/:id",
    async (req, reply): Promise<BridgedResult<BacktestRunReport> | FastifyReply> => {
      if (!backtestOn(getFeatures())) return refuse(reply);
      if (!isBacktestRunId(req.params.id)) return reply.code(400).send({ error: "not a run id" });
      return readBacktestRun(req.params.id);
    },
  );
}
