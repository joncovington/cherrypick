import fs from "node:fs";
import type { OverviewPayload, WatchdogSnapshot, ServiceEntry } from "@console/shared";
import type { ConsoleConfig } from "../config.js";

function readJson(p: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function readWatchdog(path: string): WatchdogSnapshot {
  const raw = readJson(path);
  if (raw === null) {
    return {
      ts: null,
      et: null,
      overall: null,
      inSession: false,
      isTradingDay: false,
      findings: [],
      ageSeconds: null,
    };
  }
  const ts = typeof raw["ts"] === "string" ? raw["ts"] : null;
  let ageSeconds: number | null = null;
  if (ts !== null) {
    const parsed = Date.parse(ts);
    if (!Number.isNaN(parsed)) ageSeconds = Math.max(0, (Date.now() - parsed) / 1000);
  }
  return {
    ts,
    et: typeof raw["et"] === "string" ? raw["et"] : null,
    overall: typeof raw["overall"] === "string" ? raw["overall"] : null,
    inSession: raw["in_session"] === true,
    isTradingDay: raw["is_trading_day"] === true,
    findings: Array.isArray(raw["findings"])
      ? raw["findings"].map((f: Record<string, unknown>) => ({
          key: String(f["key"] ?? ""),
          status: String(f["status"] ?? ""),
          title: String(f["title"] ?? ""),
          message: String(f["message"] ?? ""),
        }))
      : [],
    ageSeconds,
  };
}

export function readOverview(config: ConsoleConfig): OverviewPayload {
  const orch = readJson(config.paths.orchestratorConfig);
  const services: ServiceEntry[] = [];
  if (orch !== null && Array.isArray(orch["services"])) {
    for (const s of orch["services"] as Array<Record<string, unknown>>) {
      services.push({ id: String(s["id"] ?? "?"), enabled: s["enabled"] === true });
    }
  }
  return {
    watchdog: readWatchdog(config.paths.watchdogLast),
    services,
    timezone: orch !== null && typeof orch["timezone"] === "string" ? orch["timezone"] : null,
  };
}
