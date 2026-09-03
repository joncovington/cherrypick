import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { tailFile } from "./logs.js";

/**
 * Trading events -- entries, completions, settlements -- read straight off `logs/notify.log`, the
 * "always on" floor `cherrypick.notify.Notifier` writes before any push channel (Discord, desktop)
 * is even attempted. This is a pure read of a file the suite already produces, never a second
 * classification of what counts as a trade event: `trade_notifier.py` decides that once, this
 * reads its output. A toast reading a DIFFERENT event catalog than Discord would be exactly the
 * kind of second opinion this suite's own rule (mirror a query, never a derivation) warns against
 * -- here there is no derivation at all, just a filter on the `key` prefix trade_notifier already
 * writes (`trade.<module>.<entry|exit|...>`).
 *
 * `since` excludes backfill on purpose, matching trade_notifier's own stated convention ("on first
 * activation the watermark is seeded to the current DB state, so pre-existing paper trades aren't
 * backfilled as a burst") -- a browser tab that just opened should not receive this morning's
 * entire trade history as a toast flood.
 */

export interface NotifyEvent {
  ts: string;
  level: string;
  key: string;
  title: string;
  message: string;
}

const MAX_EVENTS = 50;

export function readTradeNotifications(config: ConsoleConfig, since: string | null): NotifyEvent[] {
  const p = path.join(config.paths.cherrypick, "logs", "notify.log");
  const events: NotifyEvent[] = [];
  for (const raw of tailFile(p)) {
    let j: Record<string, unknown>;
    try {
      j = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      continue;
    }
    const key = typeof j["key"] === "string" ? j["key"] : "";
    if (!key.startsWith("trade.")) continue;
    const ts = typeof j["ts"] === "string" ? j["ts"] : null;
    if (ts === null) continue;
    if (since !== null && ts <= since) continue;
    events.push({
      ts,
      level: typeof j["level"] === "string" ? j["level"] : "INFO",
      key,
      title: typeof j["title"] === "string" ? j["title"] : "Trade event",
      message: typeof j["message"] === "string" ? j["message"] : "",
    });
  }
  events.sort((a, b) => a.ts.localeCompare(b.ts));
  return events.slice(-MAX_EVENTS);
}
