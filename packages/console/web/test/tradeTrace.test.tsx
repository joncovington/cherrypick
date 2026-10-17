import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { PnlTrace } from "@console/shared";
import { TraceView } from "../src/pages/Compare/TradeTrace";

const text = (t: PnlTrace) => renderToString(<TraceView t={t} />).replace(/<!--\s*-->/g, "");

const trace: PnlTrace = {
  ok: true,
  found: true,
  entry: {
    recorded: true,
    basis: "matched",
    session: "2026-10-15",
    attempts_before: 3,
    runs: [
      { outcome: "spread_too_wide", block_detail: "0.35", count: 2, first: "2026-10-15T10:30:00-04:00", last: "2026-10-15T10:31:00-04:00" },
      { outcome: "no_fresh_quotes", block_detail: null, count: 1, first: "2026-10-15T10:32:00-04:00", last: "2026-10-15T10:32:00-04:00" },
    ],
    runs_omitted: 0,
    filled: { at: "2026-10-15T11:00:00-04:00", detail: { body_strike: 7650 } },
  },
  orders: { recorded: false, reason: "this module records no orders per position" },
  management: {
    recorded: true,
    events: 4,
    runs: [
      { action: "fire_addon", reason: "addon_credit_met", executed: false, gate: "spread_too_wide", count: 2, first: "2026-10-15T12:00:00-04:00", last: "2026-10-15T12:01:00-04:00", detail: { credit: 0.05 } },
      { action: "fire_addon", reason: "addon_credit_met", executed: true, gate: null, count: 1, first: "2026-10-15T12:02:00-04:00", last: "2026-10-15T12:02:00-04:00", detail: { credit: 0.1 } },
    ],
    runs_omitted: 75,
    executed: [],
  },
};

describe("TraceView", () => {
  it("shows the entry runs, the fill, and how the fill was found", () => {
    const html = text(trace);
    for (const s of ["3 attempts since the arm", "matched by session", "spread_too_wide", "×2", "10-15 10:30–10:31", "filled", "body_strike 7650"]) {
      expect(html).toContain(s);
    }
  });

  it("tells a considered action from a done one, names the gate that held it, and says what it left out", () => {
    const html = text(trace);
    expect(html).toContain("fire_addon (considered)");
    expect(html).toContain("held by spread_too_wide");
    expect(html).toContain("fire_addon (done)");
    expect(html).toContain("75 more runs in the middle, left out");
  });

  it("says why a part is missing rather than showing an empty table", () => {
    const html = text({ ok: true, found: true, entry: { recorded: false, reason: "this module records no entry attempts" }, management: { recorded: false, reason: "this module keeps no per-position management events" } });
    expect(html).toContain("entry attempts: not recorded — this module records no entry attempts");
    expect(html).toContain("management: not recorded — this module keeps no per-position management events");
  });
});
