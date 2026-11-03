import { describe, it, expect } from "vitest";
import Fastify from "fastify";
import { AsyncMemo } from "../src/services/asyncMemo.js";
import { prewarmOnce } from "../src/services/prewarm.js";

/**
 * The console warms its module-CLI pages' default reads itself, one at a time: a pass must never
 * start several python processes at once, even when a key is stale and refreshes in the background.
 */

function appWithSlowReads(names: string[]) {
  const app = Fastify();
  const memo = new AsyncMemo<string>();
  let running = 0;
  let peak = 0;
  let loads = 0;
  let clock = 0;
  for (const name of names) {
    app.get(`/api/${name}`, async () =>
      memo.get(name, 1_000, clock, async () => {
        running += 1;
        loads += 1;
        peak = Math.max(peak, running);
        await new Promise((res) => setTimeout(res, 20));
        running -= 1;
        return `${name}@${clock}`;
      }),
    );
  }
  return {
    app,
    stats: () => ({ peak, loads }),
    advance: (ms: number) => (clock += ms),
  };
}

describe("prewarm", () => {
  it("loads every page's read, one at a time, cold and stale alike", async () => {
    const names = ["a", "b", "c"];
    const { app, stats, advance } = appWithSlowReads(names);
    const urls = names.map((n) => `/api/${n}`);

    expect(await prewarmOnce(app, urls)).toBe(0);
    expect(stats()).toEqual({ peak: 1, loads: 3 });

    // Past the TTL each request answers from memory at once; the pass still waits each refresh out.
    advance(1_000);
    expect(await prewarmOnce(app, urls)).toBe(0);
    expect(stats()).toEqual({ peak: 1, loads: 6 });
    const res = await app.inject({ method: "GET", url: "/api/a", headers: { host: "127.0.0.1" } });
    expect(res.body).toBe("a@1000");
    await app.close();
  });

  it("counts a read that answers with an error, and carries on", async () => {
    const { app } = appWithSlowReads(["a"]);
    expect(await prewarmOnce(app, ["/api/missing", "/api/a"])).toBe(1);
    await app.close();
  });
});
