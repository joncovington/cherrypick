/**
 * The suite dashboard's system and end-of-day reads: what is configured, what
 * is scheduled, and what the last session produced. Read-only over the
 * orchestrator's own config and state files.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { buildSuiteReport } from "./report.js";

function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export interface SystemPanel {
  timezone: string | null;
  modules: Array<{
    id: string;
    enabled: boolean;
    kind: string | null;
    streamer: boolean | null;
    champion: string | null;
    liveTrading: boolean | null;
  }>;
  services: Array<{ id: string; enabled: boolean; autoRestart: boolean; launched: string | null; pid: number | null }>;
  watchdog: { intervalMinutes: number | null; renotifyMinutes: number | null; drawdownGuard: boolean | null };
  notify: { channels: string[]; tradeChannels: string[]; webhookStatus: string | null };
  /** Halt flag presence — the suite's global stop. */
  halted: { active: boolean; path: string };
}

export function readSystemPanel(config: ConsoleConfig): SystemPanel {
  const cfg = readJson(config.paths.orchestratorConfig) ?? {};
  const modulesRaw = cfg["modules"];
  const modules: SystemPanel["modules"] = [];
  const pushModule = (id: string, m: Record<string, unknown>) => {
    const moduleCfg = readJson(path.join(config.paths.cherrypick, "config", `${id}.json`)) ?? {};
    modules.push({
      id,
      enabled: m["enabled"] === true,
      kind: typeof m["kind"] === "string" ? m["kind"] : null,
      streamer: typeof m["streamer"] === "boolean" ? m["streamer"] : null,
      champion: typeof m["champion"] === "string" ? m["champion"] : null,
      liveTrading: typeof moduleCfg["enable_live_trading"] === "boolean" ? moduleCfg["enable_live_trading"] : null,
    });
  };
  if (Array.isArray(modulesRaw)) {
    for (const m of modulesRaw as Array<Record<string, unknown>>) pushModule(String(m["id"] ?? m["name"] ?? "?"), m);
  } else if (typeof modulesRaw === "object" && modulesRaw !== null) {
    for (const [k, v] of Object.entries(modulesRaw as Record<string, Record<string, unknown>>)) pushModule(k, v ?? {});
  }

  const services: SystemPanel["services"] = [];
  const svcRaw = cfg["services"];
  if (Array.isArray(svcRaw)) {
    for (const s of svcRaw as Array<Record<string, unknown>>) {
      const id = String(s["id"] ?? "?");
      const launch = readJson(path.join(config.paths.cherrypick, "state", `service-${id}.launch.json`));
      services.push({
        id,
        enabled: s["enabled"] === true,
        autoRestart: s["auto_restart"] === true,
        launched: typeof launch?.["launched_at"] === "string" ? launch["launched_at"] : null,
        pid: typeof launch?.["pid"] === "number" ? launch["pid"] : null,
      });
    }
  }

  const watchdogCfg = (cfg["watchdog"] ?? {}) as Record<string, unknown>;
  const notifyCfg = (cfg["notify"] ?? {}) as Record<string, unknown>;
  const secrets = (notifyCfg["secrets"] ?? {}) as Record<string, unknown>;
  const haltPath = path.join(config.paths.cherrypick, "state", "halt-live.flag");

  return {
    timezone: typeof cfg["timezone"] === "string" ? cfg["timezone"] : null,
    modules,
    services,
    watchdog: {
      intervalMinutes: typeof watchdogCfg["interval_minutes"] === "number" ? watchdogCfg["interval_minutes"] : null,
      renotifyMinutes: typeof watchdogCfg["renotify_minutes"] === "number" ? watchdogCfg["renotify_minutes"] : null,
      drawdownGuard:
        typeof watchdogCfg["drawdown_guard"] === "boolean"
          ? watchdogCfg["drawdown_guard"]
          : typeof (watchdogCfg["drawdown_guard"] as Record<string, unknown>)?.["enabled"] === "boolean"
            ? ((watchdogCfg["drawdown_guard"] as Record<string, unknown>)["enabled"] as boolean)
            : null,
    },
    notify: {
      channels: Array.isArray(notifyCfg["channels"]) ? notifyCfg["channels"].map(String) : [],
      tradeChannels: Array.isArray(notifyCfg["trade_channels"]) ? notifyCfg["trade_channels"].map(String) : [],
      // Never the URL — only whether a webhook is configured.
      webhookStatus: typeof secrets["status"] === "string" ? secrets["status"] : null,
    },
    halted: { active: fs.existsSync(haltPath), path: haltPath },
  };
}

export interface EodCard {
  session: string | null;
  isLastSession: boolean;
  suite: { net: number; trades: number; wins: number; losses: number };
  byModule: Array<{ module: string; net: number }>;
  /** Which per-module reports exist for the session — links the reader can open. */
  reports: Array<{ module: string; kind: string; file: string; exists: boolean }>;
}

export function readEod(config: ConsoleConfig): EodCard {
  const report = buildSuiteReport(config);
  const todayEt = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const last = report.daily[report.daily.length - 1];
  const todayRow = report.daily.find((d) => d.session === todayEt);
  const row = todayRow ?? last;
  const session = row?.session ?? null;

  const reports: EodCard["reports"] = [];
  if (session !== null) {
    const logsRoot = path.join(config.paths.cherrypick, "logs");
    for (const module of ["meic", "flies", "earnings"]) {
      for (const [kind, name] of [
        ["metrics", `paper-eod-${session}.md`],
        ["analysis", `eod-analysis-${session}.md`],
      ] as Array<[string, string]>) {
        const inModule = path.join(logsRoot, module, name);
        const atRoot = path.join(logsRoot, name);
        const file = fs.existsSync(inModule) ? inModule : atRoot;
        reports.push({ module, kind, file, exists: fs.existsSync(file) });
      }
    }
  }

  let wins = 0;
  let losses = 0;
  for (const m of Object.values(report.modules)) {
    wins += m.wins;
    losses += m.losses;
  }

  return {
    session,
    isLastSession: todayRow === undefined,
    suite: {
      net: row?.net ?? 0,
      trades: Object.values(report.modules).reduce((s, m) => s + m.trades, 0),
      wins,
      losses,
    },
    byModule: Object.entries(row?.byModule ?? {}).map(([module, net]) => ({ module, net })),
    reports,
  };
}

/** Render a report markdown file to safe HTML — headings, tables, lists, code. */
export function renderReport(file: string): string | null {
  if (!fs.existsSync(file)) return null;
  let md: string;
  try {
    md = fs.readFileSync(file, "utf-8");
  } catch {
    return null;
  }
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s: string) =>
    esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");

  const out: string[] = [];
  let inTable = false;
  let inList = false;
  const closeBlocks = () => {
    if (inTable) {
      out.push("</tbody></table>");
      inTable = false;
    }
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading !== null) {
      closeBlocks();
      const level = Math.min(heading[1]!.length + 1, 5);
      out.push(`<h${level}>${inline(heading[2]!)}</h${level}>`);
      continue;
    }
    if (/^\s*\|/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (/^[\s|:-]+$/.test(line)) continue; // separator row
      if (!inTable) {
        out.push(`<table class="data-table"><thead><tr>${cells.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`);
        inTable = true;
      } else {
        out.push(`<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`);
      }
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (inTable) {
        out.push("</tbody></table>");
        inTable = false;
      }
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`);
      continue;
    }
    closeBlocks();
    if (line.trim() !== "") out.push(`<p>${inline(line)}</p>`);
  }
  closeBlocks();
  return out.join("\n");
}
