/**
 * The memo every module-CLI bridge keeps, without blocking the server: a value per key for `ttl`
 * ms, and one load per key at a time -- a second request for a key being loaded waits on the first
 * rather than starting its own subprocess (two tabs opened together, a page and its poll).
 *
 * Until 2026-11-02 each bridge memoised a synchronous spawn, so every cold read froze every request
 * and the heartbeat for as long as python ran: 41 s for System › data's quote-history coverage,
 * 17.6 s for the calendars policy table. `load` may still answer synchronously -- the bridges' test
 * callers do -- and the answer is cached the same way.
 */
export class AsyncMemo<T> {
  private readonly cache = new Map<string, { at: number; value: T }>();
  private readonly inflight = new Map<string, Promise<T>>();

  get(key: string, ttl: number, now: number, load: () => T | Promise<T>): Promise<T> {
    const hit = this.cache.get(key);
    if (hit !== undefined && now - hit.at < ttl) return Promise.resolve(hit.value);
    const pending = this.inflight.get(key);
    if (pending !== undefined) return pending;
    const read = Promise.resolve()
      .then(load)
      .then((value) => {
        // Stamped with the caller's clock, as the synchronous memos were, so a test's `now` holds.
        this.cache.set(key, { at: now, value });
        return value;
      });
    this.inflight.set(key, read);
    void read.then(
      () => this.inflight.delete(key),
      () => this.inflight.delete(key),
    );
    return read;
  }

  clear(): void {
    this.cache.clear();
    this.inflight.clear();
  }
}
