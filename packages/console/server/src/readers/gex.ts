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

  return { latest, recent };
}
