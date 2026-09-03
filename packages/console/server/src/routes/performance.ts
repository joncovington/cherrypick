import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { MODULE_SCHEMA, readModulePerformance, type PerformanceModuleId } from "../readers/performance.js";

function isPerformanceModule(v: string): v is PerformanceModuleId {
  return Object.prototype.hasOwnProperty.call(MODULE_SCHEMA, v);
}

export function registerPerformanceRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Params: { module: string }; Querystring: { era?: string } }>(
    "/api/performance/:module",
    async (req, reply) => {
      const { module } = req.params;
      if (!isPerformanceModule(module)) {
        reply.code(404);
        return { ok: false, error: `unknown module ${JSON.stringify(module)}` };
      }
      const era = req.query.era === "ALL" ? "ALL" : "current";
      return readModulePerformance(config, module, era);
    },
  );
}
