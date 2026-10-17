import type { FastifyInstance } from "fastify";
import type {
  EarningsCalendarPayload,
  EarningsRecordPayload,
  ScreenerFidelityPayload,
  ScreenerList,
  ScreenerPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readEarningsCalendar } from "../readers/earningsCalendar.js";
import { readEarningsRecord } from "../readers/earningsRecord.js";
import { readScreener, readScreenerFidelity, SCREENER_LISTS } from "../readers/screeners.js";

export function registerScreenerRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  // Registered before the list route, so `fidelity` and `earnings` are never read as list names.
  app.get("/api/screeners/fidelity", async (): Promise<ScreenerFidelityPayload> => readScreenerFidelity(config));
  app.get("/api/screeners/earnings-record", async (): Promise<EarningsRecordPayload> => readEarningsRecord(config));
  app.get<{ Querystring: { day?: string } }>(
    "/api/screeners/earnings",
    async (req): Promise<EarningsCalendarPayload> => readEarningsCalendar(config, req.query.day ?? null),
  );
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
