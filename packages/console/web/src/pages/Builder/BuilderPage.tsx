import { useEffect, useState } from "react";
import { BuilderWizard } from "./BuilderWizard";
import { useQueryClient } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderBlock, BuilderSchema, BuilderSuggestion, BuilderToday } from "@console/shared";
import { builderBacktest, builderDraft, builderSave, builderToday, useAutomationsBuilder, useBuilderRuns, useBuilderSchema } from "../../lib/api";
import { BuilderForm, type Spec } from "./BuilderForm";
import { ExploreRunsList, RunCardPanel } from "./BuilderRunCard";
import { TodayView } from "./BuilderToday";
import { CardHelpButton } from "../../components/CardHelp";
import { SECTION_HELP } from "./BuilderHelp";

/**
 * The Builder page (#1180): one automation at a time, built in five sections (structure and data,
 * the entry signal, the gates, the legs and their price, exits and size), beside the engine's decision
 * of it today and its explore runs. Every field writes the spec the paper loop reads; Check is the
 * module's own rule, Save goes through the orchestrator's config editor and is always off, and
 * Explore run queues the saved automation where our data covers it. Declaring a study and turning an
 * automation on happen outside the page.
 */

/** What each block label says, in words. */
export const STATE_TEXT: Record<BuilderBlock["state"], string> = {
  backtested: "backtested",
  changed: "changed since its backtest",
  not_backtested: "not backtested",
};

/** "Our data covers a backtest" (naming a licensed store when it is one) or which need falls short,
 *  and the nearest store. */
export function SuggestionLine({ s }: { s: BuilderSuggestion }) {
  if (s.possible) {
    const vendor = [...new Set(s.needs.filter((n) => n.licensed && n.source).map((n) => n.source as string))];
    return (
      <div className="builder-suggestion" data-possible="yes" data-licensed={s.licensed ? "yes" : "no"}>
        {s.licensed
          ? `The licensed vendor store (${vendor.join(", ")}) covers a backtest of ${s.blocks.join(", ")} from ${s.window[0]} to ${s.window[1]}, where our own data falls short. Its result is licensed: it stays on this machine and shows only while licensed results may.`
          : `Our own data covers a backtest of ${s.blocks.join(", ")} from ${s.window[0]} to ${s.window[1]}.`}
      </div>
    );
  }
  const short = s.needs.filter((n) => !n.covered);
  return (
    <div className="builder-suggestion" data-possible="no">
      No backtest yet: our data does not cover {s.window[0]} to {s.window[1]} for{" "}
      {short.map((n) => `${n.field}${n.expiry ? ` (${n.expiry})` : ""} at ${n.cadence}`).join(", ")}.
      {short.map((n) =>
        n.nearest ? (
          <div key={n.field} className="muted">
            nearest for {n.field}: {n.nearest.source} {n.nearest.first} to {n.nearest.last}
            {n.nearest.why ? ` (${n.nearest.why})` : ""}
          </div>
        ) : null,
      )}
    </div>
  );
}

/** The keys the editor never shows: the name is its own field, a register label is a study's to
 *  write and a save is always off. */
const HIDDEN = new Set(["name", "register", "enabled"]);

export function editableSpec(spec: Spec): Spec {
  return Object.fromEntries(Object.entries(spec).filter(([k]) => !HIDDEN.has(k)));
}

export function editorText(spec: Spec): string {
  return JSON.stringify(editableSpec(spec), null, 2);
}

/** A new automation's starting point: every key the loop requires, nothing it would guess. */
export const NEW_SPEC: Spec = {
  symbol: "SPX",
  expiry: { kind: "dte", days: 0 },
  entry: { kind: "time", at: "10:00" },
  weekdays: [0, 1, 2, 3, 4],
  legs: [],
  size: { contracts: 1 },
  limits: { max_open: 1, per_day: 1 },
};

function viewBlocks(view: Record<string, unknown> | null | undefined): Record<string, BuilderBlock["state"]> {
  const blocks = Array.isArray(view?.["blocks"]) ? (view!["blocks"] as Array<Record<string, unknown>>) : [];
  return Object.fromEntries(blocks.map((b) => [String(b["block"] ?? ""), (b["state"] as BuilderBlock["state"]) ?? "not_backtested"]));
}

type Outcome =
  | { kind: "idle" }
  | { kind: "error"; text: string; problems?: string[] }
  | { kind: "checked"; isNew: boolean }
  | { kind: "saved"; added: boolean; backup: string | null }
  | { kind: "queued"; text: string };

