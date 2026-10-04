/**
 * The per-name chart: read `packages/technicals`' chart files, derive nothing.
 *
 * Same posture as the morning reader beside it. Every series — the grid, CCI, the trend scores, the
 * scan-rule matches, the vendor's levels and whether our grid places them — is that package's
 * answer, written once by the report job; a console that recomputed a CCI would be a second opinion
 * waiting to drift. Fields are read defensively and null is never coerced to zero.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  TechnicalsChart,
  TechnicalsChartBar,
  TechnicalsChartIndex,
  TechnicalsChartPayload,
  TechnicalsGrid,
  TechnicalsSetup,
  TechnicalsVendorChart,
  TechnicalsVendorLevel,
  TechnicalsWatchlist,
  TechnicalsWatchlistRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

/** A symbol is a file name here: only the characters tickers use, so no request can leave the dir. */
const SYMBOL = /^[A-Z0-9][A-Z0-9.-]{0,11}$/;

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return rec(JSON.parse(fs.readFileSync(file, "utf-8")));
  } catch {
    return null;
  }
}

function chartsDir(config: ConsoleConfig): string {
  return path.join(config.paths.technicalsDir, "charts");
}

export function readChartIndex(config: ConsoleConfig): TechnicalsChartIndex {
  const doc = readJson(path.join(chartsDir(config), "index.json"));
  if (doc === null) return { session: null, symbols: [] };
  return {
    session: str(doc["session"]),
    symbols: list(doc["symbols"]).flatMap((raw) => {
      const s = rec(raw);
      const symbol = str(s["symbol"]);
      return symbol === null ? [] : [{ symbol, vendor: s["vendor"] === true }];
    }),
  };
}

function shapeGrid(raw: unknown): TechnicalsGrid | null {
  const g = rec(raw);
  const low = num(g["low"]);
  const high = num(g["high"]);
  const step = num(g["step"]);
  if (low === null || high === null || step === null) return null;
  return { low, high, step, lowDate: str(g["low_date"]), highDate: str(g["high_date"]), window: num(g["window"]) };
}

function shapeLevel(raw: unknown): TechnicalsVendorLevel[] {
  const l = rec(raw);
  const kind = str(l["kind"]);
  const value = num(l["value"]);
  if (kind === null || value === null) return [];
  const on = l["on_our_grid"];
  const view = l["vendor_view"];
  return [
    {
      kind,
      value,
      date: str(l["date"]),
      onOurGrid: typeof on === "boolean" ? on : null,
      vendorView: typeof view === "boolean" ? view : null,
    },
  ];
}

function datedValues(raw: unknown): { date: string; value: number }[] {
  return list(raw).flatMap((r) => {
    const x = rec(r);
    const date = str(x["date"]);
    const value = num(x["value"]);
    return date === null || value === null ? [] : [{ date, value }];
  });
}

function shapeIvRank(raw: unknown): TechnicalsChart["ivRank"] {
  if (!raw || typeof raw !== "object") return null;
  const r = rec(raw);
  const date = str(r["date"]);
  const value = num(r["iv_rank"]);
  const source = str(r["source"]);
  return date === null || value === null || source === null ? null : { date, value, source };
}

function shapeVendor(raw: unknown): TechnicalsVendorChart | null {
  if (!raw || typeof raw !== "object") return null;
  const v = rec(raw);
  const capture = str(v["capture"]);
  if (capture === null) return null;
  return {
    capture,
    fetchedAt: str(v["fetched_at"]),
    through: str(v["through"]),
    levels: list(v["levels"]).flatMap(shapeLevel),
    rank: num(v["rank"]),
    sentiment: str(v["sentiment"]),
    ivRank: num(v["iv_rank"]),
    trendShort: datedValues(v["trend_short"]),
    trendLong: datedValues(v["trend_long"]),
    barsCompared: num(v["bars_compared"]),
    barsAgree: num(v["bars_agree"]),
  };
}

function shapeSetup(raw: unknown): TechnicalsSetup[] {
  const s = rec(raw);
  const id = str(s["id"]);
  const name = str(s["name"]);
  if (id === null || name === null) return [];
  return [
    {
      id,
      name,
      rule: str(s["rule"]) ?? "",
      lines: list(s["lines"]).filter((l): l is string => typeof l === "string"),
      trades: list(s["trades"]).flatMap((rt) => {
        const t = rec(rt);
        const entryDate = str(t["entry_date"]);
        return entryDate === null
          ? []
          : [
              {
                entryDate,
                entryPrice: num(t["entry_price"]),
                exitDate: str(t["exit_date"]),
                exitPrice: num(t["exit_price"]),
                reason: str(t["reason"]),
                target: num(t["target"]),
              },
            ];
      }),
    },
  ];
}

