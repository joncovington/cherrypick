/**
 * Merged log tail: every suite log's last chunk, JSON lines or prefixed text normalised to
 * {source, level, ts, text}, newest N. One source or all of them.
 *
 * Timestamps arrive in four shapes -- ISO with an offset, ISO or "YYYY-MM-DD HH:MM:SS,mmm" in the
 * machine's local time with none, and the console's own epoch milliseconds -- so each is turned
 * into UTC ISO before the merge sorts on it. Sorting the raw strings put a local 01:00 line
 * beside a UTC 07:00 one as though they were six hours apart.
 */

import fs from "node:fs";
import path from "node:path";
import type { SuiteFeatures } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { moduleOn } from "../services/featuresBridge.js";

const TAIL_BYTES = 256 * 1024;
const DEFAULT_LINES = 50;

export interface LogLine {
  source: string;
  level: string;
  ts: string | null;
  text: string;
}

export function tailFile(p: string): string[] {
  try {
    const stat = fs.statSync(p);
    const fd = fs.openSync(p, "r");
    try {
      const start = Math.max(0, stat.size - TAIL_BYTES);
      const buf = Buffer.alloc(stat.size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      const lines = buf.toString("utf-8").split(/\r?\n/);
      if (start > 0) lines.shift(); // drop the partial first line
      return lines.filter((l) => l.trim() !== "");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return [];
  }
}

const PREFIX_RE = /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s*(?:\[?(\w+)\]?)?\s*(.*)$/;

const LEVELS = new Set(["CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "OK", "NOTIFY"]);

/** Pino's numeric levels, which the console's own log writes. */
const PINO_LEVELS: Record<number, string> = { 10: "DEBUG", 20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "CRITICAL" };

/** Any of the four timestamp shapes to UTC ISO; a string with no zone is the machine's local time. */
function normaliseTs(v: unknown): string | null {
  if (typeof v === "number" && Number.isFinite(v)) return new Date(v > 1e12 ? v : v * 1000).toISOString();
  if (typeof v !== "string") return null;
  const t = Date.parse(v.replace(" ", "T").replace(",", "."));
  return Number.isNaN(t) ? v : new Date(t).toISOString();
}

function parseLine(source: string, raw: string): LogLine {
  try {
    const j = JSON.parse(raw) as Record<string, unknown>;
    const lvl = j["level"] ?? j["status"] ?? j["overall"] ?? "INFO";
    return {
      source,
      level: typeof lvl === "number" ? (PINO_LEVELS[lvl] ?? "INFO") : String(lvl).toUpperCase(),
      ts: normaliseTs(j["ts"] ?? j["time"]),
      text: String(j["msg"] ?? j["message"] ?? j["text"] ?? j["title"] ?? raw),
    };
  } catch {
    const m = PREFIX_RE.exec(raw);
    if (m !== null) {
      // The word after the timestamp is a level only when it IS one: the supervisor writes
      // "<ts> console: resident child started", and reading "console" as a level dropped it.
      const word = (m[2] ?? "").toUpperCase();
      const isLevel = LEVELS.has(word);
      return {
        source,
        level: isLevel ? word : "INFO",
        ts: normaliseTs(m[1]),
        text: isLevel ? (m[3] ?? raw) : raw.slice(m[1]!.length).trim(),
      };
    }
    return { source, level: "INFO", ts: null, text: raw };
  }
}

/** Every suite log, by the id the System page filters on. Paths as the writers name them. */
export function logSources(config: ConsoleConfig): Array<{ id: string; path: string }> {
  const root = path.join(config.paths.cherrypick, "logs");
  return [
    ["watchdog", "watchdog.log"],
    ["notify", "notify.log"],
    ["supervisor", "supervisor.log"],
    ["supervisor-fault", "supervisor-fault.log"],
    ["streamer", "streamer/streamer.log"],
    ["gex", "gex/recorder.log"],
    ["console", "console/console.log"],
    ["overview", "overview.log"],
    ["review", "review.log"],
    ["meic", "meic/paper_loop.log"],
    ["flies", "flies/flies_paper.log"],
    ["flies-live", "flies/flies_live.log"],
    ["flies-alerts", "flies/flies_alert_daemon.log"],
    ["earnings", "earnings_paper.log"],
    ["calendars", "calendars/calendars_paper.log"],
    ["pmcc", "pmcc/pmcc_paper.log"],
    ["curve", "curve/curve_paper.log"],
    ["bwb", "bwb/bwb_paper.log"],
  ].map(([id, rel]) => ({ id: id!, path: path.join(root, rel!) }));
}

/** The Overview's merged view: watchdog, notify and the trading modules' own loops. */
const DEFAULT_SOURCES = new Set(["watchdog", "notify", "meic", "flies", "earnings", "calendars", "pmcc", "curve", "bwb"]);

/**
 * The merged default view leaves out a module the suite has turned off (`features`, decided in
 * Python); asking for that source by name still reads it. Unknown features keep every default.
 */
export function readLogTail(config: ConsoleConfig, limit = DEFAULT_LINES, source?: string, features?: SuiteFeatures): LogLine[] {
  const sources = logSources(config).filter((s) =>
    source === undefined ? DEFAULT_SOURCES.has(s.id) && moduleOn(features, s.id) : s.id === source,
  );
  const lines: LogLine[] = [];
  for (const { id, path: p } of sources) {
    for (const raw of tailFile(p)) lines.push(parseLine(id, raw));
  }
  lines.sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
  return lines.slice(-limit);
}