/** A draft is an unsaved automation handed over from the wizard's "Open in the full form". */
export type Mode = { kind: "saved"; name: string } | { kind: "copy"; from: string } | { kind: "new" } | { kind: "draft"; name: string; spec: Spec };

/** The automation's explore runs, and one run's card when opened. */
function RunsPanel({ name }: { name: string }) {
  const q = useBuilderRuns(name, true);
  const [open, setOpen] = useState<string | null>(null);
  if (open !== null) return <RunCardPanel name={name} run={open} onClose={() => setOpen(null)} />;
  if (q.isLoading) return <div className="muted">Reading the runs…</div>;
  if (q.error || !q.data) return <div className="error">Could not read the runs.</div>;
  return <ExploreRunsList data={q.data} onOpen={setOpen} />;
}

/** "Today, live", read when asked: an unsaved edit is decided as it stands. */
function TodayPanel({ name, spec, dirty }: { name: string; spec: Spec | null; dirty: boolean }) {
  const [t, setT] = useState<BuilderToday | null>(null);
  const [busy, setBusy] = useState(false);
  const read = async () => {
    setBusy(true);
    const r = await builderToday(name, dirty && spec ? editableSpec(spec) : undefined);
    setBusy(false);
    setT(r.error ? { name, state: "not_decided", reason: r.error } : r);
  };
  return (
    <>
      <div className="builder-run-section-head">
        <h4>Today, live</h4>
        <CardHelpButton cardTitle="Today, live" help={SECTION_HELP["today"]!} />
        <button type="button" className="btn" disabled={busy || !spec} onClick={() => void read()}>
          {t ? "Read again" : "Read now"}
        </button>
        <span className="muted">{dirty ? "the edit as it stands" : "as saved"}</span>
      </div>
      {t ? <TodayView t={t} /> : <p className="muted">The engine's checks for this automation on today's stream cache, as paper would read them.</p>}
    </>
  );
}

/** The action row: check, save off, explore run; declaring and switching on are outside the page. */
export function ActionRow({
  busy,
  canCheck,
  canRun,
  runWhy,
  onCheck,
  onSave,
  onRun,
}: {
  busy: boolean;
  canCheck: boolean;
  canRun: boolean;
  runWhy: string | null;
  onCheck: () => void;
  onSave: () => void;
  onRun: () => void;
}) {
  return (
    <div className="builder-section builder-actions">
      <div className="builder-editor-actions">
        <button type="button" className="btn" disabled={busy || !canCheck} onClick={onCheck}>
          Check
        </button>
        <button type="button" className="btn" disabled={busy || !canCheck} onClick={onSave}>
          Save (off)
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !canRun} title={runWhy ?? undefined} onClick={onRun}>
          Explore run
        </button>
        <span className="builder-actions-gap" />
        <button type="button" className="btn" disabled title="A study declaration (RULE, CHAIN, the register row) is written and reviewed outside the page">
          Declare…
        </button>
        <button type="button" className="btn" disabled title="Turning an automation on is a hand edit or its group's switch">
          Promote to paper
        </button>
      </div>
      {runWhy && <p className="muted">Explore run: {runWhy}</p>}
      <p className="muted">
        Explore run queues the saved automation over the window and store its suggestion names, only where that data covers every
        block. It is descriptive, never a trial or a verdict, and moves no label. Declare is a study declaration made and reviewed
        outside the page. Promote turns the automation on, which stays a hand edit or its group's switch.
      </p>
    </div>
  );
}

/** Why Explore run is not offered, or null when it is. */
export function runBlocked(a: BuilderAutomation | null, dirty: boolean, mode: Mode): string | null {
  if (mode.kind !== "saved" || a === null) return "save the automation first";
  if (dirty) return "save the edit first: a run reads the saved automation";
  if (!a.suggestion) return "every block is backtested already";
  if (!a.suggestion.possible) return "our data does not cover it (see the coverage above)";
  return null;
}

