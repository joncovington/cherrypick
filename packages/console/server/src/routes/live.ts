import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import { readFliesLive } from "../readers/fliesLive.js";
import { readMeicLive } from "../readers/meicLive.js";
import { readLiveToday } from "../readers/liveToday.js";

/**
 * The Live page's endpoint (2026-09-17). Read-only: the flies live pilot's day, composed in
 * `readers/fliesLive.ts`. `?session=YYYY-MM-DD` reads a past session's rows (the ledger keeps
 * them); default is today's ET session. There is no write here and nothing that touches an order.
 */
export function registerLiveRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>("/api/live/flies", async (req, reply) => {
    const session = req.query.session;
    if (session !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(session)) {
      reply.code(400);
      return { ok: false, error: "session must be YYYY-MM-DD" };
    }
    return readFliesLive(config, session ?? null);
  });
  // MEIC's live loop (the Live page's meic tab): arming, halt, orphans, held submissions, open ICs
  // and resolved history, from files only (`readers/meicLive.ts`). Nothing here touches an order.
  app.get("/api/live/meic", async () => readMeicLive(config));
  // Every live loop's day on one payload (the Live page's today tab), composed from the per-loop
  // readers above plus bwb's (`readers/liveToday.ts`). Nothing here touches an order.
  app.get("/api/live/today", async () => readLiveToday(config));
}
