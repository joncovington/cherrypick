import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { AutomationState, GroupSpec, GroupsChange, GroupsCheckResult, GroupsEditGroup, GroupsEditMember, GroupsEditView } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { groupsCheck, groupsSave, useAutomations, useAutomationsPerformance, useGroupBudget, useGroupsEdit } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { AutomationRecordPage } from "./AutomationRecord";
import { automationHref } from "./automationsModels";
import { GroupUnitSection } from "./GroupUnit";
import { ShapeIcon } from "./ShapeIcon";
import { LockedSwitch, Toggle } from "./Toggle";

/**
 * Automations › Groups, a drill-down: every group (`?` none), one group (`?group=`), one automation
 * (`?group=&automation=`). Each level reads, adds and switches what it shows; every change is staged in
 * the save bar along the bottom, carried as you drill in and out, and written only from there.
 *
 * Everything shown comes from the module (`cli groups-view`): each automation's own switch, its start, its
 * shape and the STATE it is in (`on`, `waiting` on its start, `off_group`, `off_own`, `retired`), which the
 * page shows in words and never derives. While anything is staged the page asks the module's check (`cli
 * groups-check`, the same load the paper loop runs), which returns the view as it would be after the
 * change: a staged row's words are that answer, and the save bar lists who starts and who stops by it.
 * Saving goes through the orchestrator's config editor, which writes only what that check returned. A
 * switch turned on starts at the next session (the module sets it), so a paper record never begins mid-day.
 * A retired automation's switch is locked here: it is reinstated on its own page. Once a group has run, its
 * budget is read-only until "Change from next session": the module dates the change, so the sessions
 * before keep the budget they ran on. The module is paper only: nothing here places an order.
 */

const NONE = "_none";
const VERDICT_CHIP: Record<string, string> = { passed: "chip chip-ok", failed: "chip chip-warn", no_verdict: "chip" };
const VERDICT_WORDS: Record<string, string> = { passed: "passed", failed: "failed", no_verdict: "no verdict" };

/** A state in words, for the chip beside each automation. */
export function stateWords(state: AutomationState, from: string | null): string {
  if (state === "on") return "on";
  if (state === "waiting") return `from ${from ?? "its start"}`;
  if (state === "off_group") return "off · its group";
  if (state === "retired") return "retired";
  return "off · its own switch";
}

/** What happens to an automation, in words, from the module's state and verdict. */
export function outcomeWords(m: Pick<GroupsEditMember, "state" | "from" | "verdict">): { cls: string; words: string } {
  if (m.state === "on") return { cls: "trades", words: "Trades" };
  if (m.state === "waiting") return { cls: "trades", words: `Trades from ${m.from ?? "its start"}` };
  if (m.state === "off_group") return { cls: "held", words: "Held: its group is off" };
  if (m.state === "retired") return { cls: "retired", words: m.verdict === "failed" ? "Retired: backtest failed" : "Retired" };
  return { cls: "off", words: "Off by its own switch" };
}

