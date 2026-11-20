import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { AutomationState, GroupSpec, GroupsChange, GroupsCheckResult, GroupsEditMember, GroupsEditView } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { groupsCheck, groupsSave, useAutomations, useAutomationsPerformance, useGroupsEdit } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { AutomationRecordPage } from "./AutomationRecord";
import { automationHref } from "./automationsModels";

/**
 * Automations › Groups, a drill-down: every group (`?` none), one group (`?group=`), one automation
 * (`?group=&automation=`). Each level reads, adds and switches what it shows; every change is staged in
 * the bar along the bottom, carried as you drill in and out, and written only from Review.
 *
 * Everything shown comes from the module (`cli groups-view`): each automation's own switch, its start
 * and the STATE it is in (`on`, `waiting` on its start, `off_group`, `off_own`), which the page shows and
 * never derives. Review asks the module's check (`cli groups-check`, the same load the paper loop runs),
 * which also returns the view as it would be after the change, so "what it does to trading" is the
 * module's answer laid beside the view now. Saving goes through the orchestrator's config editor, which
 * writes only what that check returned. A switch turned on starts at the next session (the module sets
 * it), so a paper record never begins mid-day. The module is paper only: nothing here places an order.
 */

const NONE = "_none";
const VERDICT_CHIP: Record<string, string> = { passed: "chip chip-ok", failed: "chip chip-warn", no_verdict: "chip" };
const VERDICT_WORDS: Record<string, string> = { passed: "passed", failed: "failed", no_verdict: "no verdict" };

/** A state in words, for the chip beside each automation. */
export function stateWords(state: AutomationState, from: string | null): string {
  if (state === "on") return "on";
  if (state === "waiting") return `from ${from ?? "its start"}`;
  if (state === "off_group") return "off · its group";
  return "off · its own switch";
}

function StateChip({ m }: { m: Pick<GroupsEditMember, "state" | "from"> }) {
  const cls = m.state === "on" ? "chip chip-ok" : m.state === "waiting" ? "chip chip-info" : m.state === "off_group" ? "chip chip-warn" : "chip";
  return <span className={cls}>{stateWords(m.state, m.from)}</span>;
}

function Verdict({ v }: { v: string | null }) {
  return v === null ? <span className="muted">—</span> : <span className={VERDICT_CHIP[v] ?? "chip"}>{VERDICT_WORDS[v] ?? v}</span>;
}

// ---------------------------------------------------------------------------------------------------
// The draft: what the page has staged, and what it sends
// ---------------------------------------------------------------------------------------------------

/** The page's starting draft: every group's spec as it stands, nothing moved or switched. */
export function startDraft(view: GroupsEditView): GroupsChange {
  const groups: Record<string, GroupSpec> = {};
  for (const g of view.groups) {
    const spec: GroupSpec = { enabled: g.enabled };
    if (g.start !== undefined) spec.start = g.start;
    if (g.allocation !== undefined) spec.allocation = g.allocation;
    if (g.max_risk !== undefined) spec.max_risk = g.max_risk;
    if (g.shares !== undefined) spec.shares = { ...g.shares };
    groups[g.name] = spec;
  }
  return { groups, members: {}, switches: {} };
}

export interface Placed {
  m: GroupsEditMember;
  was: string | null;
  now: string | null;
  /** Its own switch under the draft. */
  on: boolean;
}

/** Each automation with the group it is in and its own switch under the draft. */
export function placement(view: GroupsEditView, draft: GroupsChange): Placed[] {
  const rows = [...view.groups.flatMap((g) => g.members.map((m) => ({ m, was: g.name as string | null }))), ...view.ungrouped.map((m) => ({ m, was: null }))];
  return rows.map(({ m, was }) => ({
    m,
    was,
    now: m.name in draft.members ? (draft.members[m.name] ?? null) : was,
    on: m.name in draft.switches ? draft.switches[m.name]! : m.enabled,
  }));
}

/** One staged change: where, what, from and to in words, and how to undo it alone. */
export interface ChangeItem {
  key: string;
  where: string;
  what: string;
  was: string;
  becomes: string;
  undo: (draft: GroupsChange) => GroupsChange;
}

function money(v: number | undefined): string {
  return v === undefined ? "none" : `$${v.toLocaleString("en-US")}`;
}

function withoutKey<T>(o: Record<string, T>, k: string): Record<string, T> {
  const { [k]: _gone, ...rest } = o;
  return rest;
}

