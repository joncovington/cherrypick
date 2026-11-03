/**
 * The GEX panel's flow layer, read in a worker thread and served from memory. The request never
 * waits on the prints: it gets the last read (marked with its age), or `pending` before the first,
 * and a read older than `FLOW_MAX_AGE_MS` starts another in the background, one at a time. The
 * panel polls every 15 s, so a fresh read reaches it on the next poll.
 */

import { Worker } from "node:worker_threads";
import type { FlowJob, FlowRead } from "./gexFlowRead.js";

/** How old a read may be before the next request starts another: the prints move with the session. */
export const FLOW_MAX_AGE_MS = 30_000;

export type CachedFlow = (FlowRead & { readAt: number }) | { status: "pending"; reason: string };

interface Entry {
  key: string;
  read: (FlowRead & { readAt: number }) | null;
  inFlight: boolean;
}

/** One session at a time: a new key (the next session, another expiration) replaces the entry. */
let entry: Entry | null = null;

/** Under `tsx` (dev, tests) the worker is the TypeScript source beside this file. */
const WORKER_URL = new URL(import.meta.url.endsWith(".ts") ? "./gexFlowWorker.ts" : "./gexFlowWorker.js", import.meta.url);

function readInWorker(job: FlowJob): Promise<FlowRead> {
  return new Promise((resolve) => {
    let worker: Worker;
    try {
      worker = new Worker(WORKER_URL, { workerData: job });
    } catch (err) {
      resolve({ status: "failed", reason: `flow worker: ${(err as Error).message}` });
      return;
    }
    worker.unref();
    // The first of these settles it; the rest are no-ops.
    worker.once("message", (m) => resolve(m as FlowRead));
    worker.once("error", (err) => resolve({ status: "failed", reason: `flow worker: ${err.message}` }));
    worker.once("exit", (code) => resolve({ status: "failed", reason: `flow worker exited (${code}) without a read` }));
  });
}

export type FlowReader = (job: FlowJob) => Promise<FlowRead>;

/** The last read for `job`, starting a fresh one when there is none or it is stale. */
export function cachedFlow(job: FlowJob, nowMs: number = Date.now(), reader: FlowReader = readInWorker): CachedFlow {
  const key = `${job.dbPath}|${job.symbol}|${job.expiration}|${job.tradeDate}`;
  if (entry?.key !== key) entry = { key, read: null, inFlight: false };
  const current = entry;
  if (!current.inFlight && (current.read === null || nowMs - current.read.readAt >= FLOW_MAX_AGE_MS)) {
    current.inFlight = true;
    void reader(job)
      .catch((err: unknown): FlowRead => ({ status: "failed", reason: (err as Error).message }))
      .then((read) => {
        current.read = { ...read, readAt: Date.now() };
        current.inFlight = false;
      });
  }
  return current.read ?? { status: "pending", reason: "reading the session's prints" };
}

/** Drop the cached read. Tests only. */
export function resetFlowCache(): void {
  entry = null;
}
