import fs from "node:fs";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import type { StatusPayload, SourceFreshness } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { streamerFreshness } from "../readers/streamcache.js";

function fileFreshness(key: string, label: string, p: string): SourceFreshness {
  try {
    const st = fs.statSync(p);
    return {
      key,
      label,
      ageSeconds: Math.max(0, (Date.now() - st.mtimeMs) / 1000),
      present: true,
    };
  } catch {
    return { key, label, ageSeconds: null, present: false };
  }
}

export function registerStatusRoutes(
  app: FastifyInstance,
  config: ConsoleConfig,
  market?: { dxState: string },
): void {
  app.get("/api/status", async (): Promise<StatusPayload> => {
    const streamer = streamerFreshness(config);
    const sources: SourceFreshness[] = [
      streamer,
      fileFreshness("meic.paper", "MEIC paper", path.join(config.paths.meicDir, "paper_trades.db")),
      fileFreshness("flies.paper", "Flies paper", path.join(config.paths.fliesDir, "paper_trades.db")),
      fileFreshness("earnings.paper", "Earnings paper", path.join(config.paths.earningsDir, "paper_trades.db")),
      fileFreshness("gex", "GEX history", path.join(config.paths.gexDir, "gex_history.db")),
    ];
    const marketData =
      market?.dxState === "connected"
        ? "live"
        : streamer.present
          ? "cached"
          : "disconnected";
    const now = new Date();
    return {
      now: now.toISOString(),
      nowEt: now.toLocaleString("en-US", { timeZone: "America/New_York" }),
      marketData,
      sources,
    };
  });
}