export function Workspace({ a, schema, mode, onSaved }: { a: BuilderAutomation | null; schema: BuilderSchema; mode: Mode; onSaved: (name: string) => void }) {
  const qc = useQueryClient();
  const source = mode.kind === "saved" ? mode.name : mode.kind === "copy" ? mode.from : null;
  const [name, setName] = useState(mode.kind === "saved" || mode.kind === "draft" ? mode.name : mode.kind === "copy" ? `${mode.from}-copy` : "my-automation");
  const [spec, setSpec] = useState<Spec | null>(mode.kind === "new" ? { ...NEW_SPEC } : mode.kind === "draft" ? mode.spec : null);
  const [dirty, setDirty] = useState(mode.kind !== "saved");
  const [json, setJson] = useState<string | null>(null);
  const [labels, setLabels] = useState<Record<string, BuilderBlock["state"]>>(
    mode.kind === "saved" && a ? Object.fromEntries(a.blocks.map((b) => [b.block, b.state])) : {},
  );
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (source === null) return;
    let live = true;
    void builderDraft(source).then((r) => {
      if (!live) return;
      if (r.ok && r.automation) setSpec(editableSpec(r.automation));
      else setOutcome({ kind: "error", text: r.error ?? `${source} could not be read` });
    });
    return () => {
      live = false;
    };
  }, [source]);

  const edit = (next: Spec) => {
    setSpec(next);
    setDirty(true);
    setOutcome({ kind: "idle" });
  };

  /** The spec as it stands, from the JSON text when that is open. */
  const current = (): Spec | null => {
    if (json === null) return spec;
    try {
      const v = JSON.parse(json) as unknown;
      if (typeof v === "object" && v !== null && !Array.isArray(v)) return v as Spec;
    } catch {
      /* reported below */
    }
    setOutcome({ kind: "error", text: "The automation is not a JSON object." });
    return null;
  };

  const check = async () => {
    const s = current();
    if (!s) return;
    setBusy(true);
    const r = await builderDraft(name, s);
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: r.error ?? "the check failed" });
    else if (r.problems && r.problems.length > 0) setOutcome({ kind: "error", text: "Not saved as it stands:", problems: r.problems });
    else {
      setLabels(viewBlocks(r.view));
      setOutcome({ kind: "checked", isNew: r.new === true });
    }
  };

  const save = async () => {
    const s = current();
    if (!s) return;
    setBusy(true);
    const r = await builderSave(name, s);
    setBusy(false);
    if (!r.ok) {
      setOutcome({ kind: "error", text: r.problems?.length ? "Not saved:" : (r.error ?? "the save failed"), problems: r.problems });
      return;
    }
    setOutcome({ kind: "saved", added: r.added === true, backup: r.backup ?? null });
    setDirty(false);
    void qc.invalidateQueries({ queryKey: ["automations-builder"] });
    onSaved(name);
  };

  const run = async () => {
    setBusy(true);
    const r = await builderBacktest(name);
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: `Not queued: ${r.error}` });
    else {
      setOutcome({ kind: "queued", text: `Queued ${r.run} (task ${r.task ?? "?"}), ${r.window?.[0]} to ${r.window?.[1]}.` });
      void qc.invalidateQueries({ queryKey: ["automations-builder-runs", name] });
    }
  };

  const why = runBlocked(a, dirty, mode);
  return (
    <>
      <div className="builder-picker">
        <label className="builder-lbl" htmlFor="builder-name">
          automation
        </label>
        <input id="builder-name" className="builder-input" value={name} disabled={mode.kind === "saved"} onChange={(e) => setName(e.target.value)} />
        <span className="chip">{mode.kind === "saved" ? (a?.enabled ? "on" : "off") : "not saved"}</span>
        {dirty && mode.kind === "saved" && <span className="chip chip-warn">edited, not saved</span>}
        {a?.group && mode.kind === "saved" && <span className="chip">group {a.group}</span>}
        <span className="chip">start {(mode.kind === "saved" && a?.start) || "not set"}</span>
        {a?.register && mode.kind === "saved" && (
          <span className="chip">
            item {a.register.item}: {a.register.verdict.replace("_", " ")}
          </span>
        )}
        <button type="button" className="btn btn-quiet" onClick={() => setJson(json === null ? editorText(spec ?? {}) : null)}>
          {json === null ? "Edit as JSON" : "Back to the form"}
        </button>
      </div>
      {mode.kind === "saved" && a?.suggestion && <SuggestionLine s={a.suggestion} />}
      <div className="builder-layout">
        <div className="builder-form">
          {spec === null ? (
            <div className="muted">Reading {source}…</div>
          ) : json !== null ? (
            <textarea
              className="builder-editor-text"
              aria-label="automation"
              spellCheck={false}
              rows={Math.min(40, json.split("\n").length + 1)}
              value={json}
              onChange={(e) => {
                setJson(e.target.value);
                setDirty(true);
                setOutcome({ kind: "idle" });
              }}
            />
          ) : (
            <BuilderForm schema={schema} spec={spec} labels={labels} onChange={edit} />
          )}
          <ActionRow busy={busy} canCheck={spec !== null} canRun={why === null} runWhy={why} onCheck={() => void check()} onSave={() => void save()} onRun={() => void run()} />
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
              No problems. {outcome.isNew ? "A new automation" : "This edit"} would save off, with the labels shown on each section.
            </div>
          )}
          {outcome.kind === "saved" && (
            <div className="builder-check" role="status">
              Saved {name} off ({outcome.added ? "added" : "replaced"}){outcome.backup ? "; the previous file is backed up" : ""}.
            </div>
          )}
          {outcome.kind === "queued" && (
            <div className="builder-check" role="status">
              {outcome.text}
            </div>
          )}
        </div>
        <aside className="builder-side" aria-label="Today and runs">
          <div className="builder-section">
            <TodayPanel name={name} spec={spec} dirty={dirty} />
          </div>
          {mode.kind === "saved" && (
            <div className="builder-section">
              <div className="builder-run-section-head">
                <h4>Explore runs · this automation</h4>
                <CardHelpButton cardTitle="Explore runs" help={SECTION_HELP["runs"]!} />
              </div>
              <RunsPanel name={mode.name} />
            </div>
          )}
        </aside>
      </div>
    </>
  );
}

