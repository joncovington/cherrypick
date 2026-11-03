/**
 * The memo every module-CLI bridge keeps, without blocking the server: a value per key for `ttl`
 * ms, and one load per key at a time -- a second request for a key being loaded waits on the first
 * rather than starting its own subprocess (two tabs opened together, a page and its poll).
 *
 * Until 2026-11-02 each bridge memoised a synchronous spawn, so every cold read froze every request
 * and the heartbeat for as long as python ran: 41 s for System › data's quote-history coverage,
 * 17.6 s for the calendars policy table. `load` may still answer synchronously -- the bridges' test
 * callers do -- and the answer is cached the same way.
 *
 * Past its TTL a value is still served for `STALE_MS` while one load refreshes it in the background:
 * with the spawns off the event loop, a page still waited out a cold read of its own (calendars'
 * policy table, 13-18 s, every five minutes). The pages poll, so the refreshed answer arrives on the
 * next one. Past the stale window the request waits for the load, as a first read does.
 */

/** How long past its TTL a value may still be served while it refreshes. */
export const STALE_MS = 60 * 60_000;

/** Every load in flight, across memos: what a background refresh leaves running. */
const running = new Set<Promise<unknown>>();

/** Resolves once no memo has a load in flight (a load started meanwhile is waited for too). */
export async function loadsSettled(): Promise<void> {
  while (running.size > 0) await Promise.allSettled([...running]);
}

export class AsyncMemo<T> {
  private readonly cache = new Map<string, { at: number; value: T }>();
  private readonly inflight = new Map<string, Promise<T>>();

  get(key: string, ttl: number, now: number, load: () => T | Promise<T>): Promise<T> {
    const hit = this.cache.get(key);
    if (hit !== undefined && now - hit.at < ttl) return Promise.resolve(hit.value);
    const read = this.inflight.get(key) ?? this.load(key, now, load);
    if (hit !== undefined && now - hit.at < ttl + STALE_MS) {
      // A refresh that fails is not remembered and is retried on the next request; the stale
      // value stays meanwhile, so nothing here may reject unobserved.
      void read.catch(() => undefined);
      return Promise.resolve(hit.value);
    }
    return read;
  }

  private load(key: string, now: number, load: () => T | Promise<T>): Promise<T> {
    const read = Promise.resolve()
      .then(load)
      .then((value) => {
        // Stamped with the caller's clock, as the synchronous memos were, so a test's `now` holds.
        this.cache.set(key, { at: now, value });
        return value;
      });
    this.inflight.set(key, read);
    running.add(read);
    const done = (): void => {
      this.inflight.delete(key);
      running.delete(read);
    };
    void read.then(done, done);
    return read;
  }

  clear(): void {
    this.cache.clear();
    this.inflight.clear();
  }
}
