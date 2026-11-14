import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CandleBar, ProfileBarsPayload } from "@console/shared";

import { buildView, partialPeriods, valueRelation } from "../src/pages/Profile/profileView";
import { buildProfile } from "../src/lib/marketProfile";

/**
 * The Profile tab's view: which sessions it profiles, one row grid for every column, the composite of
 * the newest N, and the higher / lower / overlapping reading of value against the prior session.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { cases: { name: string; bars: CandleBar[] }[] };
const SPX = FIXTURE.cases.find((c) => c.name.startsWith("SPX-like, 15m, two"))!.bars;

const payload = (over: Partial<ProfileBarsPayload> = {}): ProfileBarsPayload => ({
  symbol: "SPX",
  available: true,
  reason: null,
  sessions: ["2026-11-12", "2026-11-13"],
  barSeconds: 900,
  bars: SPX,
  contracts: {},
  newestSession: "2026-11-13",
  fetchedAt: null,
  ...over,
});

describe("Profile tab view", () => {
  it("profiles every session on one grid, oldest first, with a composite of the newest N", () => {
    const v = buildView(payload(), 1)!;
    expect(v.rowSize).toBe(2.5); // SPX's fixed row size, whatever the price
    expect(v.profiles.map((p) => p.session)).toEqual(["2026-11-12", "2026-11-13"]);
    expect(v.composite?.sessions).toEqual(["2026-11-13"]);
    expect(v.low).toBe(Math.min(...v.profiles.map((p) => p.rows[0]!.price)));
    expect(buildView(payload(), 2)!.composite?.sessions).toEqual(["2026-11-12", "2026-11-13"]);
    expect(buildView(payload(), 0)!.composite).toBeNull();
  });

  it("names a session that holds only some periods, and not a full one", () => {
    const full = buildProfile(SPX, { rowSize: 2, barSeconds: 900, session: "2026-11-13" })!;
    expect(partialPeriods(full)).toBeNull();
    // the oldest recorded session, cut by the feed's cap at 13:30 (period I: A is 09:30, H 13:00)
    const late = buildProfile(SPX.filter((b) => b.t >= Date.parse("2026-11-13T18:30:00Z") / 1000), {
      rowSize: 2,
      barSeconds: 900,
      session: "2026-11-13",
    })!;
    expect(partialPeriods(late)).toBe("I–M");
  });

  it("draws nothing for a store with nothing in it", () => {
    expect(buildView(payload({ available: false, bars: [], sessions: [] }), 5)).toBeNull();
  });

  it("adds the sessions the live candles hold beyond the store, and only those", () => {
    const end12 = Date.parse("2026-11-12T21:00:00Z") / 1000; // 16:00 ET
    const recorded = payload({ sessions: ["2026-11-12"], bars: SPX.filter((b) => b.t < end12) });
    // the live series overlaps the recorded day; only its bars after the newest recorded bar count
    const v = buildView(recorded, 2, SPX)!;
    expect(v.profiles.map((p) => p.session)).toEqual(["2026-11-12", "2026-11-13"]);
    expect(v.live).toEqual(["2026-11-13"]);
    expect(v.composite?.sessions).toEqual(["2026-11-12", "2026-11-13"]);
    const direct = buildProfile(SPX, { rowSize: 2.5, barSeconds: 900, session: "2026-11-13" })!;
    expect([v.profiles[1]!.poc, v.profiles[1]!.val, v.profiles[1]!.vah]).toEqual([direct.poc, direct.val, direct.vah]);
    // a live bar inside the recorded day changes nothing recorded
    const tampered = SPX.map((b) => (b.t < end12 ? { ...b, h: b.h + 500 } : b));
    expect(buildView(recorded, 2, tampered)!.profiles[0]).toEqual(v.profiles[0]);
  });

  it("with nothing recorded yet, still draws the live sessions", () => {
    const v = buildView(payload({ available: false, bars: [], sessions: [], reason: "not run" }), 0, SPX)!;
    expect(v.live).toEqual(["2026-11-12", "2026-11-13"]);
    expect(buildView(payload(), 0, []).live).toEqual([]);
  });

  it("reads value as higher, lower or overlapping by the value areas' outer edges", () => {
    const base = buildProfile(SPX, { rowSize: 2, barSeconds: 900, session: "2026-11-12" })!;
    const shift = (d: number) => ({ ...base, val: base.val + d, vah: base.vah + d });
    const width = base.vah + base.rowSize - base.val;
    expect(valueRelation(base, shift(width))).toBe("higher"); // starts exactly at the prior top edge
    expect(valueRelation(base, shift(-width))).toBe("lower");
    expect(valueRelation(base, shift(width - base.rowSize))).toBe("overlapping");
    expect(valueRelation(base, base)).toBe("overlapping");
  });
});
