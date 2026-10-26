import type { FastifyInstance, FastifyReply } from "fastify";
import {
  isBacktestCapital,
  isBacktestRunId,
  type BacktestOverview,
  type BacktestRunReport,
  type BridgedResult,
  type ResearchQueue,
} from "@console/shared";
import { backtestOn, getFeatures } from "../services/featuresBridge.js";
import { readBacktestOverview, readBacktestRun } from "../services/backtestBridge.js";
import { readResearchQueue } from "../services/researchQueueBridge.js";
import type { ConsoleConfig } from "../config.js";
import { readSlices } from "../readers/slices.js";

/**
 * `GET /api/backtest/overview`, `GET /api/backtest/runs/:id` and `GET /api/backtest/queue` — the
 * Backtest page.
 *
 * **Each route refuses on its own, fail closed.** Unless the orchestrator's features answer was read
 * and says `backtest: true` (`featuresBridge.backtestOn`), the answer is the same 404 an unknown API
 * path gets, before any subprocess runs; the browser hiding the page is never what keeps the data
 * in. The results come from licensed vendor history that must not leave this machine's console.
 *
 * A run id is checked against the runner's own shape before it is spawned; anything else is a 400
 * and runs nothing. The queue is read-only: rows are added on the command line (`run.py queue-add`).
 */

function refuse(reply: FastifyReply): FastifyReply {
  // The body the production not-found handler sends for an unknown `/api/` path, so a closed gate
  // reads as no route at all.
  return reply.code(404).send({ error: "not found" });
}

export function registerBacktestRoutes(app: FastifyInstance, config?: ConsoleConfig): void {
  app.get("/api/backtest/overview", async (_req, reply): Promise<BridgedResult<BacktestOverview> | FastifyReply> => {
    if (!backtestOn(getFeatures())) return refuse(reply);
    return readBacktestOverview();
  });

  app.get<{ Params: { id: string }; Querystring: { capital?: string } }>(
    "/api/backtest/runs/:id",
    async (req, reply): Promise<BridgedResult<BacktestRunReport> | FastifyReply> => {
      if (!backtestOn(getFeatures())) return refuse(reply);
      if (!isBacktestRunId(req.params.id)) return reply.code(400).send({ error: "not a run id" });
      // The cards' account: whole dollars or nothing (the run's own peak risk). Anything else is a
      // 400 and runs nothing.
      const capital = req.query.capital;
      if (capital !== undefined && !isBacktestCapital(capital)) {
        return reply.code(400).send({ error: "capital must be whole dollars" });
      }
      return readBacktestRun(req.params.id, capital === undefined ? undefined : Number(capital));
    },
  );

  // The research queue's rows (orchestrator `queue-list`). Behind the same gate: its tasks are the
  // fetches and studies over the licensed history.
  app.get("/api/backtest/queue", async (_req, reply): Promise<BridgedResult<ResearchQueue> | FastifyReply> => {
    if (!backtestOn(getFeatures())) return refuse(reply);
    return readResearchQueue();
  });

  // B5's slice reports (`technicals slices` files). Behind the same gate: one may be built from a
  // licensed replay.
  app.get("/api/backtest/slices", async (_req, reply) => {
    if (!backtestOn(getFeatures()) || config === undefined) return refuse(reply);
    return readSlices(config);
  });
}
