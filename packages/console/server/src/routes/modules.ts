import type { FastifyInstance } from "fastify";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readMeicForest } from "../readers/meic.js";
import { readEntryAttempts } from "../readers/attempts.js";
import { readFliesArmGuide, readMeicProfileGuide } from "../readers/experimentGuide.js";
import { readOccupancy } from "../readers/occupancy.js";
import {
  readMeic,
  readMeicAnalytics,
  readMeicDeepAnalytics,
  readMeicPerformance,
  readMeicScope,
  readMeicLoopStatus,
  type MeicScopeFilter,
  type MeicTradeQuery,
} from "../readers/meic.js";
import { parsePage } from "../readers/paging.js";
import {
  readFlies,
  readFliesAnalytics,
  readFliesForest,
  readFliesMeta,
  readFliesTimeline,
  readFliesHistory,
  readFliesPerformance,
  readFliesJournal,
  readArmDivergence,
  readFliesTradeLog,
  type FliesFilter,
} from "../readers/flies.js";
import { readEarnings, readSymbolWatch, readEarningsAnalytics, readEarningsDetail } from "../readers/earnings.js";
import { readEarningsLive } from "../readers/earningsLive.js";
import { readGex } from "../readers/gex.js";
import { buildGexProfile, gexSymbols } from "../services/gexProfile.js";
import { buildSuiteReport } from "../services/report.js";
import { readLogTail } from "../readers/logs.js";
import { buildCalibration } from "../services/calibrate.js";
import { readSystemPanel, readEod, renderReport } from "../services/suite.js";

function parseMode(q: unknown): TradingMode {
  const mode = (q as Record<string, unknown> | undefined)?.["mode"];
  return mode === "live" ? "live" : "paper";
}

