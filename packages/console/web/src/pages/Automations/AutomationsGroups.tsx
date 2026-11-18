import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GroupSpec, GroupsChange, GroupsEditMember, GroupsEditView } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { groupsCheck, groupsSave, useGroupsEdit } from "../../lib/api";

/**
 * Automations › Groups: which automations switch together, and each group's switch, start, allocation,
 * cap and shares. Everything shown comes from the module (`cli groups-view`); a change is checked by the
 * module (`cli groups-check`, the same load the paper loop runs) and saved through the orchestrator's
 * config editor, which writes only what that check returned. The page keeps no rule of its own.
 *
 * A group's switch only ever switches members OFF: a member that is itself off stays off in a group that
 * is on (a Builder save is always off), so each member's own switch is shown beside the group's.
 */

const VERDICT_CHIP: Record<string, string> = { passed: "chip chip-ok", failed: "chip chip-warn", no_verdict: "chip" };
const VERDICT_WORDS: Record<string, string> = { passed: "passed", failed: "failed", no_verdict: "no verdict" };
const NONE = "";

/** The page's starting draft: every group's spec as it stands, nothing moved. */
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
  return { groups, members: {} };
}

/** Each automation with the group it is in under the draft (null: none). */
export function placement(view: GroupsEditView, draft: GroupsChange): Array<{ m: GroupsEditMember; was: string | null; now: string | null }> {
  const rows = [...view.groups.flatMap((g) => g.members.map((m) => ({ m, was: g.name as string | null }))), ...view.ungrouped.map((m) => ({ m, was: null }))];
  return rows.map(({ m, was }) => ({ m, was, now: m.name in draft.members ? (draft.members[m.name] ?? null) : was }));
}

function money(v: number | undefined): string {
  return v === undefined ? "none" : `$${v.toLocaleString("en-US")}`;
}

/** What the draft changes, in words, one line each; empty when nothing does. */
export function changeLines(view: GroupsEditView, draft: GroupsChange): string[] {
  const out: string[] = [];
  const before = startDraft(view).groups;
  for (const name of Object.keys(draft.groups).sort()) {
    const a = before[name];
    const b = draft.groups[name]!;
    if (a === undefined) {
      out.push(`new group ${name}, ${b.enabled ? "on" : "off"}`);
      continue;
    }
    if (a.enabled !== b.enabled) out.push(`switch ${name} ${b.enabled ? "on" : "off"}`);
    if (a.start !== b.start) out.push(`${name} starts ${b.start ?? "with no boundary"}`);
    if (a.allocation !== b.allocation) out.push(`${name} allocation ${money(b.allocation)}`);
    if (a.max_risk !== b.max_risk) out.push(`${name} max risk ${money(b.max_risk)}`);
    if (JSON.stringify(a.shares ?? {}) !== JSON.stringify(b.shares ?? {})) out.push(`${name} shares changed`);
  }
  for (const { m, was, now } of placement(view, draft)) {
    if (was !== now) out.push(`move ${m.name} from ${was ?? "no group"} to ${now ?? "no group"}`);
  }
  return out;
}

/** The change to send: the specs of the groups that will have members, and the automations that move. */
export function changeToSend(view: GroupsEditView, draft: GroupsChange): GroupsChange {
  const rows = placement(view, draft);
  const used = new Set(rows.map((r) => r.now).filter((g): g is string => g !== null));
  const groups: Record<string, GroupSpec> = {};
  for (const g of used) groups[g] = draft.groups[g] ?? { enabled: false };
  const members: Record<string, string | null> = {};
  for (const r of rows) if (r.was !== r.now) members[r.m.name] = r.now;
  return { groups, members };
}

