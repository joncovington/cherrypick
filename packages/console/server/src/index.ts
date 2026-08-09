import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import fastifyWebsocket from "@fastify/websocket";
import { loadConfig, BIND_HOST } from "./config.js";
import { registerStatusRoutes } from "./routes/status.js";
import { registerOverviewRoutes } from "./routes/overview.js";
import { registerModuleRoutes } from "./routes/modules.js";
import { MarketDataService } from "./market/marketData.js";
import { registerWsHub } from "./ws/hub.js";
import { registerSecurity } from "./security.js";
import { registerScoutRoutes } from "./routes/scout.js";
import { registerPayoffRoutes } from "./routes/payoff.js";
import { registerOrderRoutes } from "./routes/orders.js";
import { registerScreenerRoutes } from "./routes/screener.js";

const config = loadConfig();
const app = Fastify({ logger: { level: "info" } });
const market = new MarketDataService(config);

registerSecurity(app);
registerScoutRoutes(app, config, market);
registerPayoffRoutes(app);
registerOrderRoutes(app, config);
registerScreenerRoutes(app, config, market);
await app.register(fastifyWebsocket);
registerWsHub(app, market);
registerStatusRoutes(app, config, market);
registerOverviewRoutes(app, config);
registerModuleRoutes(app, config);
app.get("/api/health", async () => ({ ok: true }));

// Serve the built SPA when present (prod); in dev, Vite serves the frontend.
const here = path.dirname(fileURLToPath(import.meta.url));
const webDist = path.resolve(here, "..", "..", "web", "dist");
if (fs.existsSync(webDist)) {
  await app.register(fastifyStatic, { root: webDist });
  app.setNotFoundHandler((req, reply) => {
    if (req.raw.url?.startsWith("/api/")) {
      void reply.code(404).send({ error: "not found" });
    } else {
      void reply.sendFile("index.html");
    }
  });
}

try {
  await app.listen({ port: config.port, host: BIND_HOST });
  app.log.info(`console serving on http://${BIND_HOST}:${config.port}/`);
} catch (err) {
  app.log.error(err);
  process.exit(1);
}

// One-time scope backfill: a credential stored before scope detection existed
// (or migrated from the pre-unification slot) gets probed once in the
// background so read-only gating applies without re-entering the secret.
void (async () => {
  const { loadCredentials, saveCredentials } = await import("./auth/credentials.js");
  const creds = loadCredentials();
  if (creds === null || creds.scope !== undefined) return;
  try {
    const { probeCredentials } = await import("./auth/probe.js");
    const probe = await probeCredentials(creds);
    if (probe.ok && probe.scope !== undefined) {
      saveCredentials({ ...creds, scope: probe.scope, validatedAt: new Date().toISOString() });
      app.log.info(`credential scope detected: ${probe.scope}${probe.scope === "read" ? " — write-oriented functions disabled" : ""}`);
    }
  } catch (err) {
    app.log.warn(`credential scope probe failed: ${(err as Error).message}`);
  }
})();
