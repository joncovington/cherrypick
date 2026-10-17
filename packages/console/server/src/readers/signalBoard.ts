/**
 * The signal board for the Screeners page's board tab (2026-10-17).
 *
 * `python -m cherrypick.technicals board` writes one file a session to `technicals/board/<day>.json`:
 * each listed stock's tags from our own engines (RS stage, its sector's rotation, the trend scan,
 * that session's analyst revisions), its side and its count. This reads that file and renames its
 * fields; it decides nothing.
 */

import fs from "node:fs";
import path from "node:path";
import type { SignalBoardMethod, SignalBoardPayload, SignalBoardRow, SignalMethod, SignalTag } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { num, str } from "./db.js";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const METHODS = new Set<SignalMethod>(["stage", "rotation", "scan", "revision"]);

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

export function signalBoardDir(config: ConsoleConfig): string {
  return path.join(config.paths.technicalsDir, "board");
}

export function signalBoardDays(config: ConsoleConfig): string[] {
  try {
    return fs
      .readdirSync(signalBoardDir(config))
      .filter((n) => n.endsWith(".json") && DAY.test(n.slice(0, -5)))
      .map((n) => n.slice(0, -5))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function tag(v: unknown): SignalTag | null {
  const t = rec(v);
  const method = str(t["method"]);
  const label = str(t["label"]);
  if (method === null || !METHODS.has(method as SignalMethod) || label === null) return null;
  return { method: method as SignalMethod, label, detail: str(t["detail"]) ?? "" };
}

function row(v: unknown): SignalBoardRow | null {
  const r = rec(v);
  const symbol = str(r["symbol"]);
  if (symbol === null) return null;
  return {
    symbol,
    sector: str(r["sector"]),
    side: r["side"] === "bullish" || r["side"] === "bearish" ? r["side"] : null,
    count: num(r["count"]) ?? 0,
    bullish: num(r["bullish"]) ?? 0,
    bearish: num(r["bearish"]) ?? 0,
    tags: Array.isArray(r["tags"]) ? r["tags"].map(tag).filter((t): t is SignalTag => t !== null) : [],
  };
}

export function readSignalBoard(config: ConsoleConfig, wanted?: string | null): SignalBoardPayload {
  const days = signalBoardDays(config);
  const day = wanted && days.includes(wanted) ? wanted : (days[0] ?? null);
  const empty: SignalBoardPayload = { day, days, names: 0, methods: {}, notCounted: [], revisionsThrough: null, rows: [] };
  if (day === null) return empty;
  let doc: Record<string, unknown>;
  try {
    doc = rec(JSON.parse(fs.readFileSync(path.join(signalBoardDir(config), `${day}.json`), "utf-8")));
  } catch {
    return empty;
  }
  const methods: Partial<Record<SignalMethod, SignalBoardMethod>> = {};
  for (const [k, v] of Object.entries(rec(doc["methods"]))) {
    const m = rec(v);
    if (METHODS.has(k as SignalMethod)) methods[k as SignalMethod] = { label: str(m["label"]) ?? k, approximate: m["approximate"] === true };
  }
  return {
    day,
    days,
    names: num(doc["names"]) ?? 0,
    methods,
    notCounted: Array.isArray(doc["not_counted"]) ? doc["not_counted"].map((x) => str(x)).filter((x): x is string => x !== null) : [],
    revisionsThrough: str(doc["revisions_through"]),
    rows: Array.isArray(doc["rows"]) ? doc["rows"].map(row).filter((r): r is SignalBoardRow => r !== null) : [],
  };
}
