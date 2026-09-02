import { describe, it, expect, afterEach } from "vitest";
import {
  readCalendarsPlan,
  readCalendarsPolicies,
  resetCalendarsCache,
  setCalendarsCaller,
} from "../src/services/calendarsBridge.js";

/**
 * The console asks the calendars module for its exit-policy table and its week anchors rather than
 * deriving either. Both would be re-implementations rather than reads: the table is a tick-by-tick
 * replay welded to the validation that reproduces the real books to the cent, and the anchors are
 * holiday-calendar arithmetic whose structure tag is the key every result is grouped by.
 *
 * What these tests pin is the part a TypeScript layer can still get wrong: the shape it hands on,
 * the memoisation the page's polling depends on, and — the one that matters — that an unavailable
 * derivation is reported as an error rather than as an empty table.
 */

afterEach(() => {
  setCalendarsCaller();
  resetCalendarsCache();
});

const PLAN = {
  ok: true,
  week_plan: {
    week_of: "2026-08-17",
    entry_session: "2026-08-17",
    front_expiration: "2026-08-21",
    back_expiration: "2026-08-24",
    structure: "dc_4_7",
  },
  open_positions: [],
};

const POLICIES = {
  ok: true,
  policies: {
    policies: {
      control: {
        dc_4_7: { weeks: 3, derivable: 2, total_net: -41.5, wins: 1, worst: { week_of: "2026-08-24", net_pnl: -60 }, avg_net: -20.75, win_rate: 0.5 },
      },
      "pt-20": {
        dc_4_7: { weeks: 3, derivable: 3, total_net: 88.0, wins: 2, worst: null, avg_net: 29.33, win_rate: 0.6667 },
      },
    },
    weeks_considered: 3,
    caveat: "triggers are evaluated at the recorded tick cadence",
    measurement_breaks: [],
    validation: { compared: 4, ok: true, mismatches: [] },
  },
};

describe("the calendars bridge", () => {
  it("passes the week anchors through as the module computed them", () => {
    setCalendarsCaller(() => ({ ok: true, json: PLAN, error: null }));
    const out = readCalendarsPlan(1_000);
    expect(out.error).toBeNull();
    expect(out.plan).toEqual({
      weekOf: "2026-08-17",
      entrySession: "2026-08-17",
      frontExpiration: "2026-08-21",
      backExpiration: "2026-08-24",
      structure: "dc_4_7",
    });
  });

  it("treats a null week plan as an answer, not a failure", () => {
    // The module returns None when the calendar cannot produce a week. That is a real state and
    // must not be reported as a broken bridge.
    setCalendarsCaller(() => ({ ok: true, json: { ok: true, week_plan: null }, error: null }));
    const out = readCalendarsPlan(1_000);
    expect(out.plan).toBeNull();
    expect(out.error).toBeNull();
  });

  it("keeps each policy's structure buckets separate", () => {
    // Distinct structure tags are distinct trades and never pool. A shape that flattened them here
    // would be the module's fourth honesty rule broken where it is least visible.
    setCalendarsCaller(() => ({ ok: true, json: POLICIES, error: null }));
    const out = readCalendarsPolicies(1_000);
    expect(out.ok).toBe(true);
    expect(out.weeksConsidered).toBe(3);
    const control = out.policies.find((p) => p.policy === "control");
    expect(control?.buckets).toHaveLength(1);
    expect(control?.buckets[0]).toMatchObject({ structure: "dc_4_7", weeks: 3, derivable: 2, avgNet: -20.75 });
    expect(control?.buckets[0].worst).toEqual({ weekOf: "2026-08-24", netPnl: -60 });
  });

  it("carries the validation with the table, never separately", () => {
    setCalendarsCaller(() => ({ ok: true, json: POLICIES, error: null }));
    const out = readCalendarsPolicies(1_000);
    expect(out.validation).toEqual({ compared: 4, ok: true, mismatches: [] });
  });

  it("keeps a worst-week of null rather than inventing a zero", () => {
    setCalendarsCaller(() => ({ ok: true, json: POLICIES, error: null }));
    const out = readCalendarsPolicies(1_000);
    expect(out.policies.find((p) => p.policy === "pt-20")?.buckets[0].worst).toBeNull();
  });

  it("memoises, because a replay over the whole mark path is a subprocess and the page polls", () => {
    let calls = 0;
    setCalendarsCaller(() => {
      calls += 1;
      return { ok: true, json: POLICIES, error: null };
    });
    readCalendarsPolicies(1_000);
    readCalendarsPolicies(60_000);
    expect(calls).toBe(1);
    // Past the TTL it asks again — a week completing since then would change the answer.
    readCalendarsPolicies(1_000 + 400_000);
    expect(calls).toBe(2);
  });

  it("memoises the plan and the table under separate keys", () => {
    const seen: string[] = [];
    setCalendarsCaller((verb) => {
      seen.push(verb);
      return { ok: true, json: verb === "status" ? PLAN : POLICIES, error: null };
    });
    readCalendarsPlan(1_000);
    readCalendarsPolicies(1_000);
    expect(seen).toEqual(["status", "policies"]);
  });

  it("reports an unavailable module as an error rather than an empty table", () => {
    // An empty policy table reads as "no exit rule works", which is a finding. It must never be
    // produced by a missing package.
    setCalendarsCaller(() => ({ ok: false, json: null, error: "calendars analytics unavailable — ..." }));
    const out = readCalendarsPolicies(1_000);
    expect(out.ok).toBe(false);
    expect(out.policies).toEqual([]);
    expect(out.validation).toBeNull();
    expect(out.error).toContain("unavailable");
  });
});
