import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { SessionsAhead } from "@console/shared";
import { SessionsAheadBody } from "../src/components/armpage/SessionsAhead";

const html = (doc: SessionsAhead) => renderToString(<SessionsAheadBody doc={doc} />).replace(/<!--\s*-->/g, "");

const DOC: SessionsAhead = {
  module: "meic",
  arm: "control",
  era_start: "2026-11-03",
  start: "2026-11-21",
  min_sessions: 10,
  sessions: [
    { session: "2026-11-25", weekday: "Wednesday", close: "16:00", early_close: false, closed_before: [], gates: [], unknown: [] },
    {
      session: "2026-11-27",
      weekday: "Friday",
      close: "13:00",
      early_close: true,
      closed_before: ["2026-11-26"],
      gates: [{ label: "EarlyClose", name: "Half day", effect: "The market closes at 13:00.", depends_on: null }],
      unknown: ["CPI"],
    },
  ],
  gates: [],
  value_gates: [{ name: "VIX", effect: "No entries while VIX is above 25.", depends_on: "VIX at the time" }],
  records: [
    { label: "FOMC", name: "Fed decision (FOMC)", sessions: 1, other_sessions: 13, unknown_sessions: 0, readable: false, to_go: 9, net: null, mean: null, other_mean: null },
    { label: "EarlyClose", name: "Half day", sessions: 10, other_sessions: 40, unknown_sessions: 2, readable: true, to_go: 0, net: 500, mean: 50, other_mean: -12.5 },
  ],
  error: null,
};

describe("the sessions ahead", () => {
  it("lists each session with its dated gates, the closed day and what the calendar cannot say", () => {
    const out = html(DOC);
    expect(out).toContain("Wed 11-25");
    expect(out).toContain("no dated gate");
    expect(out).toContain("market closed 11-26");
    expect(out).toContain('<span class="chip chip-warn">13:00</span>');
    expect(out).toContain("<b>Half day</b> The market closes at 13:00.");
    expect(out).toContain("The calendar does not say for CPI.");
  });

  it("lists the value gates once, with what each depends on", () => {
    const out = html(DOC);
    expect(out).toContain("<b>VIX</b> No entries while VIX is above 25.");
    expect(out).toContain("Depends on VIX at the time.");
  });

  it("counts a thin record and reads one only once it has enough", () => {
    const out = html(DOC);
    expect(out).toContain("1 so far, too few to read: 9 more needed");
    expect(out).toContain("$50.00 a session over 10, against -$12.50 on the other 40");
    expect(out).toContain("2 sessions the calendar does not say for");
  });

  it("has no record card without a record, and says why on an error", () => {
    expect(html({ ...DOC, records: null })).not.toContain("its record on these days");
    expect(html({ ...DOC, error: "meic has no running arm 'zz'" })).toContain("meic has no running arm &#x27;zz&#x27;");
  });
});
