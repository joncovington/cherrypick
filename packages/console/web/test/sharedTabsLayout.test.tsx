import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ArmMoneyRow, EntryOutcomes, RegimeArm, RegimeCuts } from "@console/shared";
import { CostsView } from "../src/components/costs/CostsView";
import { armHelp } from "../src/components/performance/PerformanceSlide";
import { crossTabHelp, pairedBoxes } from "../src/components/RegimeCutsTab";
import { resolveBox } from "../src/pages/Flies/FliesBoxes";

/**
 * The shared module tabs under the redesign (2026-11-07): a card's explanation sits behind its ?, the
 * long secondary tables sit in a lightbox from the parent card, and every section asks its question.
 */

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

const ARM: ArmMoneyRow = {
  arm: "control",
  positions: 10,
  wins: 6,
  premium: 1000,
  grossPnl: 200,
  fees: 40,
  settlementFees: 10,
  slippage: 20,
  netPnl: 150,
  winRate: 0.6,
};

const OUTCOME = (sessions: number): EntryOutcomes => ({ arm: "control", sessions, entered: 5, refusals: { cadence_blocked: 2 }, fills: 9 });

function costs(since: string | null, sessions = 30) {
  return text(
    <MemoryRouter>
      <CostsView
        arms={[ARM]}
        slippageInGross
        since={since}
        sinceLabel="the era's start"
        sinceRows={[OUTCOME(sessions)]}
        allRows={[OUTCOME(90)]}
        loading={false}
        premiumNote="net credit"
        gateNote="Module gate note."
        updatedAt={1}
      />
    </MemoryRouter>,
  );
}

describe("CostsView layout", () => {
  it("asks its two questions in sections", () => {
    const html = costs("2026-10-01");
    expect(html).toContain("Where did the premium go?");
    expect(html).toContain("How often did the gates let an entry through?");
    expect(html).toContain('class="pf-layout"');
  });

  it("keeps the gates note behind the card's ? and not in its body", () => {
    const html = costs("2026-10-01");
    expect(html).not.toContain("Module gate note.");
    expect(html).not.toContain("Counted per session");
    expect(html).toContain('aria-label="About entry outcomes');
  });

  it("opens the every-session table from the gates card, only when there is a boundary", () => {
    expect(costs("2026-10-01")).toContain("the same, over every session on file");
    expect(costs(null)).not.toContain("the same, over every session on file");
  });

  it("flags a thin sample on the card, since it changes how far to trust a gate", () => {
    expect(costs("2026-10-01", 8)).toContain("few sessions");
    expect(costs("2026-10-01", 30)).not.toContain("few sessions");
  });

  it("still gives fees, settlement and slippage their own columns", () => {
    const html = costs("2026-10-01");
    for (const h of ["fees", "settle", "slip (in gross)"]) expect(html).toContain(`>${h}<`);
  });
});

describe("armHelp", () => {
  it("leads with the underpowered warning only when the book is below the gate", () => {
    expect(text(<>{armHelp(true).body}</>)).toContain("Underpowered");
    expect(text(<>{armHelp(false).body}</>)).not.toContain("Underpowered");
  });
});

describe("regime cuts help and boxes", () => {
  const tab = { dims: ["gex", "trend"], arms: [{ arm: "control", cells: [] }] };

  it("says the colour is the win rate for a module with no completion concept, behind the ?", () => {
    expect(text(<>{crossTabHelp(tab, 3).body}</>)).toContain("Colour is win rate");
  });

  const arm = (dims: RegimeArm["dimensions"]): RegimeArm => ({ arm: "control", dimensions: dims } as unknown as RegimeArm);
  const pair = { a: "x", b: "y", sessions: 6, aBetterSessions: 4, bBetterSessions: 2, meanDiffPerTrade: 5, medianDiffPerTrade: 4, signTestP: 0.3 };
  const cuts = {
    module: "flies",
    dimensions: ["gex", "trend"],
    arms: [arm({ gex: { paired: [pair] }, trend: { paired: [] } } as unknown as RegimeArm["dimensions"])],
  } as unknown as RegimeCuts;

  it("gives a lightbox to each dimension that has same-day comparisons, and none to one without", () => {
    const boxes = pairedBoxes(cuts);
    expect(boxes.map((b) => b.id)).toEqual(["paired-gex"]);
    expect(resolveBox("paired-gex", boxes)?.box.id).toBe("paired-gex");
    expect(resolveBox("paired-trend", boxes)).toBeNull();
  });
});
