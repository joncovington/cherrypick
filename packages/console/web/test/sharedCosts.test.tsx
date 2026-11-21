import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { GroupMoney } from "@console/shared";
import { CostsBody } from "../src/components/costs/SharedCosts";
import { EARNINGS_SLIDES, LADDER_SLIDES, WEEKLYPUT_SLIDES } from "../src/lightbox/navGroups";

const html = (el: React.ReactElement) =>
  renderToString(<MemoryRouter>{el}</MemoryRouter>)
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

function m(over: Partial<GroupMoney> = {}): GroupMoney {
  return { trades: 3, unrecorded: 0, gross: 100, fees: 6, settlement: 1, slippage: 6, net: 87, slippage_inside_fill: false, ...over };
}

describe("the shared costs tab", () => {
  it("lays each arm's money out in its own columns, and the module's line from core", () => {
    const out = html(<CostsBody groups={[["iron_fly", m()], ["iron_condor", m({ gross: 50, net: 37 })]]} total={m({ trades: 6, gross: 150, fees: 12, settlement: 2, slippage: 12, net: 124 })} />);
    expect(out).toContain("iron_fly");
    expect(out).toContain("every arm");
    expect(out).toContain("+$150.00");
    expect(out).toContain("-$12.00");
    expect(out).toContain("$124.00");
  });

  it("says slippage is in the fill where the module concedes it there, never a zero cost", () => {
    const inside = m({ slippage: null, slippage_inside_fill: true });
    const out = html(<CostsBody groups={[["control", inside]]} total={inside} />);
    expect(out).toContain("in the fill");
    expect(out).not.toContain("-$0.00");
  });

  it("counts trades whose cost was never recorded", () => {
    expect(html(<CostsBody groups={[]} total={m({ unrecorded: 2 })} />)).toContain("2 trades have no recorded cost");
  });

  it("links an arm to its page where the module has them", () => {
    const out = html(<CostsBody groups={[["path", m()]]} total={null} armLink={(a) => `/calendars/arm/${a}`} />);
    expect(out).toContain('href="/calendars/arm/path"');
  });

  it("is a tab in earnings and ladder, with performance; weeklyput gains analysis and earnings help", () => {
    const ids = (s: readonly { id: string }[]) => s.map((x) => x.id);
    for (const t of ["performance", "costs", "guide"]) expect(ids(EARNINGS_SLIDES)).toContain(t);
    for (const t of ["performance", "costs"]) expect(ids(LADDER_SLIDES)).toContain(t);
    expect(ids(WEEKLYPUT_SLIDES)).toContain("analysis");
  });
});
