import type { FastifyInstance } from "fastify";
import type { FuturesTickerPayload, MorningPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readMorning } from "../readers/overview.js";
import { readFuturesTicker } from "../readers/futures.js";

export function registerMorningRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/morning",
    async (req): Promise<MorningPayload> => readMorning(config, req.query.session),
  );
  // Which contract each ticker product is today. The prices themselves ride the quote socket.
  app.get("/api/futures-ticker", async (): Promise<FuturesTickerPayload> => readFuturesTicker(config));
}
