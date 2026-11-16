import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationGroupsResult } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { GroupsCard } = await import("../src/pages/Automations/AutomationsPages");
const { renamedTarget } = await import("../src/App");

const html = (el: React.ReactElement, at = "/") =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[at]}>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

const RESULT: AutomationGroupsResult = {
  error: null,
  groups: [
    {
      name: "ensemble",
      enabled: false,
      automations: [
        { name: "iron-condor-1345", enabled: false, verdict: "passed" },
        { name: "iron-fly-1000", enabled: false, verdict: "failed" },
        { name: "put-spread-1300", enabled: false, verdict: "no_verdict" },
      ],
    },
  ],
};

describe("the automation groups card", () => {
  it("shows each group's switch and each automation's verdict", () => {
    const out = html(<GroupsCard result={RESULT} loading={false} />);
    expect(out).toContain("ensemble");
    expect(out).toContain("iron-condor-1345");
    expect(out).toContain(">passed<");
    expect(out).toContain(">failed<");
    expect(out).toContain(">no verdict<");
    expect(out).toContain(">off<");
  });

  it("shows the group's own switch apart from each automation's", () => {
    const g = { name: "mine", enabled: false, automations: [{ name: "a1", enabled: true, verdict: null }] };
    const out = html(<GroupsCard result={{ error: null, groups: [g] }} loading={false} />);
    expect(out).toContain('class="chip chip-warn">off</span>');
    expect(out).toContain('class="muted">on</td>');
  });

  it("says why when the package could not be asked", () => {
    expect(html(<GroupsCard result={{ groups: [], error: "automation groups unavailable" }} loading={false} />)).toContain(
      "automation groups unavailable",
    );
  });
});

describe("the ensemble page's old address", () => {
  it("follows the page to automations with its tab and query", () => {
    expect(renamedTarget("/automations", undefined, "")).toBe("/automations");
    expect(renamedTarget("/automations", "positions", "?arm=x")).toBe("/automations/positions?arm=x");
  });
});
