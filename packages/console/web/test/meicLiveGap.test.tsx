import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { MeicLiveGapPayload, MeicLiveGapSide } from "@console/shared";
import { MeicLiveGapView } from "../src/pages/Live/MeicLiveGap";

/**
 * MEIC live against paper, the card (docs/console-review-plan.md batch 5): the reader's pairs and
 * totals shown as given, a session live sat out marked as such, and an empty or absent ledger said
 * in words rather than drawn as zeros.
 */

const side = (over: Partial<MeicLiveGapSide>): MeicLiveGapSide => ({
  entry: "2026-10-20 14:00",
  symbol: "SPX",
  putStrike: 7600,
  callStrike: 7650,
  width: 10,
  credit: 1.6,
  status: "expired",
  gross: 160,
  fees: 7,
  net: 153,
  ...over,
});

const payload = (over: Partial<MeicLiveGapPayload> = {}): MeicLiveGapPayload => ({
  twin: "live-shadow",
  ledger: "ok",
  since: "2026-10-20",
  rows: [
    { session: "2026-10-21", live: null, paper: side({ net: 175 }), gap: null },
    { session: "2026-10-20", live: side({}), paper: side({ credit: 1.62, net: 157 }), gap: -4 },
  ],
  totals: { paired: 1, liveNet: 153, paperNet: 157, gap: -4, paperOnly: 1, paperOnlyNet: 175, liveOnly: 0 },
  ...over,
});

function render(p: MeicLiveGapPayload): string {
  return renderToString(<MeicLiveGapView d={p} />)
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

describe("MeicLiveGapView", () => {
  it("states the paired totals, the gap and what live sat out", () => {
    const html = render(payload());
    expect(html).toContain("live vs paper — live-shadow");
    expect(html).toContain("1 session both settled");
    expect(html).toContain("$153.00");
    expect(html).toContain("-$4.00");
    expect(html).toContain("paper traded 1 session live sat out ($175.00)");
  });

  it("marks a session live did not trade, and shows each side's condor", () => {
    const html = render(payload());
    expect(html).toMatch(/data-session="2026-10-21"[^>]*>|class="muted" data-session="2026-10-21"/);
    expect(html).toContain("not traded");
    expect(html).toContain("SPX 7600/7650 @ 1.62 · expired");
  });

  it("says so when there is no live ledger or no live trade", () => {
    expect(render(payload({ ledger: "absent", rows: [] }))).toContain("no MEIC live ledger on this machine");
    expect(render(payload({ rows: [] }))).toContain("the live loop has not traded yet");
  });
});
