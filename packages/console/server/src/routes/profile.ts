import type { FastifyInstance } from "fastify";
import type { ProfileBarsPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { isProfileSymbol, readProfileBars, sessionCount } from "../readers/profileBars.js";

/** The Charts → profile tab's bars. An unknown symbol is refused before the store is opened. */
export function registerProfileRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { symbol?: string; sessions?: string } }>(
    "/api/charts/profile-bars",
    async (req, reply): Promise<ProfileBarsPayload | { error: string }> => {
      const symbol = req.query.symbol ?? "SPX";
      if (!isProfileSymbol(symbol)) {
        reply.code(400);
        return { error: `unknown profile symbol: ${symbol}` };
      }
      return readProfileBars(config, symbol, sessionCount(req.query.sessions));
    },
  );
}
