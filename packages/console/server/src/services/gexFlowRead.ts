/**
 * One session's signed flow from gex's `gex_prints`, read-only, and why when there is none. Runs in
 * `gexFlowWorker.ts`, off the event loop: a session holds over a million prints, and summing them
 * took ~10 s on 2026-11-02 -- long enough, polled every 15 s, for the supervisor to find the
 * heartbeat silent and restart the console. `gexFlowCache.ts` is the caller.
 */

import fs from "node:fs";
import Database from "better-sqlite3";
import { readSignedFlow, type SignedFlow } from "../analytics/gexFlow.js";

export type FlowRead =
  | { status: "absent" | "empty"; reason: string }
  | { status: "failed"; reason: string }
  | { status: "ok"; flow: SignedFlow };

export interface FlowJob {
  dbPath: string;
  symbol: string;
  expiration: string;
  tradeDate: string;
}

export function readFlow({ dbPath, symbol, expiration, tradeDate }: FlowJob): FlowRead {
  if (!fs.existsSync(dbPath)) return { status: "absent", reason: "no gex history database" };
  let db: Database.Database | null = null;
  try {
    db = new Database(dbPath, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 2000");
    const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gex_prints'").get();
    if (has === undefined) return { status: "absent", reason: "the gex recorder has never recorded prints (prints.enabled)" };
    const flow = readSignedFlow(db, symbol, expiration, tradeDate);
    const counted = flow.classified_by.aggressor + flow.classified_by.quote + flow.classified_by.unclassified;
    if (counted === 0 && Object.values(flow.excluded).every((n) => n === 0)) {
      return { status: "empty", reason: `no prints recorded for ${symbol} ${expiration} on ${tradeDate}` };
    }
    return { status: "ok", flow };
  } catch (err) {
    return { status: "failed", reason: (err as Error).message };
  } finally {
    db?.close();
  }
}
