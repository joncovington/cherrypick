import fs from "node:fs";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import type { StatusPayload, SourceFreshness } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { streamerFreshness } from "../readers/streamcache.js";
import { getScope } from "../auth/credentials.js";

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
      fileFreshness("calendars.paper", "Calendars paper", path.join(config.paths.calendarsDir, "paper_trades.db")),
      fileFreshness("pmcc.paper", "PMCC-99 paper", path.join(config.paths.pmccDir, "paper_trades.db")),
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
      dxlink: (market?.dxState as "disconnected" | "connecting" | "connected" | "error" | undefined) ?? "disconnected",
      credentialScope: getScope().scope,
      sources,
    };
  });

  // The candle-warm and EOD-chain collectors went with the research section on 2026-08-31, and
  // they were everything this reported beyond the DXLink state. The socket already carries its own
  // status heartbeat, so nothing was left for a banner to say.
}