function Outcome({ m }: { m: Pick<GroupsEditMember, "state" | "from" | "verdict"> }) {
  const o = outcomeWords(m);
  return (
    <span className={`grp-out is-${o.cls}`}>
      <span className="grp-dot" aria-hidden="true" />
      {o.words}
    </span>
  );
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
      <HelpSection title="A budget once a group has run">
        <p>
          Once a group has run, its budget is read-only: "Change from next session" makes the change start at the next session, and the
          sessions before keep the budget they ran on, so its record reads each trade against the budget its session carried. The suggested
          allocation is the most its members can have at risk at once, from their own sizes and strikes.
        </p>
      </HelpSection>
      <HelpSection title="Retired">
        <p>A retired automation's switch is locked here. Reinstate it at the foot of its own page; it then stays off until you switch it on.</p>
      </HelpSection>
      <HelpSection title="Saving">
        <p>
          Changes wait in the bar along the bottom with a blue dot on each switch you moved. The bar says what they do to trading by the
          automations module's own check, and Save writes them together to the automations config, with a backup. Paper only: nothing here
          places an order.
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

function OwnSwitch({ m, on, staged, onChange }: { m: GroupsEditMember; on: boolean; staged: boolean; onChange: (on: boolean) => void }) {
  if (m.retired) return <LockedSwitch />;
  return <Toggle on={on} label={`${m.name}: its own switch`} pending={staged} onChange={onChange} />;
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
            <Toggle
              on={spec.enabled}
              label={`${name}: group switch`}
              pending={was !== undefined && was.enabled !== spec.enabled}
              onChange={(on) => edit({ ...draft, groups: { ...draft.groups, [name!]: { ...spec, enabled: on } } })}
            />
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
        <FliesTile
          title="off by its own switch"
          value={String(view.counts.off_own)}
          tone="dim"
          foot={`a Builder save is off until switched on${view.counts.retired ? ` · ${view.counts.retired} more retired, locked off` : ""}`}
        />
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

/** What each budget field means, behind the ? beside it. */
export const GROUP_TIPS: Record<"allocation" | "max_risk" | "share" | "budget", string> = {
  allocation:
    "The group's money. It is what the group's % net and % drawdown are read against, and the pot an automation sized as a percent of its allocation sizes from. It changes no trade of an automation with a fixed size.",
  max_risk:
    "The most all the group's automations together may have at risk at once: each open trade's maximum loss, added up. An entry that would go over is cut to the contracts that fit, or refused when not one fits.",
  share:
    "One automation's slice of the group's max risk and allocation, at most 100 percent across the group. It caps that automation's own risk at once, and is the money it sizes from if it is sized by percent. Without one, it has only the group's cap, shared first come, first served.",
  budget:
    "The allocation, max risk and shares together. Once the group has run they are read-only, so its record keeps the budget each session ran on: a change starts at the next session.",
};

/** A ? beside a field that opens what it means; `left` opens it leftwards, for a column near the right edge. */
export function FieldTip({ id, label, left = false }: { id: keyof typeof GROUP_TIPS; label: string; left?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="grp-tipwrap">
      <button type="button" className={open ? "wiz-q is-open" : "wiz-q"} aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
        ?
      </button>
      {open && (
        <span className={left ? "grp-tip-pop is-left" : "grp-tip-pop"} role="note">
          {GROUP_TIPS[id]}
        </span>
      )}
    </span>
  );
}

/** The group's budget as its members' pinned backtests read it: a suggestion for the max risk and the
 *  shares, and the worst fall an allocation would have had to absorb. The backtest package's reading,
 *  shown; nothing is applied until a Use button is pressed. */
function FromBacktest({ name, spec, onChange }: { name: string; spec: GroupSpec; onChange: (next: GroupSpec) => void }) {
  const q = useGroupBudget(name);
  const d = q.data;
  const head = <b>From the backtest:</b>;
  if (q.isLoading) return <p className="muted grp-hint">Reading the members' backtests…</p>;
  if (q.error || !d) return <p className="muted grp-hint">{head} could not be read{q.error ? `: ${String(q.error)}` : "."}</p>;
  if (d.error !== null) return <p className="muted grp-hint">{head} could not be read: {d.error}</p>;
  if (d.missing.length > 0)
    return (
      <p className="muted grp-hint grp-suggest">
        {head} not yet. {d.missing.join(", ")} {d.missing.length === 1 ? "has" : "have"} no pinned backtest run; pin one on each automation's Builder page.
      </p>
    );
  if (d.withheld) return <p className="muted grp-hint">{head} withheld while results built on licensed data are hidden.</p>;
  const r = d.reading;
  if (r === null) return null;
  if (r.max_risk === undefined || r.shares === undefined) return <p className="muted grp-hint">{head} none: {r.why ?? "no figure"}.</p>;
  const shares = r.shares;
  const cut = Object.entries(r.cut ?? {});
  return (
    <div className="grp-hint grp-from-bt">
      <p className="muted grp-suggest">
        {head} max risk {money(r.max_risk)}, the most its members held at once over {r.sessions} sessions ({r.overlap?.[0]} to {r.overlap?.[1]}), so it would have refused nothing
        they entered.
        {spec.max_risk !== r.max_risk && (
          <button type="button" className="btn" onClick={() => onChange({ ...spec, max_risk: r.max_risk! })}>
            Use {money(r.max_risk)}
          </button>
        )}
      </p>
      <p className="muted grp-suggest">
        Shares, each one's own most at once as a percent of {money(r.max_risk)}: {Object.entries(shares).map(([n, v]) => `${n} ${v}%`).join(", ")}.
        {JSON.stringify(spec.shares ?? {}) !== JSON.stringify(shares) && (
          <button type="button" className="btn" onClick={() => onChange({ ...spec, max_risk: spec.max_risk ?? r.max_risk!, shares })}>
            Use these shares
          </button>
        )}
      </p>
      {r.scaled && (
        <p className="grp-warn">
          Their peaks fell on different sessions, so the shares are scaled to fit 100 percent.
          {cut.length > 0 && ` At these shares ${cut.map(([n, c]) => `${n} would have been cut on ${c} session${c === 1 ? "" : "s"}`).join(", ")}.`}
        </p>
      )}
      {(r.unshared ?? []).length > 0 && (
        <p className="muted">
          No share for {(r.unshared ?? []).join(", ")}: {(r.unshared ?? []).length === 1 ? "it" : "they"} held nothing in {(r.unshared ?? []).length === 1 ? "its run" : "their runs"}, so only the
          group&apos;s cap applies.
        </p>
      )}
      {r.drawdown !== undefined && <p className="muted">Its worst fall over those sessions: {money(r.drawdown)}, what an allocation would have had to absorb.</p>}
      <p className="muted">
        Runs: {(r.members ?? []).map((m) => `${m.name} ${m.run.slice(-10)} (${m.trades} trade${m.trades === 1 ? "" : "s"})`).join(", ")}
        {r.licensed ? ", built on licensed data" : ""}.
        {(r.members ?? []).some((m) => m.trades < 20) && " Fewer than 20 trades in a run is a thin read: treat these figures as a starting point."}
      </p>
    </div>
  );
}

function budgetWords(b: { allocation?: number; max_risk?: number; shares?: Record<string, number> } | undefined): string {
  if (b === undefined || (b.allocation === undefined && b.max_risk === undefined)) return "no budget";
  const parts = [b.allocation !== undefined ? `allocation ${money(b.allocation)}` : null, b.max_risk !== undefined ? `max risk ${money(b.max_risk)}` : null];
  const shares = Object.keys(b.shares ?? {}).length;
  return parts.filter(Boolean).join(" · ") + (shares ? ` · ${shares} share${shares === 1 ? "" : "s"}` : "");
}

function GroupControls({
  name,
  spec,
  was,
  info,
  nextSession,
  onChange,
}: {
  name: string;
  spec: GroupSpec;
  was: GroupSpec | undefined;
  info: GroupsEditGroup | undefined;
  nextSession: string;
  onChange: (next: GroupSpec) => void;
}) {
  const [unlocked, setUnlocked] = useState(false);
  const set = (key: "allocation" | "max_risk", text: string) => {
    const v = numberOrUndefined(text);
    const next: GroupSpec = { ...spec };
    if (v === undefined) delete next[key];
    else next[key] = v;
    if (next.allocation === undefined && next.max_risk === undefined) delete next.shares;
    onChange(next);
  };
  const switchingOn = spec.enabled && was !== undefined && !was.enabled;
  const dated = info?.budget?.dated === true;
  const pending = info?.budget?.pending ?? null;
  const budgetMoved = was !== undefined && (was.allocation !== spec.allocation || was.max_risk !== spec.max_risk || JSON.stringify(was.shares ?? {}) !== JSON.stringify(spec.shares ?? {}));
  const readOnly = dated && !unlocked && !budgetMoved;
  const suggested = info?.suggested;
  const unknown = (suggested?.members ?? []).filter((m) => m.risk === null);
  return (
    <div className="grp-controls-wrap">
      <div className="grp-head-row">
        <span className="builder-lbl">group</span>
        <Toggle on={spec.enabled} label={`${name}: group switch`} big pending={was !== undefined && was.enabled !== spec.enabled} onChange={(on) => onChange({ ...spec, enabled: on })} />
        <span className="muted grp-hint">
          {spec.enabled
            ? switchingOn
              ? `On from ${spec.start && spec.start > nextSession ? spec.start : nextSession}: switching on starts at the next session, never mid-day.`
              : "On: its switched-on automations trade."
            : "Off: nothing in this group enters. What it holds is still managed."}
        </span>
      </div>
      <div className="builder-grid grp-controls">
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
        {readOnly ? (
          <div className="builder-f grp-budget-ro">
            <span className="builder-lbl">
              budget
              <FieldTip id="budget" label="What is the budget?" />
            </span>
            <span className="num">{budgetWords(info?.budget?.today)}</span>
            {pending !== null ? (
              <span className="grp-hint grp-pending">
                Changes to {budgetWords(pending)} from {pending.from}.
              </span>
            ) : (
              <span className="muted grp-hint">Read-only while it runs, so its record keeps the budget each session ran on.</span>
            )}
            <button type="button" className="btn" onClick={() => setUnlocked(true)}>
              Change from next session
            </button>
          </div>
        ) : (
          (["allocation", "max_risk"] as const).map((k) => (
            <div className="builder-f" key={k}>
              <span className="builder-lbl">
                <label htmlFor={`grp-${name}-${k}`}>{k === "allocation" ? "allocation" : "max risk"}</label>
                <FieldTip id={k} label={k === "allocation" ? "What is the allocation?" : "What is the max risk?"} />
              </span>
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
              <span className="muted grp-hint">{dated ? `Dollars; starts ${nextSession}, the next session.` : "Dollars, above 0; blank: none."}</span>
            </div>
          ))
        )}
      </div>
      {suggested !== undefined && (
        <p className="muted grp-hint grp-suggest">
          {suggested.allocation !== null ? (
            <>
              Suggested allocation {money(suggested.allocation)}: the most its {suggested.members.length} member{suggested.members.length === 1 ? "" : "s"} can have at risk at once, from their own sizes and strikes.{" "}
              {spec.allocation !== suggested.allocation && !readOnly && (
                <button type="button" className="btn" onClick={() => onChange({ ...spec, allocation: suggested.allocation! })}>
                  Use {money(suggested.allocation)}
                </button>
              )}
            </>
          ) : unknown.length > 0 ? (
            `No suggested allocation: ${unknown.map((m) => `${m.name} (${m.why ?? "no bound"})`).join(", ")}.`
          ) : (
            "No suggested allocation: no member to count."
          )}
        </p>
      )}
      {info !== undefined && <FromBacktest name={name} spec={spec} onChange={onChange} />}
    </div>
  );
}

/** Each automation as the module's check says it would be after the staged change; empty with none staged. */
type After = Map<string, GroupsEditMember>;

function afterMap(check: GroupsCheckResult | null): After {
  const a = check?.after;
  return new Map(a ? [...a.groups.flatMap((g) => g.members), ...a.ungrouped].map((m) => [m.name, m]) : []);
}

function GroupPage({ view, draft, edit, paper, group, after }: { view: GroupsEditView; draft: GroupsChange; edit: (d: GroupsChange) => void; paper: Paper; group: string; after: After }) {
  const [adding, setAdding] = useState(false);
  const [showRetired, setShowRetired] = useState(true);
  const none = group === NONE;
  const target: string | null = none ? null : group;
  const rows = placement(view, draft);
  const inGroup = rows.filter((r) => r.now === target);
  const members = inGroup.filter((r) => !r.m.retired);
  const retiredRows = inGroup.filter((r) => r.m.retired);
  const others = rows.filter((r) => r.now !== target);
  const info = none ? undefined : view.groups.find((g) => g.name === group);
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
            <GroupControls name={group} spec={spec} was={was} info={info} nextSession={view.next_session} onChange={(s) => edit({ ...draft, groups: { ...draft.groups, [group]: s } })} />
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
          {inGroup.length === 0 ? (
            <p className="muted">No members yet: add one, or the group is dropped when saved.</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table grp-table">
                <thead>
                  <tr>
                    <th aria-label="shape"></th>
                    <th>automation</th>
                    <th>underlying</th>
                    <th>its own switch</th>
                    <th>what happens</th>
                    <th>research verdict</th>
                    {!none && (
                      <th>
                        share %
                        <FieldTip id="share" label="What is a share?" left />
                      </th>
                    )}
                    <th className="num">open</th>
                    <th className="num">net, all</th>
                    <th>move to</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {[...members, ...(showRetired ? retiredRows : [])].map((r, i) => {
                    const staged = r.was !== r.now || r.on !== r.m.enabled;
                    // With anything staged, every row says what the module's check says it would be.
                    const shown = after.get(r.m.name) ?? r.m;
                    const fold =
                      i === members.length && retiredRows.length > 0 ? (
                        <tr key="retired-fold" className="grp-fold">
                          <td colSpan={none ? 10 : 11}>
                            <button type="button" className="grp-fold-btn" aria-expanded={showRetired} onClick={() => setShowRetired(!showRetired)}>
                              {showRetired ? "▾" : "▸"} Retired ({retiredRows.length})
                            </button>
                          </td>
                        </tr>
                      ) : null;
                    const row = (
                      <tr key={r.m.name} className={[staged ? "grp-staged" : "", r.m.retired ? "grp-retired" : "", shown.state === "on" || shown.state === "waiting" ? "" : "grp-dim"].filter(Boolean).join(" ") || undefined}>
                        <td className="grp-shape">
                          <ShapeIcon shape={r.m.shape} label={r.m.name} />
                        </td>
                        <td>
                          <Link to={automationHref(r.m.name)} className="num">
                            {r.m.name}
                          </Link>
                          {r.was !== r.now && <span className="chip chip-info grp-moved">moved here</span>}
                        </td>
                        <td>{r.m.symbol ?? "—"}</td>
                        <td>
                          <OwnSwitch m={r.m} on={r.on} staged={r.on !== r.m.enabled} onChange={(on) => flip(r, on)} />
                        </td>
                        <td>
                          {staged && !after.has(r.m.name) ? <span className="muted">Asking the module…</span> : <Outcome m={shown} />}
                          {r.m.retired && (
                            <>
                              {" "}
                              <Link className="grp-why" to={`${automationHref(r.m.name)}#automation-retired`}>
                                why ›
                              </Link>
                            </>
                          )}
                        </td>
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
                    return fold ? [fold, row] : row;
                  })}
                  {!showRetired && retiredRows.length > 0 && (
                    <tr className="grp-fold">
                      <td colSpan={none ? 10 : 11}>
                        <button type="button" className="grp-fold-btn" aria-expanded={false} onClick={() => setShowRetired(true)}>
                          ▸ Retired ({retiredRows.length})
                        </button>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {budget && <p className="muted grp-hint">Shares: {shareTotal} of 100 percent given. A member without one has no slice of its own, only the group's cap.</p>}
        </Card>
      </Section>
      {!none && <GroupUnitSection group={group} />}
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
            <Check ok={r.on && !r.m.retired}>
              <span className="grp-cell">
                {r.m.retired ? (
                  <>
                    Retired: <Link to={`${automationHref(name)}#automation-retired`}>reinstate it on its own page</Link> to switch it on
                  </>
                ) : (
                  "Its own switch"
                )}
                <OwnSwitch m={r.m} on={r.on} staged={r.on !== r.m.enabled} onChange={(on) => edit({ ...draft, switches: on === r.m.enabled ? withoutKey(draft.switches, name) : { ...draft.switches, [name]: on } })} />
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

type Saved = { kind: "idle" } | { kind: "saved"; text: string } | { kind: "error"; text: string; problems?: string[] };

/** The module's check of the staged change, asked again a moment after each edit; null while it is asked. */
function useLiveCheck(view: GroupsEditView, draft: GroupsChange, staged: boolean): GroupsCheckResult | null {
  const send = JSON.stringify(changeToSend(view, draft));
  const [check, setCheck] = useState<{ send: string; r: GroupsCheckResult } | null>(null);
  useEffect(() => {
    if (!staged) return;
    let live = true;
    const t = setTimeout(() => {
      void groupsCheck(JSON.parse(send) as GroupsChange).then((r) => live && setCheck({ send, r }));
    }, 300);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [send, staged]);
  return staged && check?.send === send ? check.r : null;
}

function SaveBar({
  view,
  draft,
  edit,
  check,
  saved,
  onSaved,
}: {
  view: GroupsEditView;
  draft: GroupsChange;
  edit: (d: GroupsChange) => void;
  check: GroupsCheckResult | null;
  saved: string | null;
  onSaved: (text: string) => void;
}) {
  const items = changeItems(view, draft);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Saved>({ kind: "idle" });
  const problems = check?.problems ?? [];
  const moved = check?.after ? impact(view, check.after) : null;
  const failedStarting = moved?.starts.filter((m) => m.verdict === "failed") ?? [];
  const ready = items.length > 0 && check !== null && check.ok && problems.length === 0;
  const save = async () => {
    setBusy(true);
    const r = await groupsSave(changeToSend(view, draft));
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: r.problems?.length ? "Not saved as it stands:" : (r.error ?? "the save failed"), problems: r.problems });
    else {
      setOutcome({ kind: "idle" });
      onSaved(`Saved ${items.length} change${items.length === 1 ? "" : "s"}; the previous file is backed up. Changes start at the next session.`);
    }
  };
  if (items.length === 0) {
    return (
      <div className="grp-bar" role="status">
        <span className="grp-bar-text">{saved ?? "Flip a switch or change a budget: it waits here until you save."}</span>
      </div>
    );
  }
  return (
    <div className="grp-bar is-staged" role="region" aria-label="Unsaved changes">
      <div className="grp-bar-row">
        <b>
          {items.length} unsaved change{items.length === 1 ? "" : "s"}
        </b>
        <span className="grp-bar-text">
          {items
            .slice(0, 3)
            .map((c) => `${c.where} ${c.what} ${c.becomes}`)
            .join(" · ") + (items.length > 3 ? ` · and ${items.length - 3} more` : "")}
        </span>
        <span className="grp-spacer" />
        <span className="grp-bar-check">
          {check === null
            ? "Asking the automations module…"
            : !check.ok
              ? (check.error ?? "the check failed")
              : problems.length > 0
                ? `${problems.length} problem${problems.length === 1 ? "" : "s"}: not saved as it stands`
                : `${moved?.starts.length ?? 0} start · ${moved?.stops.length ?? 0} stop`}
        </span>
        <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Details"}
        </button>
        <button type="button" className="btn" onClick={() => edit(startDraft(view))}>
          Undo all
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => void save()}>
          Save
        </button>
      </div>
      {(open || problems.length > 0 || outcome.kind === "error") && (
        <div className="grp-bar-details">
          {problems.length > 0 && (
            <div className="error" role="alert">
              Not saved as it stands:
              <ul>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {open && (
            <>
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
              {moved && (
                <p className="muted">
                  Starts: {moved.starts.map((m) => m.name).join(", ") || "none"}. Stops: {moved.stops.map((m) => m.name).join(", ") || "nothing that trades now stops"}.
                </p>
              )}
              {(check?.notes ?? []).map((n) => (
                <p key={n} className="muted">
                  {n}
                </p>
              ))}
            </>
          )}
          {failedStarting.length > 0 && (
            <p className="grp-warn">
              {failedStarting.length} of those starting failed their backtest ({failedStarting.map((m) => m.name).join(", ")}). They would trade on paper as a forward read; switch them off if that is not meant.
            </p>
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
      )}
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
  const [saved, setSaved] = useState<string | null>(null);
  const paper = usePaper();
  useEffect(() => setDraft(startDraft(view)), [view]);
  const edit = (d: GroupsChange) => {
    setDraft(d);
    setSaved(null);
  };
  const items = changeItems(view, draft);
  const check = useLiveCheck(view, draft, items.length > 0);
  const props = { view, draft, edit, paper };
  return (
    <div className="pf-layout grp-flow">
      {group === undefined ? <GroupsIndex {...props} /> : automation === undefined ? <GroupPage {...props} group={group} after={afterMap(check)} /> : <AutomationPage {...props} group={group} name={automation} />}
      <SaveBar
        view={view}
        draft={draft}
        edit={edit}
        check={check}
        saved={saved}
        onSaved={(text) => {
          setSaved(text);
          onSaved?.();
        }}
      />
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
