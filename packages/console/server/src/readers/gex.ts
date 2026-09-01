import path from "node:path";
import type { GexPayload, GexRegimeRow, Paged } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";
import { emptyPage, FIRST_PAGE, pagedQuery, type PageRequest } from "./paging.js";

function isoTs(v: unknown): string {
  // The recorder stores ts as epoch seconds (sometimes as a string).
  const n = typeof v === "number" ? v : typeof v === "string" ? Number.parseFloat(v) : NaN;
  if (Number.isFinite(n) && n > 1e9) return new Date(n * 1000).toISOString();
  return typeof v === "string" ? v : "";
}

function toRow(r: Record<string, unknown>): GexRegimeRow {
  return {
    symbol: str(r["symbol"]) ?? "",
    tradeDate: str(r["trade_date"]) ?? "",
    ts: isoTs(r["ts"]),
    spot: num(r["spot"]),
    netGex: num(r["net_gex"]),
    netGexVol: num(r["net_gex_vol"]),
    zeroGamma: num(r["zero_gamma"]),
    callWall: num(r["call_wall"]),
    putWall: num(r["put_wall"]),
  };
}

export function readGex(config: ConsoleConfig, recentPage: PageRequest = FIRST_PAGE): GexPayload {
  const dbPath = path.join(config.paths.gexDir, "gex_history.db");

  const latest = withReadOnlyDb<GexRegimeRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT g.* FROM gex_regime_history g
          JOIN (SELECT symbol, MAX(ts) AS max_ts FROM gex_regime_history GROUP BY symbol) m
            ON g.symbol = m.symbol AND g.ts = m.max_ts
          ORDER BY g.symbol`,
      )
      .all()
      .map(toRow),
  );

  // Paged rather than capped. The old query carried a bare `LIMIT 60` while a session records
  // 240-288 rows, so the table showed about a fifth of the day and reported no total -- the reader
  // had no way to tell a quiet session from a truncated one.
  const recent = withReadOnlyDb<Paged<GexRegimeRow>>(dbPath, emptyPage<GexRegimeRow>(recentPage), (db) =>
    pagedQuery<GexRegimeRow>(
      db,
      {
        columns: "*",
        from: "gex_regime_history",
        where: "trade_date = (SELECT MAX(trade_date) FROM gex_regime_history)",
        params: [],
        orderBy: "ts DESC",
      },
      recentPage,
      toRow,
    ),
  );

  // There is no measurement-breaks table here; what makes a GEX number untrustworthy is staleness
  // and truncation instead. A flip read is a claim about NOW, and the `daily_closes` series is the
  // suite's only multi-year one -- SPX's froze silently for 22 sessions in 2026-07/08 while every
  // other symbol stayed current, and nothing on any page would have shown it.
  const integrity = withReadOnlyDb<GexPayload["integrity"]>(
    dbPath,
    { latest: [], sessionDate: null, sessionRows: 0, closeSeries: [] },
    (db) => {
      const now = Date.now() / 1000;
      const session = db
        .prepare<[], Record<string, unknown>>(
          `SELECT trade_date AS d, COUNT(*) AS n FROM gex_regime_history
           WHERE trade_date = (SELECT MAX(trade_date) FROM gex_regime_history) GROUP BY trade_date`,
        )
        .get();
      // Age only the symbols the recorder is STILL writing, derived from the latest session rather
      // than from a config this package does not own. gex records SPX alone today; IWM, QQQ and XSP
      // are retired and their last rows are history, not staleness. Ageing those would put a
      // permanent warning on the page, which is the failure mode that makes a check worthless --
      // and if the recorder itself dies, the symbols that ARE on the latest session go stale
      // together, which is the alarm worth having.
      const current = new Set(
        db
          .prepare<[], Record<string, unknown>>(
            `SELECT DISTINCT symbol FROM gex_regime_history
             WHERE trade_date = (SELECT MAX(trade_date) FROM gex_regime_history)`,
          )
          .all()
          .map((r) => String(r["symbol"] ?? "")),
      );
      const ages = latest
        .filter((r) => current.has(r.symbol))
        .map((r) => ({
          symbol: r.symbol,
          ageSeconds: r.ts === null ? null : Math.round(now - Date.parse(r.ts) / 1000),
        }));
      const series = db
        .prepare<[], Record<string, unknown>>(
          "SELECT symbol, COUNT(*) AS rows_n, MAX(trade_date) AS latest FROM daily_closes GROUP BY symbol",
        )
        .all()
        .map((r) => ({
          symbol: String(r["symbol"] ?? ""),
          latest: typeof r["latest"] === "string" ? r["latest"] : null,
          rows: Number(r["rows_n"] ?? 0),
        }));
      // Measured against the FRESHEST series rather than a calendar, so this needs no holiday table
      // to be right: if every other close reached today and one sits a year back, that is
      // unambiguous however the trading days fall.
      const freshest = series.reduce<string | null>(
        (a, b) => (b.latest !== null && (a === null || b.latest > a) ? b.latest : a),
        null,
      );
      const closeSeries = series
        .map((r) => ({
          ...r,
          daysBehind:
            freshest === null || r.latest === null
              ? 0
              : Math.round((Date.parse(freshest) - Date.parse(r.latest)) / 86_400_000),
        }))
        .sort((a, b) => b.daysBehind - a.daysBehind || a.symbol.localeCompare(b.symbol));
      return {
        latest: ages,
        sessionDate: typeof session?.["d"] === "string" ? session["d"] : null,
        sessionRows: Number(session?.["n"] ?? 0),
        closeSeries,
      };
    },
  );

  return { latest, recent, integrity };
}