/** Every staged change, one item each; empty when nothing is staged. */
export function changeItems(view: GroupsEditView, draft: GroupsChange): ChangeItem[] {
  const out: ChangeItem[] = [];
  const before = startDraft(view).groups;
  for (const name of Object.keys(draft.groups).sort()) {
    const a = before[name];
    const b = draft.groups[name]!;
    const restore = (d: GroupsChange, key: keyof GroupSpec): GroupsChange => {
      const spec: GroupSpec = { ...d.groups[name]! };
      if (a === undefined || a[key] === undefined) delete spec[key];
      else (spec as unknown as Record<string, unknown>)[key] = a[key];
      return { ...d, groups: { ...d.groups, [name]: spec } };
    };
    if (a === undefined) {
      out.push({ key: `new-${name}`, where: name, what: "new group", was: "—", becomes: b.enabled ? "on" : "off", undo: (d) => ({ ...d, groups: withoutKey(d.groups, name) }) });
      continue;
    }
    if (a.enabled !== b.enabled) out.push({ key: `sw-${name}`, where: name, what: "group switch", was: a.enabled ? "on" : "off", becomes: b.enabled ? `on from ${b.start ?? view.next_session}` : "off", undo: (d) => restore(d, "enabled") });
    if (a.start !== b.start && !(a.enabled !== b.enabled)) out.push({ key: `st-${name}`, where: name, what: "start", was: a.start ?? "none", becomes: b.start ?? "none", undo: (d) => restore(d, "start") });
    if (a.allocation !== b.allocation) out.push({ key: `al-${name}`, where: name, what: "allocation", was: money(a.allocation), becomes: money(b.allocation), undo: (d) => restore(d, "allocation") });
    if (a.max_risk !== b.max_risk) out.push({ key: `mr-${name}`, where: name, what: "max risk", was: money(a.max_risk), becomes: money(b.max_risk), undo: (d) => restore(d, "max_risk") });
    if (JSON.stringify(a.shares ?? {}) !== JSON.stringify(b.shares ?? {})) out.push({ key: `sh-${name}`, where: name, what: "shares", was: shareWords(a.shares), becomes: shareWords(b.shares), undo: (d) => restore(d, "shares") });
  }
  for (const r of placement(view, draft)) {
    const n = r.m.name;
    if (r.was !== r.now) out.push({ key: `mv-${n}`, where: n, what: "group", was: r.was ?? "no group", becomes: r.now ?? "no group", undo: (d) => ({ ...d, members: withoutKey(d.members, n) }) });
    if (r.on !== r.m.enabled) out.push({ key: `own-${n}`, where: n, what: "its own switch", was: r.m.enabled ? "on" : "off", becomes: r.on ? `on from ${view.next_session}` : "off", undo: (d) => ({ ...d, switches: withoutKey(d.switches, n) }) });
  }
  return out;
}

function shareWords(s: Record<string, number> | undefined): string {
  const e = Object.entries(s ?? {});
  return e.length === 0 ? "none" : e.map(([k, v]) => `${k} ${v}%`).join(", ");
}

/** The change to send: the specs of the groups that will have members, the moves and the switches. */
export function changeToSend(view: GroupsEditView, draft: GroupsChange): GroupsChange {
  const rows = placement(view, draft);
  const used = new Set(rows.map((r) => r.now).filter((g): g is string => g !== null));
  const groups: Record<string, GroupSpec> = {};
  for (const g of used) groups[g] = draft.groups[g] ?? { enabled: false };
  const members: Record<string, string | null> = {};
  const switches: Record<string, boolean> = {};
  for (const r of rows) {
    if (r.was !== r.now) members[r.m.name] = r.now;
    if (r.on !== r.m.enabled) switches[r.m.name] = r.on;
  }
  return { groups, members, switches };
}

/** What a change does to trading: who starts and who stops, from the view now and the module's after. */
export function impact(view: GroupsEditView, after: GroupsEditView): { starts: GroupsEditMember[]; stops: GroupsEditMember[] } {
  const live = (s: AutomationState) => s === "on" || s === "waiting";
  const now = new Map([...view.groups.flatMap((g) => g.members), ...view.ungrouped].map((m) => [m.name, m]));
  const then = [...after.groups.flatMap((g) => g.members), ...after.ungrouped];
  return {
    starts: then.filter((m) => live(m.state) && !live(now.get(m.name)?.state ?? "off_own")),
    stops: then.filter((m) => !live(m.state) && live(now.get(m.name)?.state ?? "off_own")),
  };
}

// ---------------------------------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------------------------------

const HELP: CardHelpContent = {
  title: "Automation groups",
  body: (
    <>
      <HelpSection title="When an automation trades">
        <p>
          Three things, all needed: its own switch is on, its group is on (an automation in no group answers to its own switch alone), and its
          start has come. Each automation's chip names the one that is missing.
        </p>
      </HelpSection>
      <HelpSection title="Switching on">
        <p>
          A group or an automation switched on starts at the next session, never in the middle of a day, so its paper record has a clean first
          session. One with a forward rule keeps the start its rule reads from. A group switched off enters nothing; what its automations hold
          is still managed and settled.
        </p>
      </HelpSection>
      <HelpSection title="Allocation, max risk, shares">
        <p>
          Allocation is the group's capital, which a member sized by a percent of its allocation reads; max risk caps the group's open risk at
          once. A share gives one member its own slice of both, at most 100 percent in all, and needs an allocation or a max risk.
        </p>
      </HelpSection>
      <HelpSection title="Saving">
        <p>
          Changes wait in the bar along the bottom. Review lists them, shows what they do to trading by the automations module's own check,
          and saves them together to the automations config, with a backup. Paper only: nothing here places an order.
        </p>
      </HelpSection>
    </>
  ),
};

