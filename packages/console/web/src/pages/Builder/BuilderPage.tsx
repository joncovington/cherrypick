import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderBlock, BuilderRuns, BuilderSuggestion } from "@console/shared";
import { builderBacktest, builderDraft, builderSave, useAutomationsBuilder, useBuilderRuns } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/** What each block label says, in words. */
export const STATE_TEXT: Record<BuilderBlock["state"], string> = {
  backtested: "backtested",
  changed: "changed since its backtest",
  not_backtested: "not backtested",
};

const STATE_CHIP: Record<BuilderBlock["state"], string> = {
  backtested: "chip chip-ok",
  changed: "chip chip-warn",
  not_backtested: "chip",
};

function value(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function Block({ b }: { b: BuilderBlock }) {
  const keys = Object.entries(b.keys);
  return (
    <div className="builder-block" data-block={b.block}>
      <div className="builder-block-head">
        <strong>{b.block}</strong> <span className={STATE_CHIP[b.state]}>{STATE_TEXT[b.state]}</span>
      </div>
      {keys.length === 0 ? (
        <div className="muted">none set</div>
      ) : (
        <dl className="builder-keys">
          {keys.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>
                <code>{value(v)}</code>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** "Our data covers a backtest" or which need falls short, and the nearest store. */
export function SuggestionLine({ s }: { s: BuilderSuggestion }) {
  if (s.possible) {
    return (
      <div className="builder-suggestion" data-possible="yes">
        Our own data covers a backtest of {s.blocks.join(", ")} from {s.window[0]} to {s.window[1]}.
      </div>
    );
  }
  const short = s.needs.filter((n) => !n.covered);
  return (
    <div className="builder-suggestion" data-possible="no">
      No backtest yet: our own data does not cover {s.window[0]} to {s.window[1]} for{" "}
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

export function editorText(spec: Record<string, unknown>): string {
  return JSON.stringify(Object.fromEntries(Object.entries(spec).filter(([k]) => !HIDDEN.has(k))), null, 2);
}

function viewBlocks(view: Record<string, unknown> | null | undefined): Array<{ block: string; state: BuilderBlock["state"] }> {
  const blocks = Array.isArray(view?.["blocks"]) ? (view!["blocks"] as Array<Record<string, unknown>>) : [];
  return blocks.map((b) => ({ block: String(b["block"] ?? ""), state: (b["state"] as BuilderBlock["state"]) ?? "not_backtested" }));
}

type Outcome =
  | { kind: "idle" }
  | { kind: "error"; text: string; problems?: string[] }
  | { kind: "checked"; blocks: Array<{ block: string; state: BuilderBlock["state"] }>; isNew: boolean }
  | { kind: "saved"; added: boolean; backup: string | null };

/**
 * One automation's editor. It starts from the automation as the module resolves it (`draft` by name),
 * checks an edit with the module's own rule, and saves through the orchestrator's config editor. A save
 * is always off: switching an automation on stays its own step, outside this page.
 */
export function BuilderEditor({ source, copy, onClose }: { source: string; copy: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(copy ? `${source}-copy` : source);
  const [text, setText] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void builderDraft(source).then((r) => {
      if (!live) return;
      if (r.ok && r.automation) setText(editorText(r.automation));
      else setOutcome({ kind: "error", text: r.error ?? `${source} could not be read` });
    });
    return () => {
      live = false;
    };
  }, [source]);

  const parsed = (): Record<string, unknown> | null => {
    try {
      const v = JSON.parse(text ?? "") as unknown;
      if (typeof v === "object" && v !== null && !Array.isArray(v)) return v as Record<string, unknown>;
    } catch {
      /* reported below */
    }
    setOutcome({ kind: "error", text: "The automation is not a JSON object." });
    return null;
  };

  const check = async () => {
    const automation = parsed();
    if (!automation) return;
    setBusy(true);
    const r = await builderDraft(name, automation);
    setBusy(false);
    if (!r.ok) setOutcome({ kind: "error", text: r.error ?? "the check failed" });
    else if (r.problems && r.problems.length > 0) setOutcome({ kind: "error", text: "Not saved as it stands:", problems: r.problems });
    else setOutcome({ kind: "checked", blocks: viewBlocks(r.view), isNew: r.new === true });
  };

  const save = async () => {
    const automation = parsed();
    if (!automation) return;
    setBusy(true);
    const r = await builderSave(name, automation);
    setBusy(false);
    if (!r.ok) {
      setOutcome({ kind: "error", text: r.problems?.length ? "Not saved:" : (r.error ?? "the save failed"), problems: r.problems });
      return;
    }
    setOutcome({ kind: "saved", added: r.added === true, backup: r.backup ?? null });
    void qc.invalidateQueries({ queryKey: ["automations-builder"] });
  };

  return (
    <div className="builder-editor" data-editor={source}>
      <label className="builder-editor-name">
        name <input value={name} disabled={!copy} onChange={(e) => setName(e.target.value)} aria-label="automation name" />
      </label>
      {text === null && outcome.kind === "idle" ? (
        <div className="muted">Reading {source}…</div>
      ) : (
        <textarea
          className="builder-editor-text"
          aria-label="automation"
          spellCheck={false}
          rows={Math.min(40, (text ?? "").split("\n").length + 1)}
          value={text ?? ""}
          onChange={(e) => {
            setText(e.target.value);
            setOutcome({ kind: "idle" });
          }}
        />
      )}
      <p className="muted">
        A save writes this automation <strong>off</strong>, through the suite's config editor (with a backup). Turning it
        on is a separate step. Its register label stays as a study recorded it.
      </p>
      <div className="builder-editor-actions">
        <button type="button" className="btn" disabled={busy || text === null} onClick={() => void check()}>
          Check
        </button>
        <button type="button" className="btn" disabled={busy || text === null} onClick={() => void save()}>
          Save (off)
        </button>
        <button type="button" className="btn btn-quiet" onClick={onClose}>
          Close
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
          No problems. {outcome.isNew ? "A new automation" : "This edit"} would save off, with its blocks{" "}
          {outcome.blocks.map((b) => `${b.block} ${STATE_TEXT[b.state]}`).join(", ")}.
        </div>
      )}
      {outcome.kind === "saved" && (
        <div className="builder-check" role="status">
          Saved {name} off ({outcome.added ? "added" : "replaced"}){outcome.backup ? "; the previous file is backed up" : ""}.
        </div>
      )}
    </div>
  );
}

/**
 * The run card: an automation's Builder backtests, the queue rows first, then each result's figures
 * as the run wrote them. Every run is an explore run: description, never a verdict.
 */
export function BuilderRunsView({ data }: { data: BuilderRuns }) {
  return (
    <div className="builder-runs" data-runs={data.runs.length}>
      <p className="muted">Explore runs: descriptive only, no trial and no verdict. They move no label.</p>
      {data.tasks.length > 0 && (
        <ul className="builder-run-tasks">
          {data.tasks.map((t) => (
            <li key={t.id ?? t.run} data-status={t.status}>
              {t.run}: {t.status}
              {t.status === "failed" && t.note ? ` (${t.note})` : ""}
            </li>
          ))}
        </ul>
      )}
      {data.runs.map((r) => (
        <table key={r.run} className="builder-run-table">
          <caption>
            {r.run}: {r.symbol} on {r.source}
            {r.sessions ? `, ${r.sessions[0]} to ${r.sessions[1]}` : ""} ({r.session_count ?? 0} sessions,{" "}
            {r.cadence ?? "stored cadence"}){r.still_open ? `; ${r.still_open} still open at the end` : ""}
          </caption>
          <thead>
            <tr>
              <th>arm</th>
              <th>sessions</th>
              <th>trades</th>
              <th>P&amp;L</th>
              <th>per trade</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(r.summary).map(([arm, s]) => (
              <tr key={arm}>
                <td>{arm}</td>
                <td>{s.sessions ?? "—"}</td>
                <td>{s.trades ?? "—"}</td>
                <td>{fmtMoney(s.net)}</td>
                <td>{fmtMoney(s.mean_net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
      {data.tasks.length === 0 && data.runs.length === 0 && <p className="muted">No backtest queued from here yet.</p>}
      {data.withheld > 0 && <p className="muted">{data.withheld} licensed result(s) not shown.</p>}
      {data.results_unavailable && <p className="error">The results could not be read.</p>}
    </div>
  );
}

function BuilderRunsPanel({ name }: { name: string }) {
  const q = useBuilderRuns(name, true);
  if (q.isLoading) return <div className="muted">Reading the runs…</div>;
  if (q.error || !q.data) return <div className="error">Could not read the runs.</div>;
  return <BuilderRunsView data={q.data} />;
}

/** Queue one explore backtest. Rendered only where the automation's suggestion says our data covers it. */
function BacktestButton({ a, onQueued }: { a: BuilderAutomation; onQueued: () => void }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const go = async (): Promise<void> => {
    setBusy(true);
    const r = await builderBacktest(a.name);
    setBusy(false);
    setSaid(r.ok ? `Queued ${r.run} (task ${r.task ?? "?"}), ${r.window?.[0]} to ${r.window?.[1]}.` : `Not queued: ${r.error}`);
    if (r.ok) {
      void qc.invalidateQueries({ queryKey: ["automations-builder-runs", a.name] });
      onQueued();
    }
  };
  return (
    <>
      <button type="button" className="btn" disabled={busy} onClick={() => void go()}>
        Backtest
      </button>
      {said && (
        <span className="builder-check" role="status">
          {said}
        </span>
      )}
    </>
  );
}

export function BuilderCard({ a }: { a: BuilderAutomation }) {
  const [editing, setEditing] = useState<null | "edit" | "copy">(null);
  const [runs, setRuns] = useState(false);
  return (
    <section className="card builder-card" data-automation={a.name}>
      <header className="builder-card-head">
        <h3>{a.name}</h3>
        <span className="chip">{a.symbol}</span>
        {a.group && <span className="chip">group {a.group}</span>}
        <span className={a.enabled ? "chip chip-ok" : "chip"}>{a.enabled ? "on" : "off"}</span>
        {a.register && (
          <span className="chip">
            item {a.register.item}: {a.register.verdict.replace("_", " ")}
          </span>
        )}
      </header>
      {a.note && <p className="muted">{a.note}</p>}
      <p className="builder-summary">{a.summary}</p>
      <div className="builder-enrolment">
        <span>start {a.start ?? "not set"}</span>
        <span>forward check {a.forward ? `${value(a.forward["measure"])} of ${value(a.forward["field"])}, ${value(a.forward["min_obs"])} to read` : "not declared"}</span>
      </div>
      {a.suggestion && <SuggestionLine s={a.suggestion} />}
      <div className="builder-blocks">
        {a.blocks.map((b) => (
          <Block key={b.block} b={b} />
        ))}
      </div>
      {editing === null ? (
        <div className="builder-editor-actions">
          <button type="button" className="btn" onClick={() => setEditing("edit")}>
            Edit
          </button>
          <button type="button" className="btn" onClick={() => setEditing("copy")}>
            Copy as new
          </button>
          {a.suggestion?.possible && <BacktestButton a={a} onQueued={() => setRuns(true)} />}
          <button type="button" className="btn" onClick={() => setRuns((v) => !v)}>
            {runs ? "Hide runs" : "Runs"}
          </button>
        </div>
      ) : (
        <BuilderEditor source={a.name} copy={editing === "copy"} onClose={() => setEditing(null)} />
      )}
      {runs && <BuilderRunsPanel name={a.name} />}
    </section>
  );
}

export function BuilderPage() {
  const q = useAutomationsBuilder();
  if (q.isLoading) return <div className="muted">Loading the automations…</div>;
  if (q.error) return <div className="error">Could not read the automations: {String(q.error)}</div>;
  const data = q.data;
  if (!data) return null;
  if (data.error) return <div className="error">{data.error}</div>;
  return (
    <div className="builder-page">
      <p className="muted">
        Each automation block by block. A block is backtested while it stands as the study ran it; a changed or new
        block is not, and a paper result on it is descriptive until its forward check is read.
      </p>
      {data.automations.map((a) => (
        <BuilderCard key={a.name} a={a} />
      ))}
    </div>
  );
}
