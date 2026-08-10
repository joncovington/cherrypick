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
import { registerTtWatchlistRoutes } from "./routes/ttWatchlists.js";
import { startChainEodScheduler } from "./services/chainEod.js";

const config = loadConfig();
const app = Fastify({ logger: { level: "info" } });
const market = new MarketDataService(config);

registerSecurity(app);
registerScoutRoutes(app, config, market);
registerPayoffRoutes(app);
registerOrderRoutes(app, config);
registerScreenerRoutes(app, config, market);
registerTtWatchlistRoutes(app, config, market);
await app.register(fastifyWebsocket);
registerWsHub(app, market);
registerStatusRoutes(app, config, market);
registerOverviewRoutes(app, config);
registerModuleRoutes(app, config);
app.get("/api/health", async () => ({ ok: true }));

// Daily EOD chain snapshot (~15:30 ET weekdays) on the console's own session.
startChainEodScheduler(config, market, (msg) => app.log.info(msg));

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

// Scope detection at boot: the suite credential is probed once per process
// (never persisted, so a rotated refresh token can't carry a stale scope).
// A read-only result disables write-oriented functions everywhere.
void (async () => {
  const { loadCredentials, setScope } = await import("./auth/credentials.js");
  const creds = loadCredentials();
  if (creds === null) {
    app.log.warn("no suite broker credential found — market data and dry-run validation unavailable");
    return;
  }
  try {
    const { probeCredentials } = await import("./auth/probe.js");
    const probe = await probeCredentials(creds);
    if (probe.ok && probe.scope !== undefined) {
      setScope(probe.scope);
      app.log.info(
        `suite credential (${creds.source}) scope: ${probe.scope}${probe.scope === "read" ? " — write-oriented functions disabled" : ""}`,
      );
    } else {
      app.log.warn(`credential probe failed: ${probe.error ?? "unknown"}`);
    }
  } catch (err) {
    app.log.warn(`credential scope probe failed: ${(err as Error).message}`);
  }
})();