function numberOrUndefined(text: string): number | undefined {
  const t = text.trim().replace(/,/g, "");
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

function Crumbs({ group, automation }: { group?: string; automation?: string }) {
  return (
    <nav className="grp-crumbs" aria-label="Where you are">
      <Link to="/automations/groups">groups</Link>
      {group !== undefined && (
        <>
          {" / "}
          {automation === undefined ? <b>{group === NONE ? "in no group" : group}</b> : <Link to={`/automations/groups?group=${encodeURIComponent(group)}`}>{group === NONE ? "in no group" : group}</Link>}
        </>
      )}
      {automation !== undefined && (
        <>
          {" / "}
          <b>{automation}</b>
        </>
      )}
    </nav>
  );
}

function OwnSwitch({ name, on, staged, onChange }: { name: string; on: boolean; staged: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="grp-switch">
      <input type="checkbox" role="switch" checked={on} aria-label={`${name}: its own switch`} onChange={(e) => onChange(e.currentTarget.checked)} />
      <span>{on ? "on" : "off"}</span>
      {staged && <span className="chip chip-info">staged</span>}
    </label>
  );
}

type Paper = { trades: Record<string, number>; net: Record<string, number | null>; open: Record<string, number> };

function usePaper(): Paper {
  const perf = useAutomationsPerformance();
  const book = useAutomations();
  const trades: Record<string, number> = {};
  const net: Record<string, number | null> = {};
  for (const [arm, line] of Object.entries(perf.data?.arms ?? {})) {
    trades[arm] = line.trades;
    net[arm] = line.net;
  }
  const open: Record<string, number> = {};
  for (const p of book.data?.positions ?? []) if (p.status === "open") open[p.arm] = (open[p.arm] ?? 0) + 1;
  return { trades, net, open };
}

// ---------------------------------------------------------------------------------------------------
// Level 1: every group
// ---------------------------------------------------------------------------------------------------

function GroupsIndex({ view, draft, edit, paper }: { view: GroupsEditView; draft: GroupsChange; edit: (d: GroupsChange) => void; paper: Paper }) {
  const [newName, setNewName] = useState("");
  const rows = placement(view, draft);
  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,46}$/.test(newName) && !(newName in draft.groups);
  const groupRow = (name: string | null) => {
    const members = rows.filter((r) => r.now === name);
    const live = members.filter((r) => r.m.state === "on" || r.m.state === "waiting").length;
    const spec = name === null ? undefined : draft.groups[name];
    const was = name === null ? undefined : view.groups.find((g) => g.name === name);
    const verdicts = { passed: 0, failed: 0, no_verdict: 0 } as Record<string, number>;
    for (const r of members) if (r.m.verdict !== null) verdicts[r.m.verdict] = (verdicts[r.m.verdict] ?? 0) + 1;
    const open = members.reduce((s, r) => s + (paper.open[r.m.name] ?? 0), 0);
    const nets = members.map((r) => paper.net[r.m.name]).filter((v): v is number => typeof v === "number");
    const key = name ?? NONE;
    return (
      <tr key={key}>
        <td>
          <Link to={`/automations/groups?group=${encodeURIComponent(key)}`}>{name ?? "in no group"}</Link>
        </td>
        <td>
          {spec === undefined ? (
            <span className="muted">—</span>
          ) : (
            <span className="grp-cell">
              <span className={spec.enabled ? "chip chip-ok" : "chip chip-warn"}>{spec.enabled ? "on" : "off"}</span>
              {was !== undefined && was.enabled !== spec.enabled && <span className="chip chip-info">staged</span>}
            </span>
          )}
        </td>
        <td className="num">{members.length}</td>
        <td className="num">
          {live} of {members.length}
        </td>
        <td className="muted">{spec === undefined ? "—" : spec.allocation !== undefined || spec.max_risk !== undefined ? `${money(spec.allocation)} · cap ${money(spec.max_risk)}` : "none"}</td>
        <td>
          <span className="grp-cell">
            {verdicts["passed"]! > 0 && <span className="chip chip-ok">passed {verdicts["passed"]}</span>}
            {verdicts["failed"]! > 0 && <span className="chip chip-warn">failed {verdicts["failed"]}</span>}
            {verdicts["no_verdict"]! > 0 && <span className="chip">no verdict {verdicts["no_verdict"]}</span>}
          </span>
        </td>
        <td className="num">{open}</td>
        <td className="num">{nets.length ? fmtMoney(nets.reduce((a, b) => a + b, 0)) : "—"}</td>
        <td className="grp-actions">
          {spec !== undefined && (
            <button type="button" className="btn" onClick={() => edit({ ...draft, groups: { ...draft.groups, [name!]: { ...spec, enabled: !spec.enabled } } })}>
              {spec.enabled ? "Switch off" : "Switch on"}
            </button>
          )}
          <Link className="btn" to={`/automations/groups?group=${encodeURIComponent(key)}`}>
            Open ›
          </Link>
        </td>
      </tr>
    );
  };
  return (
    <>
      <Section concept="stand" tag="the groups" question="What is trading now, and what stops the rest?">
        <FliesTile title="on" value={String(view.counts.on)} tone="dim" foot="its group on, its own switch on, its start come" />
        <FliesTile title="waiting on a start" value={String(view.counts.waiting)} tone="dim" foot="switched on, joining at their start" />
        <FliesTile title="off by its group" value={String(view.counts.off_group)} tone="dim" foot="its own switch on, its group off" />
        <FliesTile title="off by its own switch" value={String(view.counts.off_own)} tone="dim" foot="a Builder save is off until switched on" />
      </Section>
      <Section concept="room" tag="the sets" question="Which sets are switched on, and how is each doing?">
        <Card title="groups" collapseKey="automations-groups-index" className="lv-12" help={HELP}>
          <div className="table-scroll">
            <table className="data-table grp-table">
              <thead>
                <tr>
                  <th>group</th>
                  <th>switch</th>
                  <th className="num">members</th>
                  <th className="num">on or waiting</th>
                  <th>budget</th>
                  <th>research verdicts</th>
                  <th className="num">open</th>
                  <th className="num">net, all</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(draft.groups)
                  .sort()
                  .map((g) => groupRow(g))}
                {groupRow(null)}
              </tbody>
            </table>
          </div>
          <div className="grp-new">
            <label className="builder-lbl" htmlFor="grp-new-name">
              new group
            </label>
            <input id="grp-new-name" className="builder-input" value={newName} placeholder="name" onChange={(e) => setNewName(e.currentTarget.value)} />
            <button
              type="button"
              className="btn"
              disabled={!nameOk}
              onClick={() => {
                edit({ ...draft, groups: { ...draft.groups, [newName]: { enabled: false } } });
                setNewName("");
              }}
            >
              + New group (off)
            </button>
            <span className="muted">Open it to add its automations; a group is kept only while one belongs to it.</span>
          </div>
        </Card>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------------------------------------
// Level 2: one group
// ---------------------------------------------------------------------------------------------------

function GroupControls({ name, spec, was, nextSession, onChange }: { name: string; spec: GroupSpec; was: GroupSpec | undefined; nextSession: string; onChange: (next: GroupSpec) => void }) {
  const set = (key: "allocation" | "max_risk", text: string) => {
    const v = numberOrUndefined(text);
    const next: GroupSpec = { ...spec };
    if (v === undefined) delete next[key];
    else next[key] = v;
    if (next.allocation === undefined && next.max_risk === undefined) delete next.shares;
    onChange(next);
  };
  const switchingOn = spec.enabled && was !== undefined && !was.enabled;
  return (
    <div className="builder-grid grp-controls">
      <div className="builder-f">
        <span className="builder-lbl">group switch</span>
        <label className="grp-switch">
          <input type="checkbox" role="switch" checked={spec.enabled} aria-label={`${name}: group switch`} onChange={(e) => onChange({ ...spec, enabled: e.currentTarget.checked })} />
          <span>{spec.enabled ? (switchingOn ? `on from ${spec.start && spec.start > nextSession ? spec.start : nextSession}` : "on") : "off"}</span>
          {was !== undefined && was.enabled !== spec.enabled && <span className="chip chip-info">staged</span>}
        </label>
        <span className="muted grp-hint">{spec.enabled ? "Switching on starts at the next session, never mid-day." : "Off: nothing enters; what is held is still managed."}</span>
      </div>
      <div className="builder-f">
        <label className="builder-lbl" htmlFor={`grp-${name}-start`}>
          start
        </label>
        <input
          id={`grp-${name}-start`}
          className="builder-input"
          type="date"
          value={spec.start ?? ""}
          onChange={(e) => {
            const v = e.currentTarget.value;
            const next = { ...spec };
            if (v === "") delete next.start;
            else next.start = v;
            onChange(next);
          }}
        />
        <span className="muted grp-hint">First session; blank: none.</span>
      </div>
      {(["allocation", "max_risk"] as const).map((k) => (
        <div className="builder-f" key={k}>
          <label className="builder-lbl" htmlFor={`grp-${name}-${k}`}>
            {k === "allocation" ? "allocation" : "max risk"}
          </label>
          <input
            key={`${k}-${String(spec[k] ?? "")}`}
            id={`grp-${name}-${k}`}
            className="builder-input"
            type="number"
            inputMode="decimal"
            step="any"
            min={0}
            placeholder="none"
            defaultValue={spec[k] ?? ""}
            onBlur={(e) => set(k, e.currentTarget.value)}
          />
          <span className="muted grp-hint">Dollars, above 0; blank: none.</span>
        </div>
      ))}
    </div>
  );
}

function GroupPage({ view, draft, edit, paper, group }: { view: GroupsEditView; draft: GroupsChange; edit: (d: GroupsChange) => void; paper: Paper; group: string }) {
  const [adding, setAdding] = useState(false);
  const none = group === NONE;
  const target: string | null = none ? null : group;
  const rows = placement(view, draft);
  const members = rows.filter((r) => r.now === target);
  const others = rows.filter((r) => r.now !== target);
  const spec = none ? undefined : draft.groups[group];
  const was = none ? undefined : startDraft(view).groups[group];
  const groupNames = Object.keys(draft.groups).sort();
  if (!none && spec === undefined) return <p className="muted">No group named {group}.</p>;
  const budget = spec !== undefined && (spec.allocation !== undefined || spec.max_risk !== undefined);
  const shareTotal = Object.values(spec?.shares ?? {}).reduce((a, b) => a + b, 0);
  const move = (name: string, to: string | null) => {
    const original = rows.find((r) => r.m.name === name)?.was ?? null;
    edit({ ...draft, members: to === original ? withoutKey(draft.members, name) : { ...draft.members, [name]: to } });
  };
  const flip = (r: Placed, on: boolean) => edit({ ...draft, switches: on === r.m.enabled ? withoutKey(draft.switches, r.m.name) : { ...draft.switches, [r.m.name]: on } });
  const share = (name: string, pct: number | undefined) => {
    const s = draft.groups[group]!;
    const shares = { ...(s.shares ?? {}) };
    if (pct === undefined) delete shares[name];
    else shares[name] = pct;
    const next: GroupSpec = { ...s, shares };
    if (Object.keys(shares).length === 0) delete next.shares;
    edit({ ...draft, groups: { ...draft.groups, [group]: next } });
  };
  return (
    <>
      <Crumbs group={group} />
      {spec !== undefined && (
        <Section concept="room" tag="the group" question={`Is ${group} on, from when, and with what budget?`}>
          <Card title={`group ${group}`} collapseKey={`automations-group-${group}`} className="lv-12" help={HELP}>
            <GroupControls name={group} spec={spec} was={was} nextSession={view.next_session} onChange={(s) => edit({ ...draft, groups: { ...draft.groups, [group]: s } })} />
          </Card>
        </Section>
      )}
      <Section concept="stand" tag="the members" question={none ? "Which automations are in no group, and are they on?" : "Which of its automations would trade, and why not the others?"}>
        <Card
          title={none ? "in no group" : "members"}
          collapseKey={`automations-group-${group}-members`}
          className="lv-12"
          help={HELP}
          controls={
            <span className="grp-cell">
              {members.some((r) => !r.on) && (
                <button type="button" className="btn" onClick={() => edit(members.reduce((d, r) => (r.on ? d : { ...d, switches: r.m.enabled ? withoutKey(d.switches, r.m.name) : { ...d.switches, [r.m.name]: true } }), draft))}>
                  Switch all on
                </button>
              )}
              {!none && (
                <button type="button" className="btn" aria-expanded={adding} onClick={() => setAdding(!adding)}>
                  + Add automations
                </button>
              )}
            </span>
          }
        >
          {adding && !none && (
            <div className="grp-add" role="group" aria-label="Add automations">
              <span className="builder-lbl">add to {group}</span>
              {others.length === 0 ? (
                <span className="muted">Every automation is in this group.</span>
              ) : (
                others.map((r) => (
                  <label key={r.m.name} className="grp-add-row">
                    <input type="checkbox" checked={false} onChange={() => move(r.m.name, group)} />
                    <span className="num">{r.m.name}</span>
                    <span className="muted">{r.now ?? "no group"}</span>
                    <Verdict v={r.m.verdict} />
                  </label>
                ))
              )}
            </div>
          )}
          {members.length === 0 ? (
            <p className="muted">No members yet: add one, or the group is dropped when saved.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table grp-table">
                <thead>
                  <tr>
                    <th>automation</th>
                    <th>underlying</th>
                    <th>its own switch</th>
                    <th>so it is</th>
                    <th>research verdict</th>
                    {!none && <th>share %</th>}
                    <th className="num">open</th>
                    <th className="num">net, all</th>
                    <th>move to</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((r) => {
                    const staged = r.was !== r.now || r.on !== r.m.enabled;
                    return (
                      <tr key={r.m.name} className={staged ? "grp-staged" : undefined}>
                        <td>
                          <Link to={automationHref(r.m.name)} className="num">
                            {r.m.name}
                          </Link>
                          {r.was !== r.now && <span className="chip chip-info grp-moved">moved here</span>}
                        </td>
                        <td>{r.m.symbol ?? "—"}</td>
                        <td>
                          <OwnSwitch name={r.m.name} on={r.on} staged={r.on !== r.m.enabled} onChange={(on) => flip(r, on)} />
                        </td>
                        <td>{staged ? <span className="chip chip-info">see Review</span> : <StateChip m={r.m} />}</td>
                        <td>
                          <Verdict v={r.m.verdict} />
                        </td>
                        {!none && (
                          <td>
                            {budget ? (
                              <input
                                key={`${r.m.name}-${String(spec?.shares?.[r.m.name] ?? "")}`}
                                className="builder-input grp-num"
                                type="number"
                                inputMode="decimal"
                                step="any"
                                min={0}
                                max={100}
                                aria-label={`${r.m.name} share percent`}
                                placeholder="none"
                                defaultValue={spec?.shares?.[r.m.name] ?? ""}
                                onBlur={(e) => share(r.m.name, numberOrUndefined(e.currentTarget.value))}
                              />
                            ) : (
                              <span className="muted">needs a budget</span>
                            )}
                          </td>
                        )}
                        <td className="num">{paper.open[r.m.name] ?? 0}</td>
                        <td className="num">{fmtMoney(paper.net[r.m.name] ?? null)}</td>
                        <td>
                          <select className="builder-input" aria-label={`Move ${r.m.name}`} value={r.now ?? NONE} onChange={(e) => move(r.m.name, e.currentTarget.value === NONE ? null : e.currentTarget.value)}>
                            <option value={NONE}>no group</option>
                            {groupNames.map((g) => (
                              <option key={g} value={g}>
                                {g}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <Link className="btn" to={`/automations/groups?group=${encodeURIComponent(group)}&automation=${encodeURIComponent(r.m.name)}`}>
                            Open ›
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {budget && <p className="muted grp-hint">Shares: {shareTotal} of 100 percent given. A member without one has no slice of its own, only the group's cap.</p>}
        </Card>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------------------------------------
// Level 3: one automation
// ---------------------------------------------------------------------------------------------------

function Check({ ok, wait, children }: { ok: boolean; wait?: boolean; children: ReactNode }) {
  return (
    <li className={ok ? "grp-check is-ok" : wait ? "grp-check is-wait" : "grp-check is-no"}>
      <span aria-hidden="true">{ok ? "✓" : wait ? "◷" : "✕"}</span>
      <span>{children}</span>
    </li>
  );
}

function AutomationPage({ view, draft, edit, paper, group, name }: { view: GroupsEditView; draft: GroupsChange; edit: (d: GroupsChange) => void; paper: Paper; group: string; name: string }) {
  const r = placement(view, draft).find((x) => x.m.name === name);
  if (r === undefined) return <p className="muted">No automation named {name}.</p>;
  const g = r.now;
  const gspec = g === null ? undefined : draft.groups[g];
  const gWas = g === null ? undefined : startDraft(view).groups[g];
  const groupOn = g === null || gspec?.enabled === true;
  const start = r.on !== r.m.enabled && r.on ? view.next_session : (r.m.from ?? r.m.start);
  const groupStart = gspec?.enabled && gWas && !gWas.enabled ? (gspec.start && gspec.start > view.next_session ? gspec.start : view.next_session) : gspec?.start;
  const first = [start, groupStart].filter((d): d is string => typeof d === "string").sort().pop() ?? null;
  const startCome = first === null || first <= view.today;
  const groupNames = Object.keys(draft.groups).sort();
  const budget = gspec !== undefined && (gspec.allocation !== undefined || gspec.max_risk !== undefined);
  return (
    <>
      <Crumbs group={group} automation={name} />
      <Section concept="stand" tag="the switches" question="Will it trade, and if not, what stops it?">
        <Card title="three things, all needed" collapseKey="automations-automation-switches" className="lv-8" help={HELP}>
          <ul className="grp-checks">
            <Check ok={groupOn}>
              {g === null ? (
                "In no group: it answers to its own switch alone"
              ) : (
                <>
                  Its group <Link to={`/automations/groups?group=${encodeURIComponent(g)}`}>{g}</Link> is {gspec?.enabled ? "on" : "off"}
                  {gWas !== undefined && gspec !== undefined && gWas.enabled !== gspec.enabled && <span className="chip chip-info">staged</span>}
                </>
              )}
            </Check>
            <Check ok={r.on}>
              <span className="grp-cell">
                Its own switch
                <OwnSwitch name={name} on={r.on} staged={r.on !== r.m.enabled} onChange={(on) => edit({ ...draft, switches: on === r.m.enabled ? withoutKey(draft.switches, name) : { ...draft.switches, [name]: on } })} />
              </span>
            </Check>
            <Check ok={startCome} wait={!startCome}>
              {first === null ? "No start: it may enter on its next day" : startCome ? `Its start, ${first}, has come` : `It starts ${first}${first === view.next_session ? ", the next session" : ""}`}
            </Check>
          </ul>
          {r.m.forward && <p className="muted grp-hint">It has a forward rule, so switching it on keeps the start its rule reads from.</p>}
        </Card>
        <Card title="where it sits" collapseKey="automations-automation-group" className="lv-4" help={HELP}>
          <div className="builder-grid">
            <div className="builder-f">
              <label className="builder-lbl" htmlFor="auto-group">
                group
              </label>
              <select
                id="auto-group"
                className="builder-input"
                value={g ?? NONE}
                onChange={(e) => {
                  const to = e.currentTarget.value === NONE ? null : e.currentTarget.value;
                  edit({ ...draft, members: to === r.was ? withoutKey(draft.members, name) : { ...draft.members, [name]: to } });
                }}
              >
                <option value={NONE}>no group</option>
                {groupNames.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
            </div>
            <div className="builder-f">
              <span className="builder-lbl">share of the group</span>
              <span className="num">{budget ? (gspec?.shares?.[name] !== undefined ? `${gspec.shares[name]}%` : "none: only the group's cap") : "the group has no budget"}</span>
            </div>
          </div>
        </Card>
      </Section>
      <Section concept="add" tag="its page" question="What does it do, and what has it done?">
        <Card title="the rule and the record" collapseKey="automations-automation-page" className="lv-12" help={HELP}>
          <p className="muted">Its rule in plain words, its research verdict, what it has done on paper and what it decided are on its own page.</p>
          <Link className="btn" to={automationHref(name)}>
            Open {name} ›
          </Link>
        </Card>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------------------------------------
// The staged-changes bar and Review
// ---------------------------------------------------------------------------------------------------

type Outcome = { kind: "idle" } | { kind: "saved"; text: string } | { kind: "error"; text: string; problems?: string[] };

function Review({ view, draft, edit, onClose, onSaved }: { view: GroupsEditView; draft: GroupsChange; edit: (d: GroupsChange) => void; onClose: () => void; onSaved: (text: string) => void }) {
  const items = changeItems(view, draft);
  const [check, setCheck] = useState<GroupsCheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const send = JSON.stringify(changeToSend(view, draft));
  useEffect(() => {
    let live = true;
    setCheck(null);
    void groupsCheck(JSON.parse(send) as GroupsChange).then((r) => live && setCheck(r));
    return () => {
      live = false;
    };
  }, [send]);
  const problems = check?.problems ?? [];
  const after = check?.after ?? null;
  const moved = after ? impact(view, after) : null;
  const failedStarting = moved?.starts.filter((m) => m.verdict === "failed") ?? [];
  const save = async () => {
    setBusy(true);
    const r = await groupsSave(JSON.parse(send) as GroupsChange);
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: r.problems?.length ? "Not saved as it stands:" : (r.error ?? "the save failed"), problems: r.problems });
    else onSaved(`Saved ${items.length} change${items.length === 1 ? "" : "s"}; the previous file is backed up. The paper loop reads it on its next tick.`);
  };
  return (
    <div className="grp-review-back" role="presentation">
      <section className="grp-review" role="dialog" aria-modal="true" aria-labelledby="grp-review-title">
        <header className="grp-review-head">
          <h2 id="grp-review-title">
            Review {items.length} change{items.length === 1 ? "" : "s"} to the groups
          </h2>
          <span className="chip chip-ok">PAPER</span>
          <button type="button" className="btn grp-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="grp-review-body">
          <p className="muted">Each change, what it was and what it becomes, then what it does to trading by the automations module's own check. Save writes them all at once, with a backup.</p>
          <span className="builder-lbl">the changes</span>
          <div className="table-scroll">
            <table className="data-table grp-table">
              <thead>
                <tr>
                  <th>where</th>
                  <th>what</th>
                  <th>was</th>
                  <th>becomes</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.key}>
                    <td className="grp-where">{c.where}</td>
                    <td>{c.what}</td>
                    <td className="muted grp-was">{c.was}</td>
                    <td>{c.becomes}</td>
                    <td>
                      <button type="button" className="btn" onClick={() => edit(c.undo(draft))}>
                        Undo
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <span className="builder-lbl">what it does to trading</span>
          {check === null ? (
            <p className="muted">Asking the automations module…</p>
          ) : !check.ok ? (
            <p className="error">{check.error ?? "the check failed"}</p>
          ) : problems.length > 0 ? (
            <div className="error" role="alert">
              Not saved as it stands:
              <ul>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          ) : (
            <>
              <div className="grp-impact">
                <FliesTile title="start" value={String(moved?.starts.length ?? 0)} tone={moved && moved.starts.length > 0 ? "pos" : "dim"} foot={moved?.starts.map((m) => m.name).join(", ") || "none"} />
                <FliesTile title="stop" value={String(moved?.stops.length ?? 0)} tone="dim" foot={moved?.stops.map((m) => m.name).join(", ") || "nothing that trades now stops"} />
              </div>
              {(check.notes ?? []).map((n) => (
                <p key={n} className="muted">
                  {n}
                </p>
              ))}
              {failedStarting.length > 0 && (
                <p className="grp-warn">
                  {failedStarting.length} of those starting failed their research ({failedStarting.map((m) => m.name).join(", ")}). They would trade on paper as a forward read; switch them off if that is not meant.
                </p>
              )}
              <span className="chip chip-ok grp-ok">the module&apos;s check: no problems</span>
            </>
          )}
          {outcome.kind === "error" && (
            <div className="error" role="alert">
              {outcome.text}
              {outcome.problems && (
                <ul>
                  {outcome.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <footer className="grp-review-foot">
          <button type="button" className="btn" onClick={onClose}>
            Back to editing
          </button>
          <span className="grp-spacer" />
          <button type="button" className="btn" onClick={() => edit(startDraft(view))}>
            Discard all
          </button>
          <button type="button" className="btn btn-primary" disabled={busy || items.length === 0 || check === null || !check.ok || problems.length > 0} onClick={() => void save()}>
            Save {items.length} change{items.length === 1 ? "" : "s"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function StagedBar({ items, onDiscard, onReview, saved }: { items: ChangeItem[]; onDiscard: () => void; onReview: () => void; saved: string | null }) {
  return (
    <div className={items.length ? "grp-bar is-staged" : "grp-bar"} role="status">
      <span className={items.length ? "chip chip-info" : "chip"}>
        {items.length} change{items.length === 1 ? "" : "s"} staged
      </span>
      <span className="grp-bar-text">
        {items.length
          ? items
              .slice(0, 3)
              .map((c) => `${c.where} ${c.what} ${c.becomes}`)
              .join(" · ") + (items.length > 3 ? ` · and ${items.length - 3} more` : "")
          : (saved ?? "Changes made on any level wait here.")}
      </span>
      <span className="grp-spacer" />
      <button type="button" className="btn" disabled={!items.length} onClick={onDiscard}>
        Discard
      </button>
      <button type="button" className="btn btn-primary" disabled={!items.length} onClick={onReview}>
        Review changes ›
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// The tab
// ---------------------------------------------------------------------------------------------------

export function GroupsFlow({ view, onSaved }: { view: GroupsEditView; onSaved?: () => void }) {
  const [params] = useSearchParams();
  const group = params.get("group") ?? undefined;
  const automation = params.get("automation") ?? undefined;
  const [draft, setDraft] = useState<GroupsChange>(() => startDraft(view));
  const [reviewing, setReviewing] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const paper = usePaper();
  useEffect(() => setDraft(startDraft(view)), [view]);
  const edit = (d: GroupsChange) => {
    setDraft(d);
    setSaved(null);
  };
  const items = changeItems(view, draft);
  const props = { view, draft, edit, paper };
  return (
    <div className="pf-layout grp-flow">
      {group === undefined ? <GroupsIndex {...props} /> : automation === undefined ? <GroupPage {...props} group={group} /> : <AutomationPage {...props} group={group} name={automation} />}
      <StagedBar items={items} saved={saved} onDiscard={() => edit(startDraft(view))} onReview={() => setReviewing(true)} />
      {reviewing && (
        <Review
          view={view}
          draft={draft}
          edit={edit}
          onClose={() => setReviewing(false)}
          onSaved={(text) => {
            setReviewing(false);
            setSaved(text);
            onSaved?.();
          }}
        />
      )}
    </div>
  );
}

/** The Groups tab: the module's view, drilled into, staged, reviewed and saved through the orchestrator. */
export function AutomationsGroupsPage() {
  const { name } = useParams();
  const q = useGroupsEdit();
  const qc = useQueryClient();
  if (name !== undefined) return <AutomationRecordPage name={name} />;
  if (q.isLoading) return <div className="muted">Reading the groups…</div>;
  if (q.error || !q.data) return <div className="error">Could not read the groups{q.error ? `: ${String(q.error)}` : "."}</div>;
  return (
    <GroupsFlow
      view={q.data}
      onSaved={() => {
        void qc.invalidateQueries({ queryKey: ["automations-groups-edit"] });
        void qc.invalidateQueries({ queryKey: ["automations-groups"] });
        void qc.invalidateQueries({ queryKey: ["automations-builder"] });
      }}
    />
  );
}
