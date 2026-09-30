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
  TechnicalsVendorChart,
  TechnicalsVendorLevel,
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
  return [{ kind, value, date: str(l["date"]), onOurGrid: typeof on === "boolean" ? on : null }];
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
  const aligned = (k: string) => {
    const s = list(doc[k]);
    return keep.map((i) => num(s[i]));
  };
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
    sentiment: str(doc["sentiment"]),
    ivRank: shapeIvRank(doc["iv_rank"]),
    signals: list(doc["signals"]).flatMap((raw) => {
      const s = rec(raw);
      const date = str(s["date"]);
      const rules = list(s["rules"]).filter((r): r is string => typeof r === "string");
      return date === null ? [] : [{ date, rules }];
    }),
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
