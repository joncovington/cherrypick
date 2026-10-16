import type { FastifyInstance } from "fastify";
import type { ScreenerFidelityPayload, ScreenerList, ScreenerPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readScreener, readScreenerFidelity, SCREENER_LISTS } from "../readers/screeners.js";

export function registerScreenerRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  // Registered before the list route, so `fidelity` is never read as a list name.
  app.get("/api/screeners/fidelity", async (): Promise<ScreenerFidelityPayload> => readScreenerFidelity(config));
  app.get<{ Params: { list: string }; Querystring: { day?: string } }>(
    "/api/screeners/:list",
    async (req, reply): Promise<ScreenerPayload | undefined> => {
      const which = req.params.list as ScreenerList;
      if (!SCREENER_LISTS.includes(which)) {
        await reply.code(404).send({ error: `no screener list named ${req.params.list}` });
        return undefined;
      }
      return readScreener(config, which, req.query.day ?? null);
    },
  );
}
