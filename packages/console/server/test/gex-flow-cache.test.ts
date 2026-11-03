import { afterEach, describe, it, expect } from "vitest";
import { FLOW_MAX_AGE_MS, cachedFlow, resetFlowCache } from "../src/services/gexFlowCache.js";
import type { FlowJob, FlowRead } from "../src/services/gexFlowRead.js";

/**
 * The GEX panel's flow layer never waits on the prints (~10 s on 2026-11-02, which wedged the event
 * loop until the supervisor restarted the console): the request gets the last read, and a stale one
 * starts a single background read.
 */

const JOB: FlowJob = { dbPath: "db", symbol: "SPX", expiration: "E", tradeDate: "D" };
const EMPTY = { classified_by: { aggressor: 0, quote: 0, unclassified: 0 }, excluded: {} } as never;

/** A reader the test settles by hand, counting the reads it was asked for. */
function manual() {
  const pending: Array<(r: FlowRead) => void> = [];
  return {
    reads: () => pending.length,
    settle: async (r: FlowRead) => {
      pending.at(-1)!(r);
      await new Promise((res) => setTimeout(res, 0));
    },
    reader: (_job: FlowJob) => new Promise<FlowRead>((res) => pending.push(res)),
  };
}

afterEach(() => resetFlowCache());

describe("GEX panel flow cache", () => {
  it("answers pending at once and starts one read, however many requests arrive", () => {
    const m = manual();
    expect(cachedFlow(JOB, 0, m.reader).status).toBe("pending");
    expect(cachedFlow(JOB, 1_000, m.reader).status).toBe("pending");
    expect(m.reads()).toBe(1);
  });

  it("serves the last read until it is stale, then serves it while one more read runs", async () => {
    const m = manual();
    cachedFlow(JOB, 0, m.reader);
    await m.settle({ status: "ok", flow: EMPTY });
    const now = Date.now();
    expect(cachedFlow(JOB, now, m.reader).status).toBe("ok");
    expect(m.reads()).toBe(1);
    const stale = now + FLOW_MAX_AGE_MS;
    expect(cachedFlow(JOB, stale, m.reader).status).toBe("ok");
    expect(cachedFlow(JOB, stale + 1, m.reader).status).toBe("ok");
    expect(m.reads()).toBe(2);
  });

  it("starts over for another session, never serving the last one's flow", async () => {
    const m = manual();
    cachedFlow(JOB, 0, m.reader);
    await m.settle({ status: "ok", flow: EMPTY });
    expect(cachedFlow({ ...JOB, tradeDate: "NEXT" }, Date.now(), m.reader).status).toBe("pending");
    expect(m.reads()).toBe(2);
  });

  it("keeps a failed read as the answer, with its reason", async () => {
    const m = manual();
    cachedFlow(JOB, 0, m.reader);
    await m.settle({ status: "failed", reason: "boom" });
    expect(cachedFlow(JOB, Date.now(), m.reader)).toMatchObject({ status: "failed", reason: "boom" });
  });
});