export function BuilderPage() {
  const q = useAutomationsBuilder();
  const s = useBuilderSchema();
  const [mode, setMode] = useState<Mode | null>(null);
  const [wizard, setWizard] = useState<{ from: string | null; n: number } | null>(null);
  if (q.isLoading || s.isLoading) return <div className="muted">Loading the automations…</div>;
  if (q.error) return <div className="error">Could not read the automations: {String(q.error)}</div>;
  const data = q.data;
  if (!data) return null;
  if (data.error) return <div className="error">{data.error}</div>;
  const schema = s.data?.schema;
  if (!schema) return <div className="error">The form's choices could not be read{s.data?.error ? `: ${s.data.error}` : "."}</div>;
  const current: Mode = mode ?? (data.automations[0] ? { kind: "saved", name: data.automations[0].name } : { kind: "new" });
  const a = current.kind === "saved" ? (data.automations.find((x) => x.name === current.name) ?? null) : null;
  const key = current.kind === "saved" ? `saved:${current.name}` : current.kind === "copy" ? `copy:${current.from}` : current.kind === "draft" ? `draft:${current.name}` : "new";
  if (wizard) {
    return (
      <div className="builder-page">
        <BuilderWizard
          key={wizard.n}
          schema={schema}
          automations={data.automations}
          copyFrom={wizard.from}
          onCancel={() => setWizard(null)}
          onAnother={() => setWizard({ from: null, n: wizard.n + 1 })}
          onOpenForm={(name, spec) => {
            setWizard(null);
            setMode({ kind: "draft", name, spec });
          }}
          onSaved={(name) => {
            setWizard(null);
            setMode({ kind: "saved", name });
          }}
        />
      </div>
    );
  }
  return (
    <div className="builder-page">
      <div className="builder-picker">
        <label className="builder-lbl" htmlFor="builder-pick">
          open
        </label>
        <select id="builder-pick" className="builder-input" value={current.kind === "saved" ? current.name : ""} onChange={(e) => setMode({ kind: "saved", name: e.currentTarget.value })}>
          {current.kind !== "saved" && <option value="">{current.kind === "copy" ? `a copy of ${current.from}` : "a new automation"}</option>}
          {data.automations.map((x) => (
            <option key={x.name} value={x.name}>
              {x.name}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={() => setWizard({ from: null, n: 0 })}>
          New
        </button>
        <button type="button" className="btn" disabled={current.kind !== "saved"} onClick={() => current.kind === "saved" && setWizard({ from: current.name, n: 0 })}>
          Copy as new
        </button>
        <button type="button" className="link-button" title="a blank automation straight in the form, without the steps" onClick={() => setMode({ kind: "new" })}>
          new in the form
        </button>
      </div>
      <p className="muted">
        Every field writes the automation the paper loop reads. Check asks the automations module whether paper would accept it; Save
        writes it through the suite's config editor, always off. A block is backtested while it stands as the study ran it.
      </p>
      <Workspace key={key} a={a} schema={schema} mode={current} onSaved={(n) => setMode({ kind: "saved", name: n })} />
    </div>
  );
}
