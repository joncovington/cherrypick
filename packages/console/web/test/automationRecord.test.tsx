import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationsLine, AutomationsPerformance, GroupsEditMember, GroupsEditView } from "@console/shared";
import { AutomationRecordPage, findAutomation } from "../src/pages/Automations/AutomationRecord";
import { AutomationsGroupsPage } from "../src/pages/Automations/AutomationsGroups";
import { automationHref } from "../src/pages/Automations/automationsModels";
import { railSlide } from "../src/components/shell/Shell";

const m = (name: string, over: Partial<GroupsEditMember> = {}): GroupsEditMember => ({
  name,
  enabled: true,
  start: null,
  state: "off_group",
  from: null,
  verdict: "no_verdict",
  symbol: "SPX",
  shipped: true,
  forward: false,
  ...over,
});

const VIEW: GroupsEditView = {
  today: "2026-11-20",
  next_session: "2026-11-23",
  counts: { on: 0, waiting: 0, off_group: 1, off_own: 1 },
  groups: [{ name: "ensemble", enabled: false, members: [m("iron-condor-1345", { verdict: "passed" })] }],
  ungrouped: [m("par-a1-fly-1000", { enabled: false, state: "off_own", verdict: null })],
};

const line = (over: Partial<AutomationsLine>): AutomationsLine => ({
  trades: 0,
  net: 0,
  gross: 0,
  fees: 0,
  settlement: 0,
  slippage: 0,
  netStress: 0,
  netMid: 0,
  wins: 0,
  mean: null,
  worst: null,
  drawdown: 0,
  curve: [],
  ...over,
});

function client(view: GroupsEditView = VIEW, arms: Record<string, AutomationsLine> = {}): QueryClient {
  const c = new QueryClient();
  c.setQueryData(["automations-groups-edit"], view);
  const perf: AutomationsPerformance = { dbPresent: true, arms, combined: line({}) };
  c.setQueryData(["automations-performance"], perf);
  return c;
}

const html = (el: React.ReactElement, c: QueryClient, url = "/") =>
  renderToString(
    <QueryClientProvider client={c}>
      <MemoryRouter initialEntries={[url]}>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

describe("one automation's page", () => {
  it("has its own address, the name escaped", () => {
    expect(automationHref("iron-condor-1345")).toBe("/automations/automation/iron-condor-1345");
    expect(automationHref("a b/c")).toBe("/automations/automation/a%20b%2Fc");
  });

  it("finds the automation and its group, or none", () => {
    expect(findAutomation(VIEW, "iron-condor-1345")).toMatchObject({ group: "ensemble", m: { verdict: "passed" } });
    expect(findAutomation(VIEW, "par-a1-fly-1000")).toMatchObject({ group: null });
    expect(findAutomation(VIEW, "nope")).toBeUndefined();
  });

  it("reads the rule, the record and the decisions, in that order, and changes nothing", () => {
    const out = html(<AutomationRecordPage name="iron-condor-1345" />, client(VIEW, { "iron-condor-1345": line({ trades: 9, wins: 7, net: 128, drawdown: 388 }) }));
    expect(out).toContain('<a href="/automations/groups">groups</a> / <a href="/automations/groups?group=ensemble">ensemble</a> / <b>iron-condor-1345</b>');
    const rule = out.indexOf('id="automation-rule"');
    const record = out.indexOf('id="automation-record"');
    const decisions = out.indexOf('id="automation-decisions"');
    expect(rule).toBeGreaterThan(-1);
    expect(out.indexOf('id="automation-working"')).toBeGreaterThan(rule);
    expect(record).toBeGreaterThan(out.indexOf('id="automation-working"'));
    const entries = out.indexOf('id="automation-entries"');
    expect(entries).toBeGreaterThan(record);
    const ahead = out.indexOf('id="automation-ahead"');
    expect(ahead).toBeGreaterThan(entries);
    expect(decisions).toBeGreaterThan(ahead);
    expect(out).toContain('href="#automation-ahead"');
    expect(out).toContain('href="#automation-record"');
    expect(out).toContain('href="#automation-entries"');
    expect(out).toContain('href="/automations/analysis?an-arm=iron-condor-1345"');
    expect(out).toContain("off · its group");
    expect(out).toContain('href="/automations/groups?group=ensemble&amp;automation=iron-condor-1345"');
    expect(out).toContain('href="/automations/builder?automation=iron-condor-1345"');
    expect(out).toContain("$128.00");
    expect(out).toContain("7 with a net above zero");
    expect(out).not.toContain("<input");
    expect(out).not.toContain("<select");
  });

  it("names an automation in no group, and its switches drill-in", () => {
    const out = html(<AutomationRecordPage name="par-a1-fly-1000" />, client());
    expect(out).toContain(">in no group</a>");
    expect(out).toContain("none: it answers to its own switch alone");
    expect(out).toContain('href="/automations/groups?group=_none&amp;automation=par-a1-fly-1000"');
    expect(out).toContain("none closed yet");
  });

  it("keeps only its own decisions", () => {
    const c = client();
    c.setQueryData(["decisions", "automations", "paper"], {
      mode: "paper",
      tradeDate: "2026-11-20",
      rows: [
        { arm: "iron-condor-1345", symbol: "SPX", reason: "vix_max", accepted: false, occurrences: 4, detail: null },
        { arm: "par-a1-fly-1000", symbol: "SPX", reason: "entered", accepted: true, occurrences: 1, detail: null },
      ],
    });
    const out = html(<AutomationRecordPage name="iron-condor-1345" />, c);
    const decisions = out.slice(out.indexOf('id="automation-decisions"'));
    expect(decisions).toContain("vix_max");
    expect(decisions).not.toContain("par-a1-fly-1000");
  });

  it("says so when no automation has the name", () => {
    const out = html(<AutomationRecordPage name="gone" />, client());
    expect(out).toContain("No automation named gone.");
  });

  it("is what the Groups tab shows at /automations/automation/<name>", () => {
    const out = html(
      <Routes>
        <Route path="automations/automation/:name" element={<AutomationsGroupsPage />} />
      </Routes>,
      client(),
      "/automations/automation/iron-condor-1345",
    );
    expect(out).toContain('id="automation-rule"');
    expect(out).not.toContain("changes staged");
  });

  it("marks the groups tab on the rail", () => {
    expect(railSlide("automations", "automation")).toBe("groups");
    expect(railSlide("automations", "history")).toBe("history");
    expect(railSlide("meic", "automation")).toBe("automation");
  });
});
