import path from "node:path";
import type { GexPayload, GexRegimeRow } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, num, str } from "./db.js";

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

export function readGex(config: ConsoleConfig): GexPayload {
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

  const recent = withReadOnlyDb<GexRegimeRow[]>(dbPath, [], (db) =>
    db
      .prepare<[], Record<string, unknown>>(
        `SELECT * FROM gex_regime_history
          WHERE trade_date = (SELECT MAX(trade_date) FROM gex_regime_history)
          ORDER BY ts DESC LIMIT 60`,
      )
      .all()
      .map(toRow),
  );

  return { latest, recent };
}
