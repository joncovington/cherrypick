import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { GroupsEditMember, GroupsEditView } from "@console/shared";
import { changeItems, changeToSend, FieldTip, GROUP_TIPS, GroupsFlow, impact, outcomeWords, placement, removeFromGroup, emptyGroups, startDraft, stateWords } from "../src/pages/Automations/AutomationsGroups";
import { RetiredSection, retiredWords } from "../src/pages/Automations/RetiredSection";
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
  counts: { on: 0, waiting: 0, off_group: 2, off_own: 1, retired: 0 },
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
    // The group's switch is a toggle in its row: no separate Switch on button, and nothing staged to save.
    expect(out).toContain('role="switch" aria-checked="false" aria-label="ensemble: group switch"');
    expect(out).not.toContain(">Switch on</button>");
    expect(out).toContain("it waits here until you save");
    expect(out).not.toContain(">Save</button>");
  });

  it("one group: its switch and budget, each member's own switch and the state it is in", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble");
    expect(out).toContain('<nav class="grp-crumbs" aria-label="Where you are"><a href="/automations/groups"');
    expect(out).toContain(">groups</a> / <b>ensemble</b></nav>");
    expect(out).toContain('aria-label="ensemble: group switch"');
    expect(out).toContain('aria-label="iron-condor-1345: its own switch"');
    expect(out).toContain('role="switch" aria-checked="true" aria-label="iron-condor-1345: its own switch"');
    expect(out).toContain("Held: its group is off");
    expect(out).toContain("what happens");
    expect(out).toContain('aria-label="iron-condor-1345 share percent"');
    expect(out).toContain("+ Add automations");
    expect(out).toContain('href="/automations/groups?group=ensemble&amp;automation=iron-condor-1345"');
    expect(out).toContain('href="/automations/automation/iron-condor-1345"');
  });

  it("one automation: the three things it needs, and where it sits", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble&automation=iron-condor-1345");
    expect(out).toContain("<b>iron-condor-1345</b>");
    expect(out).toContain("Will it trade, and if not, what stops it?");
    expect(out).toMatch(/grp-check is-no.*Its group/);
    expect(out).toContain('href="/automations/automation/iron-condor-1345"');
    expect(out).not.toContain("What does it place, and when?");
  });

  it("a retired member: locked, folded at the foot, its reason in words and a link to why", () => {
    const view: GroupsEditView = {
      ...VIEW,
      groups: [{ ...VIEW.groups[0]!, members: [m("iron-condor-1345"), m("iron-fly-1000", { verdict: "failed", enabled: false, state: "retired", retired: "2026-11-21" })] }],
    };
    const out = html(<GroupsFlow view={view} />, "/automations/groups?group=ensemble");
    expect(out).toContain("Retired (1)");
    expect(out).toContain('<span class="tgl-locked"');
    expect(out).not.toContain('aria-label="iron-fly-1000: its own switch"');
    expect(out).toContain("Retired: backtest failed");
    expect(out).toContain('href="/automations/automation/iron-fly-1000#automation-retired"');
    // The fold sits after the members still in play.
    expect(out.indexOf("iron-condor-1345")).toBeLessThan(out.indexOf("Retired (1)"));
  });

  it("a group that has run: its budget read-only, with the suggestion and what is waiting", () => {
    const view: GroupsEditView = {
      ...VIEW,
      groups: [
        {
          ...VIEW.groups[0]!,
          enabled: true,
          allocation: 12000,
          budget: { today: { allocation: 10000 }, pending: { from: "2026-11-19", allocation: 12000 }, dated: true, history: [] },
          suggested: { allocation: 9300, members: [{ name: "iron-condor-1345", risk: 9300, why: null }] },
        },
      ],
    };
    const out = html(<GroupsFlow view={view} />, "/automations/groups?group=ensemble");
    expect(out).toContain("allocation $10,000");
    expect(out).toContain("Changes to allocation $12,000 from 2026-11-19.");
    expect(out).toContain(">Change from next session</button>");
    expect(out).not.toContain('id="grp-ensemble-allocation"');
    expect(out).toContain("Suggested allocation $9,300");
  });

  it("a group that has not run edits its budget, and says when no allocation can be suggested", () => {
    const view: GroupsEditView = {
      ...VIEW,
      groups: [{ ...VIEW.groups[0]!, budget: { today: {}, pending: null, dated: false, history: [] }, suggested: { allocation: null, members: [{ name: "iron-fly-1000", risk: null, why: "a wing is placed by delta or percent" }] } }],
    };
    const out = html(<GroupsFlow view={view} />, "/automations/groups?group=ensemble");
    expect(out).toContain('id="grp-ensemble-allocation"');
    expect(out).toContain("No suggested allocation: iron-fly-1000 (a wing is placed by delta or percent).");
  });

  it("is a tab of the automations module", () => {
    expect(AUTOMATIONS_SLIDES.map((s) => s.id)).toContain("groups");
  });
});

