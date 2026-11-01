import type { FastifyInstance, FastifyReply } from "fastify";
import {
  isBacktestArm,
  isBacktestCapital,
  isBacktestPosition,
  isBacktestRiskPct,
  isBacktestRiskUnit,
  isBacktestRunId,
  isBacktestSession,
  type BacktestOverview,
  type BacktestRunReport,
  type BacktestTradeLog,
  type BacktestTradePath,
  type BridgedResult,
  type ResearchQueue,
  type StudiesBoard,
} from "@console/shared";
import { backtestOn, getFeatures } from "../services/featuresBridge.js";
import { readBacktestOverview, readBacktestPath, readBacktestRun, readBacktestTrades } from "../services/backtestBridge.js";
import { readResearchQueue } from "../services/researchQueueBridge.js";
import { readStudies } from "../services/studiesBridge.js";
import type { ConsoleConfig } from "../config.js";
import { readSlices } from "../readers/slices.js";
import { readQueueLog } from "../readers/logs.js";

/**
 * `GET /api/backtest/overview`, `GET /api/backtest/runs/:id`, `GET /api/backtest/runs/:id/trades`,
 * `GET /api/backtest/runs/:id/path`, `GET /api/backtest/queue` and `GET /api/backtest/studies` — the
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

  app.get<{ Params: { id: string }; Querystring: { capital?: string; risk?: string; unit?: string } }>(
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
      // The compounded lens's risk per session, in percent, or nothing (the package's default).
      const risk = req.query.risk;
      if (risk !== undefined && !isBacktestRiskPct(risk)) {
        return reply.code(400).send({ error: "risk must be a percent above 0 and at most 100" });
      }
      // The lens's risk unit: one of the package's declared names, or nothing (max loss).
      const unit = req.query.unit;
      if (unit !== undefined && !isBacktestRiskUnit(unit)) {
        return reply.code(400).send({ error: "unit must be max_loss or stop" });
      }
      return readBacktestRun(
        req.params.id,
        capital === undefined ? undefined : Number(capital),
        risk === undefined ? undefined : Number(risk),
        unit,
      );
    },
  );

  // One arm's trade log, asked for when it is opened. Both the id and the arm are checked for shape
  // before anything is spawned; the module refuses an arm the run does not have.
  app.get<{ Params: { id: string }; Querystring: { arm?: string } }>(
    "/api/backtest/runs/:id/trades",
    async (req, reply): Promise<BridgedResult<BacktestTradeLog> | FastifyReply> => {
      if (!backtestOn(getFeatures())) return refuse(reply);
      if (!isBacktestRunId(req.params.id)) return reply.code(400).send({ error: "not a run id" });
      const arm = req.query.arm;
      if (arm === undefined || !isBacktestArm(arm)) return reply.code(400).send({ error: "not an arm name" });
      return readBacktestTrades(req.params.id, arm);
    },
  );

  // One trade's path through its session (MEIC's condors), asked for when a trade is opened. Every
  // argument is checked for shape before anything is spawned; the module refuses one it cannot find.
  app.get<{ Params: { id: string }; Querystring: { arm?: string; session?: string; position?: string } }>(
    "/api/backtest/runs/:id/path",
    async (req, reply): Promise<BridgedResult<BacktestTradePath> | FastifyReply> => {
      if (!backtestOn(getFeatures())) return refuse(reply);
      if (!isBacktestRunId(req.params.id)) return reply.code(400).send({ error: "not a run id" });
      const { arm, session, position } = req.query;
      if (arm === undefined || !isBacktestArm(arm)) return reply.code(400).send({ error: "not an arm name" });
      if (session === undefined || !isBacktestSession(session)) return reply.code(400).send({ error: "not a session date" });
      if (position === undefined || !isBacktestPosition(position)) return reply.code(400).send({ error: "not a position id" });
      return readBacktestPath(req.params.id, arm, session, position);
    },
  );

  // The research queue's rows (orchestrator `queue-list`). Behind the same gate: its tasks are the
  // fetches and studies over the licensed history.
  app.get("/api/backtest/queue", async (_req, reply): Promise<BridgedResult<ResearchQueue> | FastifyReply> => {
    if (!backtestOn(getFeatures())) return refuse(reply);
    return readResearchQueue();
  });

  // The studies board (orchestrator `hypotheses-board`). Behind the same gate: its verdicts are read
  // from licensed result files.
  app.get("/api/backtest/studies", async (_req, reply): Promise<BridgedResult<StudiesBoard> | FastifyReply> => {
    if (!backtestOn(getFeatures())) return refuse(reply);
    return readStudies();
  });

  // One task's log tail, for its expanded row. The id is a positive integer and the path is built
  // from it alone, so nothing outside `logs/research-queue/` can be named.
  app.get<{ Params: { id: string } }>("/api/backtest/queue/:id/log", async (req, reply) => {
    if (!backtestOn(getFeatures()) || config === undefined) return refuse(reply);
    if (!/^[1-9]\d{0,8}$/.test(req.params.id)) return reply.code(400).send({ error: "task id must be a positive integer" });
    const result: BridgedResult<{ lines: string[] }> = {
      ok: true,
      data: { lines: readQueueLog(config, Number(req.params.id)) },
      error: null,
    };
    return result;
  });

  // B5's slice reports (`technicals slices` files). Behind the same gate: one may be built from a
  // licensed replay.
  app.get("/api/backtest/slices", async (_req, reply) => {
    if (!backtestOn(getFeatures()) || config === undefined) return refuse(reply);
    return readSlices(config);
  });
}
