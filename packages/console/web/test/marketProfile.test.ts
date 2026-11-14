import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CandleBar } from "@console/shared";

import { buildProfile, defaultRowSize, sessionDates } from "../src/lib/marketProfile";

/**
 * The Intraday chart's profile is a TypeScript mirror of `cherrypick.core.marketprofile`. Both answer
 * the same fixture file, which `packages/core/tests/test_marketprofile.py` also reads, so the two
 * cannot drift apart without a red test on one side. The fixture's first case is worked by hand in
 * the Python test, and its value area (99-103) is the guard: a one-row expansion gives 100-103 and
 * fails here too (shown by breaking the port that way on purpose).
 */

interface FixtureCase {
  name: string;
  row_size: number;
  bar_seconds: number;
  session: string | null;
  bars: CandleBar[];
  expected: Record<string, unknown> & { rows: { price: number; letters: string; volume: number }[] };
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { row_sizes: [number, number][]; cases: FixtureCase[] };

/** The Python key each mirror field answers for. */
const KEYS: Record<string, string> = {
  session: "session",
  rowSize: "row_size",
  periods: "periods",
  open: "open",
  close: "close",
  high: "high",
  low: "low",
  poc: "poc",
  val: "val",
  vah: "vah",
  tpoTotal: "tpo_total",
  ibLow: "ib_low",
  ibHigh: "ib_high",
  extensionUp: "extension_up",
  extensionDown: "extension_down",
  tailHigh: "tail_high",
  tailLow: "tail_low",
  poorHigh: "poor_high",
  poorLow: "poor_low",
  singlePrints: "single_prints",
  volumeTotal: "volume_total",
  volumePoc: "volume_poc",
};

describe("marketProfile mirrors core.marketprofile", () => {
  it.each(FIXTURE.row_sizes)("default row size for %d is %d", (price, size) => {
    expect(defaultRowSize(price)).toBe(size);
  });

  it.each(FIXTURE.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const got = buildProfile(c.bars, { rowSize: c.row_size, barSeconds: c.bar_seconds, session: c.session });
    expect(got).not.toBeNull();
    const p = got!;
    expect(p.rows.map((r) => [r.price, r.letters])).toEqual(c.expected.rows.map((r) => [r.price, r.letters]));
    p.rows.forEach((r, i) => expect(r.volume).toBeCloseTo(c.expected.rows[i]!.volume, 6));
    for (const [ts, py] of Object.entries(KEYS)) {
      const want = c.expected[py];
      const have = (p as unknown as Record<string, unknown>)[ts];
      // Volume shares sum in float, so a total can land 1e-10 off a whole number on either side.
      if (typeof want === "number") expect(have, ts).toBeCloseTo(want, 6);
      else expect(have, ts).toEqual(want);
    }
  });

  it("the hand-worked case's value area is the two-row expansion's", () => {
    const c = FIXTURE.cases[0]!;
    const p = buildProfile(c.bars, { rowSize: c.row_size, barSeconds: c.bar_seconds, session: c.session })!;
    expect([p.poc, p.val, p.vah]).toEqual([101, 99, 103]);
  });

  it("profiles /NQ's day session only and reads its sessions", () => {
    const nq = FIXTURE.cases.find((c) => c.name.startsWith("/NQ"))!;
    expect(sessionDates(nq.bars)).toEqual(["2026-11-13"]);
  });

  it("refuses a bar that straddles two periods", () => {
    const t = FIXTURE.cases[0]!.bars[0]!.t + 5 * 60; // 09:35
    expect(() => buildProfile([{ t, o: 1, h: 1, l: 1, c: 1, v: 0 }], { rowSize: 1, barSeconds: 900 })).toThrow();
    expect(buildProfile([], { rowSize: 1, barSeconds: 60 })).toBeNull();
  });
});