function numberOrUndefined(text: string): number | undefined {
  const t = text.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

const HELP: CardHelpContent = {
  title: "Automation groups",
  body: (
    <>
      <HelpSection title="What a group is">
        <p>
          A set of like automations switched as one. A group that is off enters nothing; what its automations already hold is still managed and
          settled. A group that is on leaves each member to its own switch, so a member that is off stays off.
        </p>
      </HelpSection>
      <HelpSection title="Start, allocation, max risk, shares">
        <p>
          Start is the first session the group's members may enter: a member joins at its group's start. Allocation is the group's capital, which
          a member sized by a percent of its allocation reads; max risk caps the group's open risk at once. A share gives one member its own slice
          of both, at most 100 percent in all. A share needs an allocation or a max risk.
        </p>
      </HelpSection>
      <HelpSection title="Saving">
        <p>
          Check asks the automations module whether the paper loop would load the change; Save writes it through the suite's config editor, with a
          backup, and the paper loop reads it on its next tick.
        </p>
      </HelpSection>
    </>
  ),
};

function MemberRows({
  rows,
  draft,
  groupNames,
  onMove,
  onShare,
  group,
}: {
  rows: Array<{ m: GroupsEditMember; was: string | null; now: string | null }>;
  draft: GroupsChange;
  groupNames: string[];
  onMove: (name: string, to: string | null) => void;
  onShare: (name: string, pct: number | undefined) => void;
  group: string | null;
}) {
  const spec = group === null ? undefined : draft.groups[group];
  const budget = spec !== undefined && (spec.allocation !== undefined || spec.max_risk !== undefined);
  return (
    <table className="data-table grp-table">
      <thead>
        <tr>
          <th>automation</th>
          <th>underlying</th>
          <th>its own switch</th>
          <th>research verdict</th>
          {group !== null && <th>share %</th>}
          <th>{group === null ? "add to" : "move to"}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ m, was }) => (
          <tr key={m.name}>
            <td>
              {m.name}
              {was !== group && <span className="chip chip-warn grp-moved">moved here</span>}
            </td>
            <td className="muted">{m.symbol ?? "—"}</td>
            <td>
              <span className={m.enabled ? "chip chip-ok" : "chip"}>{m.enabled ? "on" : "off"}</span>
            </td>
            <td>{m.verdict === null ? "—" : <span className={VERDICT_CHIP[m.verdict] ?? "chip"}>{VERDICT_WORDS[m.verdict] ?? m.verdict}</span>}</td>
            {group !== null && (
              <td>
                {budget ? (
                  <input
                    key={`${m.name}-${String(spec?.shares?.[m.name] ?? "")}`}
                    className="builder-input grp-num"
                    type="number"
                    inputMode="decimal"
                    step="any"
                    min={0}
                    max={100}
                    aria-label={`${m.name} share percent`}
                    placeholder="none"
                    defaultValue={spec?.shares?.[m.name] ?? ""}
                    onBlur={(e) => onShare(m.name, numberOrUndefined(e.currentTarget.value))}
                  />
                ) : (
                  <span className="muted">needs an allocation or max risk</span>
                )}
              </td>
            )}
            <td>
              <select
                className="builder-input"
                aria-label={`${group === null ? "Add" : "Move"} ${m.name}`}
                value={group ?? NONE}
                onChange={(e) => onMove(m.name, e.currentTarget.value === NONE ? null : e.currentTarget.value)}
              >
                <option value={NONE}>no group</option>
                {groupNames.map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GroupControls({ name, spec, onChange }: { name: string; spec: GroupSpec; onChange: (next: GroupSpec) => void }) {
  const set = (key: "allocation" | "max_risk", text: string) => {
    const v = numberOrUndefined(text);
    const next: GroupSpec = { ...spec };
    if (v === undefined) delete next[key];
    else next[key] = v;
    if (next.allocation === undefined && next.max_risk === undefined) delete next.shares;
    onChange(next);
  };
  return (
    <div className="builder-grid grp-controls">
      <div className="builder-f">
        <label className="builder-lbl" htmlFor={`grp-${name}-switch`}>
          group switch
        </label>
        <select id={`grp-${name}-switch`} className="builder-input" value={spec.enabled ? "on" : "off"} onChange={(e) => onChange({ ...spec, enabled: e.currentTarget.value === "on" })}>
          <option value="on">on</option>
          <option value="off">off</option>
        </select>
      </div>
      <div className="builder-f">
        <label className="builder-lbl" htmlFor={`grp-${name}-start`}>
          start <span className="builder-hint">· first session, blank: none</span>
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
      </div>
      {(["allocation", "max_risk"] as const).map((k) => (
        <div className="builder-f" key={k}>
          <label className="builder-lbl" htmlFor={`grp-${name}-${k}`}>
            {k === "allocation" ? "allocation" : "max risk"} <span className="builder-hint">· $, &gt; 0, blank: none</span>
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
        </div>
      ))}
    </div>
  );
}

type Outcome = { kind: "idle" } | { kind: "checked" } | { kind: "saved"; moved: string[] } | { kind: "error"; text: string; problems?: string[] };

export function GroupsEditor({ view, onSaved }: { view: GroupsEditView; onSaved?: () => void }) {
  const [draft, setDraft] = useState<GroupsChange>(() => startDraft(view));
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  useEffect(() => setDraft(startDraft(view)), [view]);

  const edit = (next: GroupsChange) => {
    setDraft(next);
    setOutcome({ kind: "idle" });
  };
  const rows = placement(view, draft);
  const groupNames = Object.keys(draft.groups).sort();
  const lines = changeLines(view, draft);
  const move = (name: string, to: string | null) => edit({ ...draft, members: { ...draft.members, [name]: to } });
  const share = (group: string) => (name: string, pct: number | undefined) => {
    const spec = draft.groups[group]!;
    const shares = { ...(spec.shares ?? {}) };
    if (pct === undefined) delete shares[name];
    else shares[name] = pct;
    const next: GroupSpec = { ...spec, shares };
    if (Object.keys(shares).length === 0) delete next.shares;
    edit({ ...draft, groups: { ...draft.groups, [group]: next } });
  };
  const nameOk = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,46}$/.test(newName) && newName === newName.trim() && !(newName in draft.groups);

  const run = async (save: boolean) => {
    setBusy(true);
    const change = changeToSend(view, draft);
    const r = save ? await groupsSave(change) : await groupsCheck(change);
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: r.problems?.length ? "Not saved as it stands:" : (r.error ?? "the request failed"), problems: r.problems });
    else if (r.problems?.length) setOutcome({ kind: "error", text: "Not saved as it stands:", problems: r.problems });
    else if (save) {
      setOutcome({ kind: "saved", moved: "moved" in r ? (r.moved ?? []) : [] });
      onSaved?.();
    } else setOutcome({ kind: "checked" });
  };

  return (
    <div className="grp-page">
      {groupNames.map((g) => {
        const members = rows.filter((r) => r.now === g);
        return (
          <Card key={g} title={`group ${g}`} collapseKey={`automations-group-${g}`} className="lv-12" help={HELP}>
            <GroupControls name={g} spec={draft.groups[g]!} onChange={(spec) => edit({ ...draft, groups: { ...draft.groups, [g]: spec } })} />
            {members.length > 0 ? (
              <MemberRows rows={members} draft={draft} groupNames={groupNames} onMove={move} onShare={share(g)} group={g} />
            ) : (
              <p className="muted">No members: a group is kept only while an automation belongs to it.</p>
            )}
          </Card>
        );
      })}
      <Card title="in no group" collapseKey="automations-group-none" className="lv-12" help={HELP}>
        {rows.some((r) => r.now === null) ? (
          <MemberRows rows={rows.filter((r) => r.now === null)} draft={draft} groupNames={groupNames} onMove={move} onShare={() => {}} group={null} />
        ) : (
          <p className="muted">Every automation is in a group.</p>
        )}
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
            Add group (off)
          </button>
        </div>
      </Card>
      <Card title="changes" collapseKey="automations-group-changes" className="lv-12" help={HELP}>
        {lines.length === 0 ? (
          <p className="muted">Nothing changed.</p>
        ) : (
          <ul className="grp-changes">
            {lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
        <div className="builder-editor-actions">
          <button type="button" className="btn" disabled={busy || lines.length === 0} onClick={() => void run(false)}>
            Check
          </button>
          <button type="button" className="btn" disabled={busy || lines.length === 0} onClick={() => void run(true)}>
            Save
          </button>
          <button type="button" className="btn btn-quiet" disabled={busy || lines.length === 0} onClick={() => edit(startDraft(view))}>
            Undo changes
          </button>
        </div>
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
        {outcome.kind === "checked" && (
          <div className="builder-check" role="status">
            No problems: the paper loop would load this change.
          </div>
        )}
        {outcome.kind === "saved" && (
          <div className="builder-check" role="status">
            Saved; the previous file is backed up. The paper loop reads it on its next tick.
          </div>
        )}
      </Card>
    </div>
  );
}

/** The Groups tab: the module's view, edited and saved through the orchestrator. */
export function AutomationsGroupsPage() {
  const q = useGroupsEdit();
  const qc = useQueryClient();
  if (q.isLoading) return <div className="muted">Reading the groups…</div>;
  if (q.error || !q.data) return <div className="error">Could not read the groups{q.error ? `: ${String(q.error)}` : "."}</div>;
  return (
    <GroupsEditor
      view={q.data}
      onSaved={() => {
        void qc.invalidateQueries({ queryKey: ["automations-groups-edit"] });
        void qc.invalidateQueries({ queryKey: ["automations-groups"] });
      }}
    />
  );
}
