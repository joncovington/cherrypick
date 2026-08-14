import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { readAdvisor } from "../readers/advisor.js";
import { callAdvisorCli } from "../services/advisorBridge.js";

/**
 * The Advisor page's two write actions — and there are exactly two, both of which only ever make
 * the advisor do LESS.
 *
 *  - **Kill an experiment.** It stops issuing tonight, the reason is journaled, and a queued
 *    experiment takes its slot.
 *  - **Dismiss a proposal.** It stays in the record and travels in the next deep pack's journal,
 *    which is the point: a dismissal the model cannot see gets re-proposed next week.
 *
 * Neither holds any logic here. Both invoke the advisor's own CLI as a subprocess, the same
 * invoke-the-owning-package's-editor shape the Config page uses — the lifecycle lives in one place
 * in Python, and the scheduled runs and the browser go through the same door.
 *
 * There is deliberately no way to START, TUNE or ENACT anything from the browser. Those are the
 * directions that add exposure, and they belong to the scheduled deterministic path where they can
 * be validated against bounds rather than to a button.
 *
 * Gating is the standard mutating-surface posture from `security.ts` (loopback Host, CSRF, JSON).
 */

export function registerAdvisorOpsRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.post<{ Params: { id: string } }>("/api/advisor/experiments/:id/kill", async (req, reply) => {
    const result = callAdvisorCli({ op: "kill", experimentId: req.params.id });
    if (!result.ok) {
      return reply.code(result.code === "not_found" ? 404 : 503).send({ error: result.error });
    }
    app.log.info(`advisor experiment ${req.params.id} killed from the console`);
    return { ...readAdvisor(config), killed: req.params.id, activated: result["activated"] ?? [] };
  });

  app.post<{ Params: { id: string } }>("/api/advisor/proposals/:id/dismiss", async (req, reply) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return reply.code(400).send({ error: "proposal id must be an integer" });

    const result = callAdvisorCli({ op: "dismiss", proposalId: id });
    if (!result.ok) {
      return reply.code(result.code === "not_found" ? 404 : 503).send({ error: result.error });
    }
    app.log.info(`advisor proposal ${String(id)} dismissed from the console`);
    return { ...readAdvisor(config), dismissed: id };
  });
}
