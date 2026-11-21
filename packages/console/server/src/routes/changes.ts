import type { FastifyInstance } from "fastify";
import { CHANGES_MODULES, isChangesModule } from "@console/shared";
import { readModuleChanges } from "../readers/changes.js";

/** `GET /api/changes/:module` — every modification under test in one module (`readers/changes.ts`). */
export function registerChangesRoutes(app: FastifyInstance): void {
  app.get<{ Params: { module: string } }>("/api/changes/:module", async (req, reply) => {
    const { module } = req.params;
    if (!isChangesModule(module)) return reply.code(404).send({ error: `module must be one of: ${CHANGES_MODULES.join(", ")}` });
    return readModuleChanges(module);
  });
}
