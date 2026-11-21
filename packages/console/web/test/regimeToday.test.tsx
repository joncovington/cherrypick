import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { RegimeArm, RegimeCuts, RegimeTodayCell } from "@console/shared";
import { TodayConditionsCard } from "../src/components/RegimeCutsTab";

/** Today's conditions: each dimension's bucket, and the arm's figures there only where the writer read them. */

const html = (cuts: RegimeCuts, arm = "control") => renderToString(<TodayConditionsCard cuts={cuts} arm={arm} />).replace(/<!--\s*-->/g, "");

function c(over: Partial<RegimeTodayCell>): RegimeTodayCell {
  return { bucket: null, readable: false, why: null, sessions: null, trades: null, netPnl: null, avgPnl: null, winRate: null, thin: null, fragile: null, ...over };
}

const ARM = {
  arm: "control",
  today: {
    gex: c({ bucket: "negative", readable: true, sessions: 11, trades: 3031, netPnl: -51318.32, avgPnl: -16.93, fragile: true }),
    trend: c({ bucket: "down_from_open", why: "underpowered" }),
    vol_implied: c({ bucket: "high", why: "thin", sessions: 2, netPnl: 40 }),
  },
} as unknown as RegimeArm;

const CUTS = {
  module: "meic",
  session: "2026-11-20",
  today: { session: "2026-11-20", at: "2026-11-20 16:04:27.848751-05:00", buckets: { gex: "negative", trend: "down_from_open", vol_implied: "high" } },
  arms: [ARM],
} as unknown as RegimeCuts;

describe("today's conditions", () => {
  it("names the session's last reading and each dimension's bucket", () => {
    const out = html(CUTS);
    expect(out).toContain("2026-11-20&#x27;s last reading (16:04 ET)");
    expect(out).toContain("<td>gex</td><td>negative</td>");
    expect(out).toContain("<td>trend</td><td>down_from_open</td>");
  });

  it("gives figures only where the cut can carry them, and the reason elsewhere", () => {
    const out = html(CUTS);
    expect(out).toContain('<span class="pnl-neg">-$51318.32</span>');
    expect(out).toContain("fragile");
    expect(out).toContain("too few sessions for a regime claim yet");
    expect(out).toContain("too few sessions in this bucket to read");
    expect(out).not.toContain("$40.00"); // a thin cell's net is held, never shown
  });

  it("renders nothing on an artifact written before the stamp, or for an arm not in the cut", () => {
    expect(html({ ...CUTS, today: null } as RegimeCuts)).toBe("");
    expect(html({ ...CUTS, today: undefined } as unknown as RegimeCuts)).toBe("");
    expect(html(CUTS, "bp-10k")).toBe("");
  });
});
