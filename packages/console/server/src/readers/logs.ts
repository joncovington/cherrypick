/**
 * Merged log tail, following the suite dashboard's rules: watchdog.log,
 * notify.log, and each module's paper.log; last chunk of each file; JSON
 * lines or prefixed text normalized to {source, level, ts, text}; newest N.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";

const TAIL_BYTES = 256 * 1024;
const DEFAULT_LINES = 50;

export interface LogLine {
  source: string;
  level: string;
  ts: string | null;
  text: string;
}

function tailFile(p: string): string[] {
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

const PREFIX_RE = /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s*(?:\[?(\w+)\]?)?\s*(.*)$/;

function parseLine(source: string, raw: string): LogLine {
  try {
    const j = JSON.parse(raw) as Record<string, unknown>;
    return {
      source,
      level: String(j["level"] ?? j["status"] ?? "INFO").toUpperCase(),
      ts: typeof j["ts"] === "string" ? j["ts"] : typeof j["time"] === "string" ? j["time"] : null,
      text: String(j["msg"] ?? j["message"] ?? j["text"] ?? raw),
    };
  } catch {
    const m = PREFIX_RE.exec(raw);
    if (m !== null) {
      const level = (m[2] ?? "INFO").toUpperCase();
      return {
        source,
        level: ["CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "OK", "NOTIFY"].includes(level) ? level : "INFO",
        ts: m[1] ?? null,
        text: m[3] ?? raw,
      };
    }
    return { source, level: "INFO", ts: null, text: raw };
  }
}

export function readLogTail(config: ConsoleConfig, limit = DEFAULT_LINES): LogLine[] {
  const logsRoot = path.join(config.paths.cherrypick, "logs");
  const sources: Array<[string, string]> = [
    ["watchdog", path.join(logsRoot, "watchdog.log")],
    ["notify", path.join(logsRoot, "notify.log")],
  ];
  for (const mod of ["meic", "flies", "earnings"]) {
    const inModule = path.join(logsRoot, mod, "paper.log");
    const atRoot = path.join(logsRoot, `${mod}-paper.log`);
    sources.push([mod, fs.existsSync(inModule) ? inModule : atRoot]);
  }
  sources.push(["calendars", path.join(logsRoot, "calendars", "calendars_paper.log")]);

  const lines: LogLine[] = [];
  for (const [source, p] of sources) {
    for (const raw of tailFile(p)) lines.push(parseLine(source, raw));
  }
  lines.sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
  return lines.slice(-limit);
}
