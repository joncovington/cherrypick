import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { loadConfig, BIND_HOST } from "./config.js";
import { registerStatusRoutes } from "./routes/status.js";
import { registerOverviewRoutes } from "./routes/overview.js";
import { registerModuleRoutes } from "./routes/modules.js";

const config = loadConfig();
const app = Fastify({ logger: { level: "info" } });

registerStatusRoutes(app, config);
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
