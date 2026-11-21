import { afterEach, describe, expect, it } from "vitest";
import { readSessionsAhead, setAheadCaller } from "../src/readers/ahead.js";

/** The sessions ahead: the module's own command named per module, its document passed through. */

const DOC = {
  ok: true,
  module: "meic",
  arm: "control",
  era_start: "2026-11-03",
  start: "2026-11-21",
  min_sessions: 10,
  sessions: [{ session: "2026-11-27", weekday: "Friday", close: "13:00", early_close: true, closed_before: ["2026-11-26"], gates: [], unknown: [] }],
  gates: [{ label: "EarlyClose", name: "Half day", effect: "x", depends_on: null }],
  value_gates: [],
  records: [],
};

let calls: Array<[string, string]> = [];
afterEach(() => {
  setAheadCaller();
  calls = [];
});

describe("readSessionsAhead", () => {
  it("passes the module's document through", async () => {
    setAheadCaller((m, a) => {
      calls.push([m, a]);
      return { ok: true, error: null, json: DOC };
    });
    const out = await readSessionsAhead("meic", "control");
    expect(calls).toEqual([["meic", "control"]]);
    expect(out.error).toBeNull();
    expect(out.sessions[0]?.closed_before).toEqual(["2026-11-26"]);
    expect(out.min_sessions).toBe(10);
    expect(out.records).toEqual([]);
  });

  it("keeps an absent record as none, not an empty one", async () => {
    setAheadCaller(() => ({ ok: true, error: null, json: { ...DOC, records: null } }));
    expect((await readSessionsAhead("automations", "x-1")).records).toBeNull();
  });

  it("says why when the command refuses", async () => {
    setAheadCaller(() => ({ ok: true, error: null, json: { ok: false, error: "meic has no running arm 'zz'" } }));
    const out = await readSessionsAhead("meic", "zz");
    expect(out.error).toBe("meic has no running arm 'zz'");
    expect(out.sessions).toEqual([]);
  });
});
