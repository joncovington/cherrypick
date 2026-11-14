import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CandleBar } from "@console/shared";

import {
  hiddenRoles,
  hideParam,
  showParam,
  toggleRole,
  compositeSessions,
  LINE_LEGEND,
  lineStyleFor,
  OPTIONAL_LINES,
  profileLines,
  sessionProfiles,
  untestedBars,
  untestedCandidates,
  overnightSession,
} from "../src/pages/Intraday/profileOverlay";
import { recordedProfileSymbol } from "../src/pages/Intraday/IntradayPage";
import { overnightProfile } from "../src/lib/marketProfile";

/**
 * The Intraday chart's profile overlay: which sessions it draws, when it calls one developing, and
 * where its lines sit. The profile itself is pinned in marketProfile.test.ts.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { cases: { name: string; bars: CandleBar[] }[] };
const SPX = FIXTURE.cases.find((c) => c.name.startsWith("SPX-like, 15m, two"))!.bars;
const NQ = FIXTURE.cases.find((c) => c.name.startsWith("/NQ-like"))!.bars;
const ON = overnightProfile(NQ, { session: "2026-11-13", rowSize: 10, barSeconds: 900 });

describe("the composite lines", () => {
  it("are off until ?show=comp, and the rest are on until ?hide=", () => {
    expect([...hiddenRoles(null)].sort()).toEqual(["composite-poc", "composite-va"]);
    expect(hiddenRoles(null, "comp").size).toBe(0);
    expect([...hiddenRoles("ib", "comp")]).toEqual(["ib"]);
    expect(hiddenRoles("comp", null).has("composite-poc")).toBe(true); // ?hide= cannot show it
  });

  it("switch together, and write back to ?show= only while on", () => {
    const off = hiddenRoles(null);
    const on = toggleRole(off, "composite-va");
    expect(on.has("composite-poc") || on.has("composite-va")).toBe(false);
    expect([showParam(on), hideParam(on)]).toEqual(["comp", ""]);
    const offAgain = toggleRole(on, "composite-poc");
    expect([showParam(offAgain), hideParam(offAgain)]).toEqual(["", ""]);
    // a default-on group still writes to ?hide=
    expect(hideParam(toggleRole(on, "ib"))).toBe("ib");
  });

  it("draw the composite's POC through its row's middle and its value area by outer edges", () => {
    const p = sessionProfiles(NQ, Date.UTC(2026, 10, 13, 22, 0));
    const lines = profileLines(p, new Set(), [], null, ON).filter((l) => l.role.startsWith("composite"));
    expect(lines.map((l) => [l.key, l.price])).toEqual([
      ["cpoc", ON!.poc + 5],
      ["cvah", ON!.vah + 10],
      ["cval", ON!.val],
    ]);
    expect(profileLines(p, hiddenRoles(null), [], null, ON).some((l) => l.role.startsWith("composite"))).toBe(false);
  });

  it("merge the newest five finished sessions, never today's while it trades", () => {
    const recorded = ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"];
    const during = sessionProfiles(SPX, at(12, 0)); // 11-13 developing
    const after = sessionProfiles(SPX, at(20, 0));
    expect(compositeSessions(recorded, during)).toEqual(["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]);
    expect(compositeSessions(recorded, after)).toEqual(["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]);
  });
});

describe("the overnight lines", () => {
  it("draws the overnight's high, POC (through its row's middle) and low, hidden by ?hide=on", () => {
    const p = sessionProfiles(NQ, Date.UTC(2026, 10, 13, 22, 0));
    const on = profileLines(p, new Set(), [], ON).filter((l) => l.role === "overnight");
    expect(on.map((l) => [l.title, l.price])).toEqual([
      ["ONH", ON!.high],
      ["ON POC", ON!.poc + 5],
      ["ONL", ON!.low],
    ]);
    expect(profileLines(p, hiddenRoles("on"), [], ON).some((l) => l.role === "overnight")).toBe(false);
  });

  it("shows the overnight under way, or the one before the newest session", () => {
    const bar = (iso: string) => [{ t: Date.parse(iso) / 1000, o: 1, h: 1, l: 1, c: 1, v: 0 }];
    expect(overnightSession(bar("2026-11-13T12:00:00Z"))).toBe("2026-11-13"); // Fri 07:00 ET, before the open
    expect(overnightSession(bar("2026-11-13T15:00:00Z"))).toBe("2026-11-13"); // Fri 10:00, the session's own
    expect(overnightSession(bar("2026-11-13T21:55:00Z"))).toBe("2026-11-13"); // Fri 16:55, the weekend reads Friday's
    expect(overnightSession(bar("2026-11-15T23:15:00Z"))).toBe("2026-11-16"); // Sun 18:15, Monday's
    expect(overnightSession([])).toBeNull();
  });
});

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
    const roles = [...new Set(profileLines(sessionProfiles(SPX, at(20, 0)), new Set(), [["2026-11-10", 6800]], ON, ON).map((l) => l.role))];
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
    expect(OPTIONAL_LINES.map((o) => o.role)).toEqual(["ib", "prior-poc", "prior-va", "untested-poc", "overnight", "composite-poc", "composite-va"]);
  });

  it("draws each untested POC through its row's middle, named by its session, hidden by ?hide=npoc", () => {
    const p = sessionProfiles(SPX, at(20, 0));
    const lines = profileLines(p, new Set(), [["2026-11-06", 6800], ["2026-11-10", 6850]]);
    const npoc = lines.filter((l) => l.role === "untested-poc");
    expect(npoc.map((l) => [l.title, l.price])).toEqual([
      ["nPOC Nov 6", 6801],
      ["nPOC Nov 10", 6851],
    ]);
    expect(profileLines(p, hiddenRoles("npoc"), [["2026-11-06", 6800]]).some((l) => l.role === "untested-poc")).toBe(false);
  });

  it("names only sessions the chart has no POC line for yet", () => {
    const p = sessionProfiles(SPX, at(20, 0)); // newest 11-13, prior 11-12
    expect(untestedCandidates(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"], p)).toEqual(["2026-11-10", "2026-11-11"]);
  });

  it("reads recorded bars, then live ones only after the newest recorded bar", () => {
    const rec = [
      { t: 100, o: 1, h: 1, l: 1, c: 1, v: 0 },
      { t: 200, o: 2, h: 2, l: 2, c: 2, v: 0 },
    ];
    const live = [
      { t: 150, o: 9, h: 9, l: 9, c: 9, v: 0 },
      { t: 300, o: 3, h: 3, l: 3, c: 3, v: 0 },
    ];
    expect(untestedBars(rec, live).map((b) => b.t)).toEqual([100, 200, 300]);
    expect(untestedBars([], live).map((b) => b.t)).toEqual([150, 300]);
  });

  it("asks the store only for the symbols it records", () => {
    expect(["SPX", "SPY", "NDX", "QQQ", "ES", "NQ"].map(recordedProfileSymbol)).toEqual(["SPX", "SPY", "NDX", "QQQ", "/ES", "/NQ"]);
    expect(["CL", "GC", "ZB"].map(recordedProfileSymbol)).toEqual(["", "", ""]);
  });

  it("draws nothing without regular-session bars", () => {
    const p = sessionProfiles([]);
    expect(p.latest).toBeNull();
    expect(profileLines(p)).toEqual([]);
  });
});
