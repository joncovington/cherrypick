/**
 * The suite dashboard's system read: what is configured and what is scheduled. Read-only over the
 * orchestrator's own config and state files.
 */

import fs from "node:fs";
import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import type { AlertDaemonHealth } from "@console/shared";
import { readModuleGate } from "./liveLock.js";
import { readAlertDaemonHealth } from "./alertDaemon.js";
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
  /** The flies order-alert daemon's health; null when `live.use_order_alert_daemon` is off. */
  alertDaemon: AlertDaemonHealth | null;
}

/** Where each service writes its pid; a service not listed here has none the console knows of. */
const SERVICE_PID_FILES: Record<string, string[]> = {
  streamer: ["data", "marketdata", "streamer.pid"],
  "gex-recorder": ["data", "gex", "recorder.pid"],
};

function readPidFile(config: ConsoleConfig, id: string): number | null {
  const rel = SERVICE_PID_FILES[id];
  if (rel === undefined) return null;
  try {
    const n = Number.parseInt(fs.readFileSync(path.join(config.paths.cherrypick, ...rel), "utf-8").trim(), 10);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
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
        // The launch stamp records WHEN (`stamped_at`, epoch seconds) but not the pid; the daemon's
        // own pid file has that. Reading `launched_at`/`pid` off the stamp left both always null.
        launched: typeof launch?.["stamped_at"] === "number" ? new Date(launch["stamped_at"] * 1000).toISOString() : null,
        pid: readPidFile(config, id),
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
    alertDaemon: readAlertDaemonHealth(config),
  };
}
