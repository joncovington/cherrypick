import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationsGroupUnit, AutomationsGroupsRead, AutomationsLine, AutomationsPerformance } from "@console/shared";
import { GroupUnitBody, groupHref, groupRows, NO_GROUP_LABEL } from "../src/pages/Automations/GroupUnit";
import { AutomationsCosts, AutomationsPerformancePage } from "../src/pages/Automations/AutomationsPages";

const html = (el: React.ReactElement, url = "/") =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

function line(net: number, drawdown = 0): AutomationsLine {
  return { trades: 3, net, gross: net + 9, fees: 3, settlement: 0, slippage: 6, netStress: net - 3, netMid: net + 6, wins: 2, mean: net / 3, worst: -50, drawdown, curve: [{ x: "2026-11-03", y: net }] };
}

const UNIT: AutomationsGroupUnit = {
  name: "ensemble",
  enabled: true,
  allocation: 10000,
  max_risk: 2000,
  shares: { a: 60, b: 40 },
  members: ["a", "b"],
  line: line(-200, 500),
  against_allocation: { net: -0.02, drawdown: 0.05 },
  risk: { peak: 1600, session: "2026-11-06", cap: 2000, of_cap: 0.8 },
  member_risk: [
    { name: "a", share: 60, peak: 900, session: "2026-11-06", limit: 1200, of_limit: 0.75 },
    { name: "b", share: 40, peak: 900, session: "2026-11-05", limit: 800, of_limit: 1.125 },
  ],
  refused: { cap: 2, share: 1, unknown: 0, latest: [{ session: "2026-11-05", automation: "a", by: "cap" }] },
  together: {
    pairs: [{ a: "a", b: "b", zero_filled: 0.42, zero_filled_periods: 31, similarity: 0.8, similarity_periods: 12, thin: true }],
    effective_members: 1.38,
    members_counted: ["a", "b"],
    shared_periods: 31,
    thin: false,
    drawdown: { combined: 500, sum_of_arms: 800, benefit: 0.375 },
    min_periods: 20,
  },
};

describe("a group as a unit", () => {
  it("reads its net, drawdown and risk against its allocation and cap, and what it refused", () => {
    const out = html(<GroupUnitBody g={UNIT} />);
    expect(out).toContain("-2.0% of its $10000.00 allocation");
    expect(out).toContain("5.0% of its allocation");
    expect(out).toContain("80.0% of its $2000.00 cap, on 2026-11-06");
    expect(out).toContain("2 by the cap, 1 by a member&#x27;s share");
    // A member over its share is marked.
    expect(out).toContain('class="pnl-neg">$800.00 · 112.5%');
  });

  it("says how its members move together, and marks what is thin", () => {
    const out = html(<GroupUnitBody g={UNIT} />);
    expect(out).toContain("1.38 of 2 counted");
    expect(out).toContain("37.5% less");
    expect(out).toContain("0.42");
    expect(out).toContain("grp-thin");
  });

  it("says so when fewer than two members have closed a trade", () => {
    expect(html(<GroupUnitBody g={{ ...UNIT, together: null }} />)).toContain("Fewer than two of its members");
  });

  it("lists each group's line and the automations in none, each name leading to Groups", () => {
    const read: AutomationsGroupsRead = { groups: [UNIT], ungrouped: { members: ["c"], line: line(999) }, error: null };
    expect(groupRows(read).map(([n]) => n)).toEqual(["ensemble", NO_GROUP_LABEL]);
    expect(groupRows({ ...read, ungrouped: { members: [], line: line(0) } }).map(([n]) => n)).toEqual(["ensemble"]);
    expect(groupHref("ensemble")).toBe("/automations/groups?group=ensemble");
    expect(groupHref(NO_GROUP_LABEL)).toBe("/automations/groups?group=_none");
  });
});

describe("performance and costs by group", () => {
  const perf: AutomationsPerformance = { dbPresent: true, arms: { a: line(100) }, combined: line(100) };

  it("shows each automation by default, with the switch", () => {
    const out = html(<AutomationsPerformancePage perf={perf} />);
    expect(out).toContain("net and drawdown by arm");
    expect(out).toContain('aria-pressed="true">by automation');
  });

  it("switches to groups from the address", () => {
    const out = html(<AutomationsPerformancePage perf={perf} />, "/automations/performance?by=group");
    expect(out).toContain("net and drawdown by group");
    expect(out).toContain('aria-pressed="true">by group');
    const costs = html(<AutomationsCosts perf={perf} />, "/automations/costs?by=group");
    expect(costs).toContain("costs by group");
  });
});
