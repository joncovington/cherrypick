import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CandleBar } from "@console/shared";

import {
  hiddenRoles,
  hideParam,
  LINE_LEGEND,
  lineStyleFor,
  OPTIONAL_LINES,
  profileLines,
  sessionProfiles,
} from "../src/pages/Intraday/profileOverlay";

/**
 * The Intraday chart's profile overlay: which sessions it draws, when it calls one developing, and
 * where its lines sit. The profile itself is pinned in marketProfile.test.ts.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { cases: { name: string; bars: CandleBar[] }[] };
const SPX = FIXTURE.cases.find((c) => c.name.startsWith("SPX-like, 15m, two"))!.bars;

/** 2026-11-13 at an ET wall-clock time (EST, UTC-5). */
const at = (hh: number, mm: number): number => Date.UTC(2026, 10, 13, hh + 5, mm);

describe("Intraday profile overlay", () => {
  it("draws the newest session and the one before it", () => {
    const p = sessionProfiles(SPX, at(20, 0));
    expect(p.latest?.session).toBe("2026-11-13");
    expect(p.prior?.session).toBe("2026-11-12");
    expect(p.latest?.rowSize).toBe(2);
    expect(p.developing).toBe(false);
  });

  it("calls today's session developing until 16:00 ET", () => {
    expect(sessionProfiles(SPX, at(15, 59)).developing).toBe(true);
    expect(sessionProfiles(SPX, at(16, 0)).developing).toBe(false);
  });

  it("bounds the value area by its rows' outer edges and runs the POC through its row's middle", () => {
    const p = sessionProfiles(SPX, at(20, 0));
    const byKey = Object.fromEntries(profileLines(p).map((l) => [l.key, l.price]));
    const l = p.latest!;
    expect(byKey.poc).toBe(l.poc + 1);
    expect(byKey.vah).toBe(l.vah + 2);
    expect(byKey.val).toBe(l.val);
    expect(byKey.ibh).toBe(l.ibHigh);
    expect(Object.keys(byKey)).toEqual(["poc", "vah", "val", "ibh", "ibl", "ppoc", "pvah", "pval"]);
  });

  it("names every line on the axis and in the legend, and draws no two kinds alike", () => {
    const colours = { poc: "#a", va: "#b", muted: "#c", rowFill: "", vaFill: "", pocFill: "" };
    const roles = [...new Set(profileLines(sessionProfiles(SPX, at(20, 0))).map((l) => l.role))];
    expect(LINE_LEGEND.map((e) => e.role)).toEqual(roles);
    const looks = roles.map((r) => {
      const s = lineStyleFor(r, colours);
      expect(s.axisLabelVisible, r).toBe(true);
      return `${s.color}|${s.lineStyle}`;
    });
    expect(new Set(looks).size).toBe(roles.length);
  });

  it("hides only the optional groups a ?hide= names, and never POC or the value area", () => {
    const p = sessionProfiles(SPX, at(20, 0));
    const keys = (hide: string | null) => profileLines(p, hiddenRoles(hide)).map((l) => l.key);
    expect(keys(null)).toEqual(["poc", "vah", "val", "ibh", "ibl", "ppoc", "pvah", "pval"]);
    expect(keys("ib")).toEqual(["poc", "vah", "val", "ppoc", "pvah", "pval"]);
    expect(keys("ppoc,pva")).toEqual(["poc", "vah", "val", "ibh", "ibl"]);
    expect(keys("ib,ppoc,pva,poc,va,junk")).toEqual(["poc", "vah", "val"]);
  });

  it("writes ?hide= back in a stable order and empty when nothing is hidden", () => {
    expect(hideParam(hiddenRoles("pva,ib"))).toBe("ib,pva");
    expect(hideParam(new Set())).toBe("");
    expect(OPTIONAL_LINES.map((o) => o.role)).toEqual(["ib", "prior-poc", "prior-va"]);
  });

  it("draws nothing without regular-session bars", () => {
    const p = sessionProfiles([]);
    expect(p.latest).toBeNull();
    expect(profileLines(p)).toEqual([]);
  });
});
