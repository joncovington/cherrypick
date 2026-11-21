import type { FastifyInstance } from "fastify";
import { AHEAD_MODULES, isAheadModule } from "@console/shared";
import { readSessionsAhead } from "../readers/ahead.js";

/** `GET /api/ahead/:module?arm=` — one arm's next sessions and what its dated gates do (`readers/ahead.ts`). */
export function registerAheadRoutes(app: FastifyInstance): void {
  app.get<{ Params: { module: string }; Querystring: { arm?: string } }>("/api/ahead/:module", async (req, reply) => {
    const { module } = req.params;
    if (!isAheadModule(module)) return reply.code(404).send({ error: `module must be one of: ${AHEAD_MODULES.join(", ")}` });
    const arm = String(req.query?.arm ?? "");
    if (!/^[A-Za-z0-9][A-Za-z0-9:_.-]{0,79}$/.test(arm)) return reply.code(400).send({ error: "arm: an arm's name" });
    return readSessionsAhead(module, arm);
  });
}