/** The file is columnar (one array per field); rows are rebuilt here. A bar missing any price is
 *  dropped rather than drawn at zero, and the indicator arrays stay aligned with the bars kept. */
function shapeChart(doc: Record<string, unknown>): TechnicalsChart | null {
  const symbol = str(doc["symbol"]);
  const session = str(doc["session"]);
  if (doc["ok"] !== true || symbol === null || session === null) return null;
  const b = rec(doc["bars"]);
  const col = (k: string) => list(b[k]);
  const keep: number[] = [];
  const bars: TechnicalsChartBar[] = [];
  col("date").forEach((d, i) => {
    const date = str(d);
    const open = num(col("open")[i]);
    const high = num(col("high")[i]);
    const low = num(col("low")[i]);
    const close = num(col("close")[i]);
    if (date === null || open === null || high === null || low === null || close === null) return;
    keep.push(i);
    bars.push({ date, open, high, low, close, volume: num(col("volume")[i]) });
  });
  const alignedIn = (from: Record<string, unknown>, k: string) => {
    const s = list(from[k]);
    return keep.map((i) => num(s[i]));
  };
  const aligned = (k: string) => alignedIn(doc, k);
  const lines = rec(doc["setup_lines"]);
  return {
    symbol,
    session,
    generatedAt: str(doc["generated_at"]),
    bars,
    grid: shapeGrid(doc["grid"]),
    cci14: aligned("cci14"),
    cci5: aligned("cci5"),
    rsi14: aligned("rsi14"),
    trendShort: aligned("trend_short"),
    trendLong: aligned("trend_long"),
    rank: num(doc["rank"]),
    sentiment: str(doc["sentiment"]),
    ivRank: shapeIvRank(doc["iv_rank"]),
    signals: list(doc["signals"]).flatMap((raw) => {
      const s = rec(raw);
      const date = str(s["date"]);
      const rules = list(s["rules"]).filter((r): r is string => typeof r === "string");
      return date === null ? [] : [{ date, rules }];
    }),
    setups: list(doc["setups"]).flatMap(shapeSetup),
    setupLines: Object.fromEntries(Object.keys(lines).map((k) => [k, alignedIn(lines, k)])),
    volumeSource: str(doc["volume_source"]),
    vendor: shapeVendor(doc["vendor"]),
  };
}

export function readChart(config: ConsoleConfig, symbol?: string): TechnicalsChartPayload {
  const index = readChartIndex(config);
  const wanted = symbol?.toUpperCase();
  if (wanted === undefined || !SYMBOL.test(wanted)) return { index, chart: null };
  const doc = readJson(path.join(chartsDir(config), `${wanted}.json`));
  return { index, chart: doc === null ? null : shapeChart(doc) };
}

function shapeNear(raw: unknown): TechnicalsWatchlistRow["support"] {
  const n = rec(raw);
  const value = num(n["value"]);
  return value === null ? null : { value, pct: num(n["pct"]) };
}

function shapeWatchRow(raw: unknown): TechnicalsWatchlistRow[] {
  const r = rec(raw);
  const symbol = str(r["symbol"]);
  const session = str(r["session"]);
  const setup = str(r["setup"]);
  const entryDate = str(r["entry_date"]);
  if (symbol === null || session === null || setup === null || entryDate === null) return [];
  return [
    {
      symbol,
      session,
      setup,
      setupName: str(r["setup_name"]) ?? setup,
      entryDate,
      entryPrice: num(r["entry_price"]),
      entryAgo: num(r["entry_ago"]),
      exitDate: str(r["exit_date"]),
      exitPrice: num(r["exit_price"]),
      exitAgo: num(r["exit_ago"]),
      reason: str(r["reason"]),
      target: num(r["target"]),
      // A row with an exit is closed whatever the file says; an open one has none.
      status: str(r["exit_date"]) === null ? "open" : "closed",
      lastClose: num(r["last_close"]),
      movePct: num(r["move_pct"]),
      trend1m: num(r["trend_1m"]),
      trend6m: num(r["trend_6m"]),
      trend1mLabel: str(r["trend_1m_label"]),
      trend6mLabel: str(r["trend_6m_label"]),
      rs: num(r["rs"]),
      vsSpy1m: num(r["vs_spy_1m"]),
      support: shapeNear(r["support"]),
      resistance: shapeNear(r["resistance"]),
    },
  ];
}

/** The setups watchlist the report job writes beside the chart files; empty when there is none. */
export function readSetupsWatchlist(config: ConsoleConfig): TechnicalsWatchlist {
  const doc = readJson(path.join(chartsDir(config), "setups-index.json"));
  if (doc === null) return { session: null, generatedAt: null, window: null, rows: [] };
  return {
    session: str(doc["session"]),
    generatedAt: str(doc["generated_at"]),
    window: num(doc["window"]),
    rows: list(doc["rows"]).flatMap(shapeWatchRow),
  };
}