describe("what happens, in words", () => {
  it("names each state as the row says it", () => {
    expect(outcomeWords({ state: "on", from: null, verdict: null }).words).toBe("Trades");
    expect(outcomeWords({ state: "waiting", from: "2026-11-23", verdict: null }).words).toBe("Trades from 2026-11-23");
    expect(outcomeWords({ state: "off_group", from: null, verdict: null }).words).toBe("Held: its group is off");
    expect(outcomeWords({ state: "off_own", from: null, verdict: null }).words).toBe("Off by its own switch");
    expect(outcomeWords({ state: "retired", from: null, verdict: "failed" }).words).toBe("Retired: backtest failed");
    expect(outcomeWords({ state: "retired", from: null, verdict: "passed" }).words).toBe("Retired");
  });
});

describe("the retired switch at the foot of an automation's page", () => {
  it("says it plainly and never names a research item", () => {
    expect(retiredWords({ retired: "2026-11-21", verdict: "failed" })).toBe("Retired 11-21: backtest failed.");
    expect(retiredWords({ retired: null, verdict: "failed" })).toMatch(/^Not retired\. Its backtest failed/);
    const out = html(<RetiredSection m={m("iron-fly-1000", { retired: "2026-11-21", verdict: "failed", state: "retired" })} open={0} />);
    expect(out).toContain('role="switch" aria-checked="true" aria-label="iron-fly-1000: retired"');
    expect(out).toContain("It is off and stays off until you reinstate it.");
    expect(out).not.toMatch(/item \d/);
  });
});

describe("the budget's help and its backtest suggestion", () => {
  it("puts a ? beside allocation, max risk and share, each explaining itself", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble");
    expect(out).toContain('aria-label="What is the allocation?"');
    expect(out).toContain('aria-label="What is the max risk?"');
    expect(out).toContain('aria-label="What is a share?"');
    for (const tip of Object.values(GROUP_TIPS)) expect(tip.length).toBeGreaterThan(60);
    expect(html(<FieldTip id="share" label="What is a share?" />)).toContain('aria-expanded="false"');
  });

  it("asks the members' backtests for the max risk and shares", () => {
    const view: GroupsEditView = { ...VIEW, groups: [{ ...VIEW.groups[0]!, budget: { today: {}, pending: null, dated: false, history: [] } }] };
    expect(html(<GroupsFlow view={view} />, "/automations/groups?group=ensemble")).toContain("Reading the members&#x27; backtests…");
  });
});

describe("removing an automation from its group", () => {
  it("each member has Remove and no move-to menu", () => {
    const out = html(<GroupsFlow view={VIEW} />, "/automations/groups?group=ensemble");
    expect(out).toContain('aria-label="Remove iron-condor-1345 from ensemble"');
    expect(out).not.toContain('aria-label="Move iron-condor-1345"');
    expect(out).not.toContain(">move to<");
  });

  it("takes it out and switches it off, so it does not trade on its own", () => {
    const d = startDraft(VIEW);
    const r = placement(VIEW, d).find((x) => x.m.name === "iron-condor-1345")!;
    const after = removeFromGroup(d, r);
    expect(after.members).toEqual({ "iron-condor-1345": null });
    expect(after.switches).toEqual({ "iron-condor-1345": false });
    expect(changeItems(VIEW, after).map((c) => [c.where, c.what, c.becomes])).toEqual([
      ["iron-condor-1345", "group", "no group"],
      ["iron-condor-1345", "its own switch", "off"],
    ]);
  });

  it("leaves a retired or switched-off one's switch alone", () => {
    const view: GroupsEditView = { ...VIEW, groups: [{ ...VIEW.groups[0]!, members: [m("iron-fly-1000", { enabled: false, state: "retired", retired: "2026-11-21" })] }] };
    const d = startDraft(view);
    const r = placement(view, d).find((x) => x.m.name === "iron-fly-1000")!;
    expect(removeFromGroup(d, r).switches).toEqual({});
  });
});

describe("a group with no automation", () => {
  it("is saved, off, and sent with the change", () => {
    const d = startDraft(VIEW);
    const draft = { ...d, groups: { ...d.groups, testing: { enabled: false } } };
    expect(emptyGroups(VIEW, draft)).toEqual({ empty: ["testing"], on: [] });
    expect(changeToSend(VIEW, draft).groups["testing"]).toEqual({ enabled: false });
  });

  it("cannot be on: the save holds until it is off or has a member", () => {
    const d = startDraft(VIEW);
    const on = { ...d, groups: { ...d.groups, testing: { enabled: true } } };
    expect(emptyGroups(VIEW, on).on).toEqual(["testing"]);
    expect(emptyGroups(VIEW, { ...on, members: { "par-a1-fly-1000": "testing" } }).on).toEqual([]);
  });

  it("its switch cannot be turned on, and it can be deleted", () => {
    const view: GroupsEditView = { ...VIEW, groups: [...VIEW.groups, { name: "testing", enabled: false, members: [] }] };
    const out = html(<GroupsFlow view={view} />);
    expect(out).toMatch(/aria-label="testing: group switch"[^>]*disabled=""/);
    expect(out).toContain('aria-label="Delete testing"');
    const d = startDraft(view);
    const gone = { ...d, groups: { ensemble: d.groups["ensemble"]! } };
    expect(changeItems(view, gone).map((c) => [c.where, c.becomes])).toEqual([["testing", "deleted"]]);
    expect(changeToSend(view, gone).groups).not.toHaveProperty("testing");
  });
});
