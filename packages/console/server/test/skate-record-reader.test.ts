import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readSkateRecord } from "../src/readers/skateRecord.js";

/**
 * The forward record reader renames `record.json`'s fields and decides nothing: a missing file and an
 * unreadable one are stated (never zeros), the early record passes through with its nulls, and a
 * decision-grade record's `decision_grade` and `passes` are the file's, even where the z scores and the
 * thresholds would say otherwise.
 */

let tmp: string;
let config: ConsoleConfig;

function write(doc: unknown): void {
  const p = path.join(tmp, "technicals", "skate", "record.json");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, typeof doc === "string" ? doc : JSON.stringify(doc));
}

const EMPTY_GROUP = { n: 0, skated: 0, rate: null, implied: null, z: null };
const TEST = { gap_high: 0.05, gap_low: 0.0, min_expiries: 20, z_one_sided: 1.645 };

const EARLY = {
  recorded: 118,
  added: { "2026-10-20": 118 },
  settled_now: { skated: 0, tested: 0, through: 0, waiting: 118 },
  verdict: {
    settled: 0,
    expiries: 0,
    decision_grade: false,
    bands: {
      fixed: { high_gap: EMPTY_GROUP, below_zero: EMPTY_GROUP, z_high_over_low: null, passes: null, tested: 0 },
      by_expiry: { high_gap: EMPTY_GROUP, below_zero: EMPTY_GROUP, z_high_over_low: null, passes: null, tested: 0 },
    },
  },
  test: TEST,
};

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-skate-record-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
});

describe("readSkateRecord", () => {
  it("states no record when the file is missing, with nulls rather than zeros", () => {
    expect(readSkateRecord(config)).toMatchObject({
      status: "none",
      writtenAt: null,
      recorded: null,
      settled: null,
      expiries: null,
      decisionGrade: false,
      bands: { fixed: null, byExpiry: null },
      test: null,
    });
  });

  it("states an unreadable file, and a file with no verdict, as unreadable", () => {
    write("{ not json");
    const bad = readSkateRecord(config);
    expect(bad).toMatchObject({ status: "unreadable", recorded: null, settled: null });
    expect(bad.writtenAt).not.toBeNull();
    write({ recorded: 3 });
    expect(readSkateRecord(config)).toMatchObject({ status: "unreadable", recorded: null });
  });

  it("passes the early record through, renamed", () => {
    write(EARLY);
    const p = readSkateRecord(config);
    expect(p).toMatchObject({
      status: "ok",
      recorded: 118,
      added: { "2026-10-20": 118 },
      settledNow: { skated: 0, tested: 0, through: 0, waiting: 118 },
      settled: 0,
      expiries: 0,
      decisionGrade: false,
      test: { gapHigh: 0.05, gapLow: 0, minExpiries: 20, zOneSided: 1.645 },
    });
    expect(p.bands.fixed).toEqual({
      highGap: { n: 0, skated: 0, rate: null, implied: null, z: null },
      belowZero: { n: 0, skated: 0, rate: null, implied: null, z: null },
      zHighOverLow: null,
      passes: null,
      tested: 0,
    });
  });

  it("carries a decision-grade record's verdict as the file states it", () => {
    write({
      ...EARLY,
      verdict: {
        settled: 412,
        expiries: 21,
        decision_grade: true,
        bands: {
          // Clears both thresholds, yet the file says it fails: the reader keeps the file's word.
          fixed: {
            high_gap: { n: 90, skated: 88, rate: 0.9778, implied: 0.8, z: 4.1 },
            below_zero: { n: 60, skated: 48, rate: 0.8, implied: 0.84, z: -0.9 },
            z_high_over_low: 3.3,
            passes: false,
            tested: 12,
          },
          // A non-boolean verdict is no verdict, never a pass.
          by_expiry: { high_gap: EMPTY_GROUP, below_zero: EMPTY_GROUP, z_high_over_low: null, passes: "yes", tested: 0 },
        },
      },
    });
    const p = readSkateRecord(config);
    expect(p).toMatchObject({ status: "ok", settled: 412, expiries: 21, decisionGrade: true });
    expect(p.bands.fixed).toMatchObject({ passes: false, zHighOverLow: 3.3, tested: 12, highGap: { n: 90, z: 4.1 } });
    expect(p.bands.byExpiry?.passes).toBeNull();
  });
});
