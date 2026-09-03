import { describe, it, expect, afterEach } from "vitest";
import {
  readExcursions,
  resetExcursionsCache,
  setExcursionsCaller,
} from "../src/services/excursionsBridge.js";

/**
 * MAE/MFE per closed position, via each module's own `excursions` verb. Position shape genuinely
 * differs per module (curve/calendars/pmcc tag by `book`, earnings by `strategy`; earnings has no
 * per-position `n`) -- what these tests pin is that both normalise into the SAME shape
 * `ExcursionsCard.tsx` reads, and that a module with no Python excursions support (meic/flies/bwb)
 * reports unavailable rather than an empty result that would read as "no positions ever moved."
 */

afterEach(() => {
  setExcursionsCaller();
  resetExcursionsCache();
});

describe("the excursions bridge", () => {
  it("normalises a ledger-module's book-tagged positions", () => {
    setExcursionsCaller(() => ({
      ok: true,
      error: null,
      data: {
        positions: [{ id: "p1", tag: "control", symbol: "VXX", mae: -20.0, mfe: 40.0, n: 4 }],
        maeDistribution: { median: -20.0, n: 1 },
        mfeDistribution: { median: 40.0, n: 1 },
      },
    }));
    const out = readExcursions("curve", "/db/curve.db", 1_000);
    expect(out.ok).toBe(true);
    expect(out.data?.positions[0]).toEqual({ id: "p1", tag: "control", symbol: "VXX", mae: -20.0, mfe: 40.0, n: 4 });
  });

  it("reports an unavailable module (meic/flies/bwb) as an error, never an empty result", () => {
    // No SPEC exists for these -- an empty {positions:[]} would read as "every position was
    // exactly flat," which is not what "this module isn't wired" means.
    setExcursionsCaller(() => ({ ok: false, data: null, error: "excursions unavailable" }));
    const out = readExcursions("meic", "/db/meic.db", 1_000);
    expect(out.ok).toBe(false);
    expect(out.data).toBeNull();
    expect(out.error).toContain("unavailable");
  });

  it("memoises per (module, db), because MAE/MFE replays every usable mark", () => {
    let calls = 0;
    setExcursionsCaller(() => {
      calls += 1;
      return { ok: true, error: null, data: { positions: [], maeDistribution: { median: null, n: 0 }, mfeDistribution: { median: null, n: 0 } } };
    });
    readExcursions("curve", "/db/curve.db", 1_000);
    readExcursions("curve", "/db/curve.db", 60_000);
    expect(calls).toBe(1);
    readExcursions("curve", "/db/curve.db", 1_000 + 200_000);
    expect(calls).toBe(2);
  });

  it("memoises different modules and db paths under separate keys", () => {
    let calls = 0;
    setExcursionsCaller(() => {
      calls += 1;
      return { ok: true, error: null, data: { positions: [], maeDistribution: { median: null, n: 0 }, mfeDistribution: { median: null, n: 0 } } };
    });
    readExcursions("curve", "/db/curve.db", 1_000);
    readExcursions("pmcc", "/db/pmcc.db", 1_000);
    readExcursions("curve", "/db/other-curve.db", 1_000);
    expect(calls).toBe(3);
  });
});
