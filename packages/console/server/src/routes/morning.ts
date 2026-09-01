import type { FastifyInstance } from "fastify";
import type { MorningPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readMorning } from "../readers/overview.js";

export function registerMorningRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/morning",
    async (req): Promise<MorningPayload> => readMorning(config, req.query.session),
  );
}
