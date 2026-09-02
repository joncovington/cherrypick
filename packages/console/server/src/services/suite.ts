/**
 * The suite dashboard's system and end-of-day reads: what is configured, what
 * is scheduled, and what the last session produced. Read-only over the
 * orchestrator's own config and state files.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { buildSuiteReport } from "./report.js";
import { readModuleGate } from "./liveLock.js";
import { readJson } from "../readers/db.js";

export interface SystemPanel {
  timezone: string | null;
  modules: Array<{
    id: string;
    enabled: boolean;
    kind: string | null;
    streamer: boolean | null;
    liveTrading: boolean | null;
  }>;
  services: Array<{
    id: string;
    enabled: boolean;
    autoRestart: boolean;
    launched: string | null;
    pid: number | null;
    /** The watchdog's own verdict for this service (its `service.<id>` finding from the last tick):
     * level (OK/WARN/CRITICAL), a short note ("running", "stalled — recycled", "was down —
     * restarted"), and the finding's full message. The console renders what the watchdog decided —
     * it never re-derives service health itself. Null when the watchdog has not reported. */
    health: string | null;
    note: string | null;
    detail: string | null;
  }>;
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
    modules.push({
      id,
      enabled: m["enabled"] === true,
      kind: typeof m["kind"] === "string" ? m["kind"] : null,
      streamer: typeof m["streamer"] === "boolean" ? m["streamer"] : null,
      // Shared with the Config page's lock hero so the two can't disagree — and so flies' nested
      // `live.enabled` is seen here too, which a top-level-only read misses entirely.
      liveTrading: readModuleGate(config, id).liveEnabled,
    });
  };
  if (Array.isArray(modulesRaw)) {
    for (const m of modulesRaw as Array<Record<string, unknown>>) pushModule(String(m["id"] ?? m["name"] ?? "?"), m);
  } else if (typeof modulesRaw === "object" && modulesRaw !== null) {
    for (const [k, v] of Object.entries(modulesRaw as Record<string, Record<string, unknown>>)) pushModule(k, v ?? {});
  }

  // The watchdog's per-service findings (`service.<id>`, written every tick) are the authority on
  // service health — they carry the stall/recycle verdicts a config or launch stamp cannot see.
  const watchdogLast = readJson(config.paths.watchdogLast);
  const serviceFindings = new Map<string, { status: string; title: string; message: string }>();
  if (Array.isArray(watchdogLast?.["findings"])) {
    for (const f of watchdogLast["findings"] as Array<Record<string, unknown>>) {
      const key = String(f["key"] ?? "");
      if (key.startsWith("service.")) {
        serviceFindings.set(key.slice("service.".length), {
          status: String(f["status"] ?? ""),
          title: String(f["title"] ?? ""),
          message: String(f["message"] ?? ""),
        });
      }
    }
  }

  const services: SystemPanel["services"] = [];
  const svcRaw = cfg["services"];
  if (Array.isArray(svcRaw)) {
    for (const s of svcRaw as Array<Record<string, unknown>>) {
      const id = String(s["id"] ?? "?");
      const launch = readJson(path.join(config.paths.cherrypick, "state", `service-${id}.launch.json`));
      const finding = serviceFindings.get(id) ?? null;
      // An OK finding's title is just the id and its message is the note ("running"); a WARN
      // finding's title carries the verdict ("gex-recorder stalled — recycled") with the long
      // explanation in the message. Strip the id so the table cell reads as the verdict alone.
      const note =
        finding === null
          ? null
          : finding.status === "OK"
            ? finding.message
            : finding.title.startsWith(id)
              ? finding.title.slice(id.length).trim()
              : finding.title;
      services.push({
        id,
        enabled: s["enabled"] === true,
        autoRestart: s["auto_restart"] === true,
        launched: typeof launch?.["launched_at"] === "string" ? launch["launched_at"] : null,
        pid: typeof launch?.["pid"] === "number" ? launch["pid"] : null,
        health: finding?.status ?? null,
        note,
        detail: finding?.message ?? null,
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
    // The six per-module EOD files were retired 2026-08-13; this listed all of them and would now
    // render six dead links. The session's record is the review's three artifacts.
    for (const [kind, name] of [
      ["facts", `eod-${session}.json`],
      ["render", `eod-${session}.md`],
      ["note", `eod-${session}.note.md`],
    ] as Array<[string, string]>) {
      const file = path.join(config.paths.reviewDir, name);
      reports.push({ module: "review", kind, file, exists: fs.existsSync(file) });
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
