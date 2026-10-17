import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { MODULE_SCHEMA, readModulePerformance, type PerformanceModuleId } from "../readers/performance.js";
import { readArmsRisk, readModuleAnalysis } from "../readers/analysis.js";
import { readPnlCalendar, readPnlExcursion, readPnlTrace, readPnlTrades } from "../readers/pnlCalendar.js";

function isPerformanceModule(v: string): v is PerformanceModuleId {
  return Object.prototype.hasOwnProperty.call(MODULE_SCHEMA, v);
}

type ModuleQuery = { Params: { module: string }; Querystring: { era?: string; mode?: string } };

export function registerPerformanceRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<ModuleQuery>("/api/performance/:module", async (req, reply) => {
    const { module } = req.params;
    if (!isPerformanceModule(module)) {
      reply.code(404);
      return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
    }
    const era = req.query.era === "ALL" ? "ALL" : "current";
    const mode = req.query.mode === "live" ? "live" : "paper";
    return readModulePerformance(config, module, era, mode);
  });

  // Every arm's net per session, for the Compare page's calendar tab (core.metrics' session nets).
  app.get<{ Querystring: { mode?: string } }>("/api/calendar", async (req) => {
    const mode = req.query.mode === "live" ? "live" : "paper";
    return readPnlCalendar(config, mode);
  });

  // One arm's trades on one session: the calendar's day drill.
  app.get<{ Querystring: { module?: string; session?: string; tag?: string; mode?: string } }>(
    "/api/calendar/trades",
    async (req, reply) => {
      const { module, session, tag } = req.query;
      if (module === undefined || !isPerformanceModule(module)) {
        reply.code(404);
        return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
      }
      if (session === undefined || !/^\d{4}-\d{2}-\d{2}$/.test(session) || !tag) {
        reply.code(400);
        return { ok: false, error: "session (YYYY-MM-DD) and tag are required" };
      }
      const mode = req.query.mode === "live" ? "live" : "paper";
      return readPnlTrades(config, module, session, tag, mode);
    },
  );

  // One closed position's excursion: a trade row in the calendar's drill.
  app.get<{ Querystring: { module?: string; position?: string; mode?: string } }>(
    "/api/calendar/excursion",
    async (req, reply) => {
      const { module, position } = req.query;
      if (module === undefined || !isPerformanceModule(module)) {
        reply.code(404);
        return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
      }
      if (!position) {
        reply.code(400);
        return { ok: false, error: "position is required" };
      }
      const mode = req.query.mode === "live" ? "live" : "paper";
      return readPnlExcursion(config, module, position, mode);
    },
  );

  // One closed position's entry and management trace: beside its excursion in the calendar drill.
  app.get<{ Querystring: { module?: string; position?: string; mode?: string } }>(
    "/api/calendar/trace",
    async (req, reply) => {
      const { module, position } = req.query;
      if (module === undefined || !isPerformanceModule(module)) {
        reply.code(404);
        return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
      }
      if (!position) {
        reply.code(400);
        return { ok: false, error: "position is required" };
      }
      const mode = req.query.mode === "live" ? "live" : "paper";
      return readPnlTrace(config, module, position, mode);
    },
  );

  // Every arm of every module, for the Compare page's risk tab: picked from each module's analysis.
  app.get<{ Querystring: { mode?: string } }>("/api/analysis", async (req) => {
    const mode = req.query.mode === "live" ? "live" : "paper";
    return readArmsRisk(config, mode);
  });

  // The per-arm strategy analysis (docs/strategy-analysis-plan.md), over the same modules, window
  // and ledger as the performance read above.
  app.get<ModuleQuery>("/api/analysis/:module", async (req, reply) => {
    const { module } = req.params;
    if (!isPerformanceModule(module)) {
      reply.code(404);
      return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
    }
    const era = req.query.era === "ALL" ? "ALL" : "current";
    const mode = req.query.mode === "live" ? "live" : "paper";
    return readModuleAnalysis(config, module, era, mode);
  });
}