export function registerModuleRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  const parseMeicScope = (q: unknown): MeicScopeFilter => {
    const query = (q ?? {}) as Record<string, unknown>;
    const pick = (k: string): string | null => {
      const v = query[k];
      if (typeof v !== "string" || v === "" || v.length > 40) return null;
      // For symbol and profile "ALL" means unfiltered, so it drops to null. For
      // era it is a real selection — every era at once — and must survive.
      if (v === "ALL") return k === "era" ? "ALL" : null;
      return v;
    };
    return { symbol: pick("symbol"), profile: pick("profile"), era: pick("era") };
  };
  const parseMeicTradeQuery = (q: unknown): MeicTradeQuery => {
    const query = (q ?? {}) as Record<string, unknown>;
    const text = (k: string, max: number): string => {
      const v = query[k];
      return typeof v === "string" ? v.slice(0, max) : "";
    };
    const int = (k: string, fallback: number): number => {
      const n = Number(query[k]);
      return Number.isFinite(n) ? Math.trunc(n) : fallback;
    };
    const outcome = text("outcome", 10);
    const reason = text("reason", 60);
    return {
      ...parseMeicScope(query),
      outcome: outcome === "wins" || outcome === "losses" || outcome === "open" ? outcome : "all",
      reason: reason === "" ? null : reason,
      search: text("search", 60),
      limit: int("limit", 100),
      offset: int("offset", 0),
    };
  };
  app.get("/api/meic", async (req) => readMeic(config, parseMode(req.query), parseMeicTradeQuery(req.query)));
  app.get("/api/meic/analytics", async (req) =>
    readMeicAnalytics(config, parseMode(req.query), parseMeicScope(req.query)),
  );
  app.get("/api/meic/deep", async (req) =>
    readMeicDeepAnalytics(config, parseMode(req.query), parseMeicScope(req.query)),
  );
  app.get("/api/meic/scope", async (req) => readMeicScope(config, parseMode(req.query), parseMeicScope(req.query).era));
  app.get("/api/meic/forest", async (req) => {
    const f = parseFliesFilter(req.query);
    return readMeicForest(config, parseMode(req.query), f.date);
  });
  app.get("/api/meic/attempts", async (req) => {
    const f = parseFliesFilter(req.query);
    return readEntryAttempts(config, "meic", parseMode(req.query), f.date);
  });
  app.get("/api/meic/occupancy", async (req) => {
    const f = parseFliesFilter(req.query);
    return readOccupancy(config, "meic", parseMode(req.query), f.date);
  });
  app.get("/api/flies/occupancy", async (req) => {
    const f = parseFliesFilter(req.query);
    return readOccupancy(config, "flies", parseMode(req.query), f.date);
  });
  app.get("/api/flies/attempts", async (req) => {
    const f = parseFliesFilter(req.query);
    return readEntryAttempts(config, "flies", parseMode(req.query), f.date);
  });
  app.get("/api/meic/loop", async (req) =>
    readMeicLoopStatus(config, parseMode(req.query), parseMeicScope(req.query)),
  );
  app.get("/api/meic/performance", async (req) => {
    const q = req.query as Record<string, unknown>;
    const gran = ["daily", "weekly", "monthly"].includes(String(q["granularity"])) ? String(q["granularity"]) : "daily";
    const scope = parseMeicScope(req.query);
    return readMeicPerformance(config, parseMode(req.query), gran, scope.symbol, scope.profile, scope.era);
  });
  const parseFliesFilter = (q: unknown): FliesFilter => {
    const query = (q ?? {}) as Record<string, unknown>;
    const date = typeof query["date"] === "string" && /^\d{4}-\d{2}-\d{2}$/.test(query["date"]) ? query["date"] : null;
    const arm = typeof query["arm"] === "string" && query["arm"] !== "" && query["arm"].length <= 40 ? query["arm"] : null;
    const era = query["era"] === "ALL" ? "ALL" : null;
    const symbol =
      typeof query["symbol"] === "string" && query["symbol"] !== "" && query["symbol"].length <= 12
        ? query["symbol"]
        : null;
    return { arm, date, symbol, era };
  };
  app.get("/api/flies", async (req) =>
    readFlies(config, parseMode(req.query), parseFliesFilter(req.query), {
      books: parsePage(req.query, "books"),
      positions: parsePage(req.query, "positions"),
    }),
  );
  app.get("/api/flies/tradelog", async (req) => {
    const q = (req.query ?? {}) as Record<string, unknown>;
    const outcome = typeof q["outcome"] === "string" ? q["outcome"] : "all";
    return readFliesTradeLog(config, parseMode(req.query), {
      ...parsePage(req.query),
      outcome:
        outcome === "wins" || outcome === "losses" || outcome === "pinned" || outcome === "risk-free"
          ? outcome
          : "all",
      search: typeof q["search"] === "string" ? q["search"].slice(0, 60) : "",
    });
  });
  // The experiment guides: what each arm/profile is and how it got there. Config + ledger only, so
  // they cost nothing and never need the market.
  app.get("/api/flies/arms", async (req) => readFliesArmGuide(config, parseMode(req.query)));
  app.get("/api/meic/profiles", async (req) => readMeicProfileGuide(config, parseMode(req.query)));

  app.get("/api/flies/analytics", async (req) =>
    readFliesAnalytics(config, parseMode(req.query), parseFliesFilter(req.query)),
  );
  app.get("/api/flies/meta", async (req) => readFliesMeta(config, parseMode(req.query), parseFliesFilter(req.query).era));
  app.get("/api/flies/history", async (req) => readFliesHistory(config, parseMode(req.query), parseFliesFilter(req.query)));
  app.get("/api/flies/divergence", async (req) =>
    readArmDivergence(config, parseMode(req.query), parseFliesFilter(req.query).date),
  );
  app.get("/api/flies/journal", async (req) => {
    const f = parseFliesFilter(req.query);
    return readFliesJournal(config, parseMode(req.query), f.date, f.arm);
  });
  app.get("/api/flies/performance", async (req) => {
    const q = req.query as Record<string, unknown>;
    const gran = ["daily", "weekly", "monthly"].includes(String(q["granularity"])) ? String(q["granularity"]) : "daily";
    return readFliesPerformance(config, parseMode(req.query), gran, parseFliesFilter(req.query));
  });
  app.get("/api/flies/timeline", async (req) => {
    const f = parseFliesFilter(req.query);
    return readFliesTimeline(config, parseMode(req.query), f.date);
  });
  app.get("/api/flies/forest", async (req) => {
    const f = parseFliesFilter(req.query);
    return readFliesForest(config, parseMode(req.query), f.date, f.arm);
  });
  app.get("/api/earnings", async (req) =>
    readEarnings(config, { trades: parsePage(req.query, "trades"), reviews: parsePage(req.query, "reviews") }),
  );
  app.get("/api/earnings/upcoming", async () => readSymbolWatch(config));
  app.get("/api/earnings/analytics", async (req) => readEarningsAnalytics(config, parseMode(req.query)));
  app.get("/api/earnings/detail", async (req) => readEarningsDetail(config, parseMode(req.query)));
  // Open positions as the managed loop sees them: latest mark, and whether the loop is alive.
  app.get("/api/earnings/live", async () => readEarningsLive(config));
  app.get("/api/gex", async () => readGex(config));
  app.get("/api/gex/symbols", async () => ({ symbols: gexSymbols(config) }));
  app.get("/api/gex/profile/:symbol", async (req) => {
    const { symbol } = req.params as { symbol: string };
    return buildGexProfile(config, symbol.toUpperCase());
  });
  app.get("/api/report", async () => buildSuiteReport(config));
  app.get("/api/logs", async () => ({ lines: readLogTail(config) }));
  app.get("/api/calibration", async () => ({ modules: buildCalibration(config) }));
  app.get("/api/system", async () => readSystemPanel(config));
  app.get("/api/eod", async () => readEod(config));
  app.get("/api/eod/report", async (req, reply) => {
    // Only files the EOD card itself listed can be rendered — no arbitrary reads.
    const { file } = req.query as { file?: string };
    const allowed = new Set(readEod(config).reports.filter((r) => r.exists).map((r) => r.file));
    if (file === undefined || !allowed.has(file)) return reply.code(404).send({ error: "unknown report" });
    const html = renderReport(file);
    if (html === null) return reply.code(404).send({ error: "unreadable" });
    return { html };
  });
}
