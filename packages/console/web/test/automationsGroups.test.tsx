import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { GroupsEditMember, GroupsEditView } from "@console/shared";
import { changeItems, changeToSend, GroupsFlow, impact, placement, startDraft, stateWords } from "../src/pages/Automations/AutomationsGroups";
import { AUTOMATIONS_SLIDES } from "../src/lightbox/navGroups";

const html = (el: React.ReactElement, url = "/automations/groups") =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

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
  today: "2026-11-18",
  next_session: "2026-11-19",
  counts: { on: 0, waiting: 0, off_group: 2, off_own: 1 },
  groups: [
    {
      name: "ensemble",
      enabled: false,
      max_risk: 2000,
      shares: { "iron-condor-1345": 50 },
      members: [m("iron-condor-1345", { verdict: "passed" }), m("iron-fly-1000", { verdict: "failed" })],
    },
  ],
  ungrouped: [m("par-a1-fly-1000", { enabled: false, state: "off_own", verdict: null })],
};

describe("states in words", () => {
  it("names the one thing that stops an automation", () => {
    expect(stateWords("on", null)).toBe("on");
    expect(stateWords("waiting", "2026-11-19")).toBe("from 2026-11-19");
    expect(stateWords("off_group", null)).toBe("off · its group");
    expect(stateWords("off_own", null)).toBe("off · its own switch");
  });
});

describe("the draft", () => {
  it("starts from the groups as they are, nothing moved or switched", () => {
    const d = startDraft(VIEW);
    expect(d).toEqual({ groups: { ensemble: { enabled: false, max_risk: 2000, shares: { "iron-condor-1345": 50 } } }, members: {}, switches: {} });
    expect(changeItems(VIEW, d)).toEqual([]);
  });

  it("lists each change in words, and sends only what changes", () => {
    const d = startDraft(VIEW);
    const draft = {
      groups: { ...d.groups, ensemble: { ...d.groups["ensemble"]!, enabled: true } },
      members: { "par-a1-fly-1000": "ensemble" },
      switches: { "par-a1-fly-1000": true, "iron-fly-1000": false },
    };
    const items = changeItems(VIEW, draft).map((c) => [c.where, c.what, c.was, c.becomes]);
    expect(items).toEqual([
      ["ensemble", "group switch", "off", "on from 2026-11-19"],
      ["iron-fly-1000", "its own switch", "on", "off"],
      ["par-a1-fly-1000", "group", "no group", "ensemble"],
      ["par-a1-fly-1000", "its own switch", "off", "on from 2026-11-19"],
    ]);
    expect(changeToSend(VIEW, draft)).toEqual({
      groups: { ensemble: draft.groups.ensemble },
      members: { "par-a1-fly-1000": "ensemble" },
      switches: { "iron-fly-1000": false, "par-a1-fly-1000": true },
    });
    expect(placement(VIEW, draft).find((r) => r.m.name === "par-a1-fly-1000")).toMatchObject({ was: null, now: "ensemble", on: true });
  });

  it("undoes one change alone", () => {
    const d = startDraft(VIEW);
    const draft = { ...d, members: { "par-a1-fly-1000": "ensemble" }, switches: { "iron-fly-1000": false } };
    const items = changeItems(VIEW, draft);
    const undone = items.find((c) => c.what === "its own switch")!.undo(draft);
    expect(undone).toEqual({ ...draft, switches: {} });
    expect(changeItems(VIEW, undone).map((c) => c.what)).toEqual(["group"]);
  });
});

describe("what a change does to trading", () => {
  it("is who starts and who stops, between the view now and the module's after", () => {
    const after: GroupsEditView = {
      ...VIEW,
      groups: [{ ...VIEW.groups[0]!, members: [m("iron-condor-1345", { state: "waiting", from: "2026-11-19" }), m("iron-fly-1000", { state: "off_own" })] }],
    };
    const now: GroupsEditView = { ...VIEW, groups: [{ ...VIEW.groups[0]!, members: [m("iron-condor-1345"), m("iron-fly-1000", { state: "on" })] }] };
    const r = impact(now, after);
    expect(r.starts.map((x) => x.name)).toEqual(["iron-condor-1345"]);
    expect(r.stops.map((x) => x.name)).toEqual(["iron-fly-1000"]);
  });
});

describe("the three levels", () => {
  it("every group: the four states counted, a row per group and one for none, nothing staged", () => {
    const out = html(<GroupsFlow view={VIEW} />);
    expect(out).toContain("What is trading now, and what stops the rest?");
    expect(out).toContain("off by its group");
    expect(out).toContain('href="/automations/groups?group=ensemble"');
    expect(out).toContain('href="/automations/groups?group=_none"');
    expect(out).toContain(">Switch on</button>");
    expect(out).toContain("0 changes staged");
    expect(out).toMatch(/<button type="button" class="btn btn-primary" disabled="">Review changes ›<\/button>/);
  });

  it("one group: its switch and budget, each member's own switch and the state it is in", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble");
    expect(out).toContain('<nav class="grp-crumbs" aria-label="Where you are"><a href="/automations/groups"');
    expect(out).toContain(">groups</a> / <b>ensemble</b></nav>");
    expect(out).toContain('aria-label="ensemble: group switch"');
    expect(out).toContain('aria-label="iron-condor-1345: its own switch"');
    expect(out).toContain('<span class="chip chip-warn">off · its group</span>');
    expect(out).toContain('aria-label="iron-condor-1345 share percent"');
    expect(out).toContain("+ Add automations");
    expect(out).toContain('href="/automations/groups?group=ensemble&amp;automation=iron-condor-1345"');
  });

  it("one automation: the three things it needs, and where it sits", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble&automation=iron-condor-1345");
    expect(out).toContain("<b>iron-condor-1345</b>");
    expect(out).toContain("Will it trade, and if not, what stops it?");
    expect(out).toMatch(/grp-check is-no.*Its group/);
    expect(out).toContain('href="/builder?automation=iron-condor-1345"');
  });

  it("is a tab of the automations module", () => {
    expect(AUTOMATIONS_SLIDES.map((s) => s.id)).toContain("groups");
  });
});
