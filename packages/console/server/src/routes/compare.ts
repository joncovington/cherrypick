import type { FastifyInstance } from "fastify";
import { COMPARE_SCOPES, isCompareScope, type ComparePayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readCompare } from "../services/compareBridge.js";

/**
 * `GET /api/compare?scope=era|all` — every arm beside its control (`services/compareBridge.ts`).
 *
 * `era` (each module's own evidence window) is the default; `all` is the stated widening. Any other
 * scope is refused with 400 rather than quietly read as the default: a mistyped `?scope=al` that
 * came back as the era would be read as the whole record.
 */
export function registerCompareRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { scope?: string } }>("/api/compare", async (req, reply) => {
    const raw = req.query.scope ?? "era";
    if (!isCompareScope(raw)) {
      return reply.code(400).send({ error: `scope must be one of: ${COMPARE_SCOPES.join(", ")}` });
    }
    const payload: ComparePayload = readCompare(config, raw);
    return payload;
  });
}
