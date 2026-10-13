import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import {
  readFliesGateSessions,
  readFliesLiveGap,
  readFliesSelector,
} from "../services/fliesEvidenceBridge.js";

/**
 * The flies evidence pages' three reads (2026-10-12), each the module's own answer through
 * `services/fliesEvidenceBridge.ts`. Read-only; nothing here touches an order.
 *
 * - `/api/flies/live-gap` -- live vs paper, paired per session (the `livegap` tab).
 * - `/api/flies/gates` -- each gate arm against control over its own window (the `gates` tab,
 *   which replaced the retired GEX-centre divergence card).
 * - `/api/flies/selector` -- the selector arm's sessions and its models' intervals (`selector`).
 */
export function registerFliesEvidenceRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/flies/live-gap", async () => readFliesLiveGap(config));
  app.get("/api/flies/gates", async () => readFliesGateSessions(config));
  app.get("/api/flies/selector", async () => readFliesSelector(config));
}
