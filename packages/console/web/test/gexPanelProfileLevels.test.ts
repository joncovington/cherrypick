import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CandleBar } from "@console/shared";

import { profileLevels } from "../src/pages/GexPanel/profileLevels";
import { sessionProfiles } from "../src/pages/Intraday/profileOverlay";

/**
 * The GEX panel's profile levels: today's POC while the session runs, else the newest finished
 * session as the prior one (the panel then shows the next session's chain), its POC and value area by
 * the same edges the intraday chart uses, and untested POCs other than the prior session's.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { cases: { name: string; bars: CandleBar[] }[] };
const SPX = FIXTURE.cases.find((c) => c.name.startsWith("SPX-like, 15m, two"))!.bars;
const at = (hh: number, mm: number): number => Date.UTC(2026, 10, 13, hh + 5, mm); // 11-13, EST

describe("GEX panel profile levels", () => {
  it("during the session: today's POC, and the session before as the prior", () => {
    const p = sessionProfiles(SPX, at(12, 0), "SPX");
    const levels = profileLevels(p, []);
    expect(levels.map((l) => l.title)).toEqual(["POC", "pPOC", "pVAH", "pVAL"]);
    expect(levels[0]!.price).toBe(p.latest!.poc + 1.25);
    expect(levels[1]!.price).toBe(p.prior!.poc + 1.25);
    expect(levels[2]!.price).toBe(p.prior!.vah + 2.5);
    expect(levels[3]!.price).toBe(p.prior!.val);
  });

  it("after the close: the newest finished session is the prior, and no today POC", () => {
    const p = sessionProfiles(SPX, at(20, 0), "SPX");
    const levels = profileLevels(p, [
      ["2026-11-06", 6750],
      ["2026-11-13", 6844],
    ]);
    expect(levels.map((l) => l.title)).toEqual(["pPOC", "pVAH", "pVAL", "nPOC 11-06"]);
    expect(levels[0]!.price).toBe(p.latest!.poc + 1.25);
    expect(levels[3]).toMatchObject({ price: 6751.25, style: "dashed", token: "--amber-line" });
  });

  it("draws nothing without bars", () => {
    expect(profileLevels(sessionProfiles([], at(12, 0), "SPX"), [])).toEqual([]);
  });
});
