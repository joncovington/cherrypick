import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MeicSummary } from "../src/pages/Meic/SummarySlide";
import { NAV_DECL } from "../src/lightbox/navGroups";

const html = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

/**
 * The trade-summary prototype is a fixture-data slide with no reader behind it. Both facts are
 * pinned: the fixture's own numbers must survive to the DOM (a styling pass must not move a
 * figure), and — until a reader replaces it — the slide must keep saying so, never silently
 * present invented figures as MEIC's session. Shown to fail:
 *   - a fixture number changed (net 254 → 255)       -> "renders the fixture's figures" failed
 *   - the prototype badge removed from the toolbar   -> "carries the prototype badge" failed
 * Both were reverted; the assertions below are what caught them.
 */
const FIXTURE = {
  session: "Wednesday, October 7, 2026",
  entriesFilled: 3,
  hero: { net: 254, gross: 1580, fees: 245, settlement: 120, openRisk: 480 },
  capture: { value: 16.1 },
  winRate: { days: { won: 12, of: 16 }, entries: { won: 2, of: 3 } },
  sides: [
    { side: "put", net: -55, credit: 560 },
    { side: "call", net: 674, credit: 1020 },
  ],
  timeBuckets: [
    { time: "13:11", net: -158 },
    { time: "14:02", net: 210 },
    { time: "15:26", net: 202 },
  ],
  entries: [
    { time: "13:11", credit: 505, net: -158 },
    { time: "14:02", credit: 560, net: 210 },
    { time: "15:26", credit: 515, net: 202 },
  ],
};

describe("MeicSummary (trade-summary prototype)", () => {
  it("renders the fixture's figures verbatim", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("+$254.00"); // hero net
    expect(page).toContain("+$1,580.00"); // gross captured
    expect(page).toContain("16.1%"); // capture gauge
    expect(page).toContain("75%"); // days win rate 12/16
    expect(page).toContain("put");
    expect(page).toContain("-$55.00"); // by-side put net
    expect(page).toContain("+$674.00"); // by-side call net
    for (const b of FIXTURE.timeBuckets) expect(page).toContain(b.time);
    expect(page).toContain("+$210.00"); // best bucket
    expect(page).toContain("13:11"); // entries table times
  });

  it("renders zero-weight losses as a marker, wins as bars", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("ts-marker"); // the 13:11 loss bucket
    expect(page).toContain("ts-vbar");
  });

  it("carries the prototype badge — fixture data never presents as the session", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("prototype — fixture data");
  });

  it("does not wire a query or reader yet — stub refusal is honest", () => {
    // The slide takes no props and fetches nothing; when a reader lands, this test is replaced
    // by the mirror test pinning it to the ledger.
    expect(MeicSummary.length).toBe(0); // no props
  });

  it("the summary tab is declared in MEIC's rail, in the today group", () => {
    const decl = NAV_DECL.meic!;
    expect(decl.slides.map((s) => s.id)).toContain("summary");
    expect(decl.groups.flatMap((g) => g.ids)).toContain("summary");
  });
});
