import type { FastifyInstance } from "fastify";
import { loadsSettled } from "./asyncMemo.js";

/**
 * Keeps the module-CLI bridges' answers warm, so no page waits on a cold python read.
 *
 * The bridges serve a stale answer while they refresh (`AsyncMemo`), for up to an hour past their
 * TTL. That covers a page someone is using; it does not cover the first visit after a restart, or
 * one after an idle hour, which waited out the whole read (System › data 27 s, calendars' policy
 * table 13-18 s, 2026-11-02). So the console asks for those pages' default reads itself: once
 * shortly after it starts listening, then every `INTERVAL_MS`, which is inside the stale window, so
 * a visit is always answered from memory.
 *
 * Through `app.inject`, the same route and arguments the page uses, so the warmed key is the one the
 * page reads. One at a time, so a pass never starts several python processes at once; a pass costs
 * what opening those pages would, and a fresh key is answered from memory without spawning.
 */

/** The default reads of the pages whose data comes from a module CLI. */
export const PREWARM_URLS = [
  "/api/system/quote-history",
  "/api/system/impliedvar",
  "/api/calendars",
  "/api/calendars/policies",
  "/api/earnings/verdict?",
  "/api/earnings/screen?mode=paper",
  "/api/earnings",
  "/api/desk",
  "/api/bwb/vol",
  "/api/compare?scope=era",
  // Not a bridge: its first read after a restart counts every pmcc mark (see readers/pmcc.ts).
  "/api/pmcc",
] as const;

const FIRST_PASS_DELAY_MS = 10_000;
export const INTERVAL_MS = 30 * 60_000;

export async function prewarmOnce(app: FastifyInstance, urls: readonly string[] = PREWARM_URLS): Promise<number> {
  let failed = 0;
  for (const url of urls) {
    try {
      const res = await app.inject({ method: "GET", url, headers: { host: "127.0.0.1" } });
      if (res.statusCode >= 400) failed += 1;
    } catch {
      failed += 1;
    }
    // A stale key answers at once and refreshes in the background: wait for that refresh, so the
    // pass still runs one python process at a time.
    await loadsSettled();
  }
  return failed;
}

export function startPrewarm(app: FastifyInstance, log: (msg: string) => void): void {
  let running = false;
  const pass = (): void => {
    if (running) return;
    running = true;
    void prewarmOnce(app)
      .then((failed) => {
        if (failed > 0) log(`prewarm: ${String(failed)} of ${String(PREWARM_URLS.length)} reads answered with an error`);
      })
      .finally(() => {
        running = false;
      });
  };
  setTimeout(pass, FIRST_PASS_DELAY_MS).unref();
  setInterval(pass, INTERVAL_MS).unref();
}
