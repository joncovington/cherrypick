import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { GroupsEditView } from "@console/shared";
import { changeLines, changeToSend, GroupsEditor, placement, startDraft } from "../src/pages/Automations/AutomationsGroups";
import { AUTOMATIONS_SLIDES } from "../src/lightbox/navGroups";

const html = (el: React.ReactElement) => renderToString(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>).replace(/<!--\s*-->/g, "");

const m = (name: string, enabled = true) => ({ name, enabled, verdict: "no_verdict" as const, symbol: "SPX", shipped: true });
const VIEW: GroupsEditView = {
  groups: [{ name: "ensemble", enabled: true, max_risk: 2000, shares: { "iron-fly-1000": 40 }, members: [m("iron-fly-1000"), m("long-put-1030", false)] }],
  ungrouped: [m("gld-call-spread-1dte")],
};

describe("the groups draft", () => {
  it("starts from each group's spec, nothing moved", () => {
    expect(startDraft(VIEW)).toEqual({ groups: { ensemble: { enabled: true, max_risk: 2000, shares: { "iron-fly-1000": 40 } } }, members: {} });
    expect(changeLines(VIEW, startDraft(VIEW))).toEqual([]);
    expect(changeToSend(VIEW, startDraft(VIEW))).toEqual({ groups: { ensemble: startDraft(VIEW).groups["ensemble"] }, members: {} });
  });

  it("says each change in words and sends only the groups that keep members and the moves", () => {
    const d = startDraft(VIEW);
    const draft = {
      groups: { ...d.groups, ensemble: { ...d.groups["ensemble"]!, enabled: false }, gold: { enabled: false } },
      members: { "gld-call-spread-1dte": "gold", "long-put-1030": null },
    };
    expect(changeLines(VIEW, draft)).toEqual([
      "switch ensemble off",
      "new group gold, off",
      "move long-put-1030 from ensemble to no group",
      "move gld-call-spread-1dte from no group to gold",
    ]);
    expect(changeToSend(VIEW, draft)).toEqual({
      groups: { ensemble: draft.groups.ensemble, gold: { enabled: false } },
      members: { "long-put-1030": null, "gld-call-spread-1dte": "gold" },
    });
    expect(placement(VIEW, draft).find((r) => r.m.name === "gld-call-spread-1dte")?.now).toBe("gold");
  });

  it("drops a group left with no members, and a move back is no move", () => {
    const d = startDraft(VIEW);
    const all = { groups: { ...d.groups, rest: { enabled: true } }, members: { "iron-fly-1000": "rest", "long-put-1030": "rest" } };
    expect(Object.keys(changeToSend(VIEW, all).groups)).toEqual(["rest"]);
    expect(changeToSend(VIEW, { ...d, members: { "iron-fly-1000": "ensemble" } }).members).toEqual({});
  });
});

describe("the groups editor", () => {
  const out = html(<GroupsEditor view={VIEW} />);

  it("shows each group's switch and budget, and each member's own switch beside it", () => {
    expect(out).toContain("group ensemble");
    expect(out).toContain('<option value="on" selected="">on</option>');
    expect(out).toContain('id="grp-ensemble-max_risk"');
    expect(out).toContain('value="2000"');
    expect(out).toContain("its own switch");
    expect(out).toMatch(/long-put-1030<\/td><td class="muted">SPX<\/td><td><span class="chip">off<\/span>/);
    expect(out).toContain('aria-label="iron-fly-1000 share percent"');
  });

  it("lists the automations in no group, offers a new group, and has nothing to save yet", () => {
    expect(out).toContain("in no group");
    expect(out).toContain('aria-label="Add gld-call-spread-1dte"');
    expect(out).toContain("Add group (off)");
    expect(out).toContain("Nothing changed.");
    expect(out).toMatch(/<button type="button" class="btn" disabled="">Save<\/button>/);
  });

  it("is a tab of the automations module", () => {
    expect(AUTOMATIONS_SLIDES.map((s) => s.id)).toContain("groups");
  });
});
