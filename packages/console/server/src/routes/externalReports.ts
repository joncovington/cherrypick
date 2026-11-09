import type { FastifyInstance } from "fastify";
import type { ExternalReportsPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readExternalReports } from "../readers/externalReports.js";

export function registerExternalReportsRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/external-reports", async (): Promise<ExternalReportsPayload> => readExternalReports(config));
}
