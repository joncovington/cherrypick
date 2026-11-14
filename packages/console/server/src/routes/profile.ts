import type { FastifyInstance } from "fastify";
import type { ProfileBarsPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { isProfileSymbol, readProfileBars, sessionCount } from "../readers/profileBars.js";

/** The recorded profile bars (Charts → profile, and the intraday chart's untested POCs). An unknown
 *  symbol is refused before the store is opened; `hours=extended` adds the bars outside the session. */
export function registerProfileRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { symbol?: string; sessions?: string; hours?: string } }>(
    "/api/charts/profile-bars",
    async (req, reply): Promise<ProfileBarsPayload | { error: string }> => {
      const symbol = req.query.symbol ?? "SPX";
      if (!isProfileSymbol(symbol)) {
        reply.code(400);
        return { error: `unknown profile symbol: ${symbol}` };
      }
      const hours = req.query.hours === "extended" ? "extended" : "regular";
      return readProfileBars(config, symbol, sessionCount(req.query.sessions), hours);
    },
  );
}
