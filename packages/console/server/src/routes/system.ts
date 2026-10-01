import fs from "node:fs";
import type { FastifyInstance } from "fastify";
import type {
  SystemDataPayload,
  SystemEnvironmentPayload,
  SystemHealthPayload,
  SystemLogSource,
  SystemModulesPayload,
  SystemSupervisorPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readSystemHealth, readSystemModules, readSystemSupervisor } from "../readers/system.js";
import { readSystemData, readSystemEnvironment } from "../readers/systemEnv.js";
import { logSources } from "../readers/logs.js";

/** The System page: one route per tab, so a tab's poll pays only for its own reads. */
export function registerSystemRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/system/health", async (): Promise<SystemHealthPayload> => readSystemHealth(config));
  app.get("/api/system/supervisor", async (): Promise<SystemSupervisorPayload> => readSystemSupervisor(config));
  app.get("/api/system/modules", async (): Promise<SystemModulesPayload> => readSystemModules(config));
  app.get("/api/system/data", async (): Promise<SystemDataPayload> => readSystemData(config));
  app.get("/api/system/environment", async (): Promise<SystemEnvironmentPayload> => readSystemEnvironment(config));
  app.get("/api/system/log-sources", async (): Promise<{ sources: SystemLogSource[] }> => ({
    sources: logSources(config).map((s) => ({ id: s.id, path: s.path, exists: fs.existsSync(s.path) })),
  }));
}
