import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GridGate, GridResult, GridRow, Grids } from "@console/shared";
import { builderTrySettings, gridDeclare, gridHoldout, useGridResult, useGrids } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * The settings grid (#1294): exit and width settings for one saved automation, each replayed beside the
 * automation's own over its newest long Builder run, read on the part before a split fixed when the grid
 * was queued. Every row is a hypothesis, never a change to the automation. A setting that survives every
 * check and that the later part can test may be declared, then tested once on the later part. It shares
 * the idea search's layout and classes.
 */

/** Why a row failed, one phrase per check it missed. */
export const GRID_GATE_WORDS: Record<GridGate, string> = {
  gains: "the change did not plainly gain",
  beats_menu: "no better than the best random change",
  plateau: "a lone peak: its neighbouring settings disagree",
  stable: "not gaining across the folds",
  pays_at_1_5x: "the gain is gone at 1.5x costs",
};

export function gridFailedChecks(row: GridRow): string[] {
  if (!row.scored) return [row.why ?? "not scored"];
  return (Object.keys(GRID_GATE_WORDS) as GridGate[]).filter((g) => row.gates?.[g] === false).map((g) => GRID_GATE_WORDS[g]);
}

export function gridRowState(row: GridRow): "declare" | "test" | "tested" | "untestable" | "failed" {
  if (row.held_out_read) return "tested";
  if (row.declaration) return "test";
  if (!row.survives) return "failed";
  return row.held_out?.testable ? "declare" : "untestable";
}

export function GridList({ data, onOpen }: { data: Grids; onOpen: (id: string) => void }) {
  const live = data.tasks.filter((t) => t.status === "queued" || t.status === "running");
  const failed = data.tasks.filter((t) => t.status === "failed");
  return (
    <div className="builder-ideas" data-grids={data.grids.length}>
      {live.map((t) => (
        <p key={t.id ?? t.grid} className="builder-run-live" data-status={t.status}>
          task {t.id ?? "?"} · {t.grid} · {t.status} · each setting is a full replay
        </p>
      ))}
      {failed.slice(0, 2).map((t) => (
        <p key={t.id ?? t.grid} className="error">
          {t.grid} failed{t.note ? `: ${t.note}` : ""}
        </p>
      ))}
      {data.grids.map((g) => (
        <p key={g.name} className="builder-idea-brief">
          <button type="button" className="link-button" onClick={() => onOpen(g.name)}>
            {g.name.slice(-10)}
          </button>{" "}
          · {g.tried} settings scored before {g.split}, {g.survivors} survive{g.survivors === 1 ? "s" : ""}
          {g.held_out_used > 0 ? ` · held-out part used ${g.held_out_used}×` : ""}
        </p>
      ))}
      {data.tasks.length === 0 && data.grids.length === 0 && <p className="muted">No settings grid yet.</p>}
      {data.withheld > 0 && <p className="muted">{data.withheld} licensed grid(s) not shown.</p>}
      {data.results_unavailable && <p className="error">The grids could not be read.</p>}
    </div>
  );
}

function Action({ row, busy, onDeclare, onTest }: { row: GridRow; busy: boolean; onDeclare: (id: string) => void; onTest: (id: string) => void }) {
  const state = gridRowState(row);
  if (state === "declare") {
    return (
      <button type="button" className="btn" disabled={busy} onClick={() => onDeclare(row.id)}>
        Declare
      </button>
    );
  }
  if (state === "test") {
    return (
      <button type="button" className="btn" disabled={busy} onClick={() => onTest(row.id)} title="Reads the later sessions once; it cannot be repeated">
        Test once on held-out
      </button>
    );
  }
  if (state === "tested") {
    const r = row.held_out_read!;
    const words = { passed: "Held-out: passed", failed: "Held-out: failed", no_verdict: "Held-out: no verdict" }[r.verdict];
    return (
      <span className={`builder-idea-verdict ${r.verdict}`} title={`${r.moved} held-out sessions changed, gain ${fmtMoney(r.gain)}`}>
        {words}
      </span>
    );
  }
  if (state === "untestable") return <span className="muted">Not testable on our history</span>;
  return <span className="muted">{gridFailedChecks(row).join("; ")}</span>;
}

function plateauWords(v: string | undefined): string {
  return v === "lone_peak" ? "lone peak" : v === "plateau" ? "plateau" : "no neighbours";
}

export function GridView({
  grid,
  busy = false,
  onDeclare = () => {},
  onTest = () => {},
}: {
  grid: GridResult;
  busy?: boolean;
  onDeclare?: (id: string) => void;
  onTest?: (id: string) => void;
}) {
  const scored = grid.rows.filter((r) => r.scored);
  const unscored = grid.rows.filter((r) => !r.scored);
  const refused = Object.entries(grid.refused ?? {});
  const declared = grid.rows.filter((r) => r.declaration);
  return (
    <div className="builder-ideas-view" data-grid={grid.name}>
      <p className="builder-ideas-lead">
        {grid.tried} settings scored on {grid.base.sessions} sessions before {grid.split} ({grid.run}), each against the
        automation's own replay. The later {grid.base.held_out_sessions} sessions were not read. <strong>{grid.survivors}</strong>{" "}
        survive every check{grid.held_out_used > 0 ? `; the held-out part has been used ${grid.held_out_used}×` : ""}. Holm keeps{" "}
        {grid.holm_survivors}, BH keeps {grid.bh_survivors} of the {grid.tried}.
      </p>
      {!grid.base_matches_run && (
        <p className="error">Replaying the automation did not give the run's own trades again: read these rows with care.</p>
      )}
      <ol className="builder-ideas-list">
        {scored.map((r) => (
          <li key={r.id} data-state={gridRowState(r)} className={r.survives ? "builder-idea survives" : "builder-idea"}>
            <div className="builder-idea-head">
              <span className="builder-idea-name">{r.setting}</span>
              <Action row={r} busy={busy} onDeclare={onDeclare} onTest={onTest} />
            </div>
            <div className="builder-idea-figs">
              <span>
                changes <span className="num">{r.moved}</span> days, <span className="num">{fmtMoney(r.mean_change ?? null)}</span>/day
                {r.change_ci && (
                  <span className="muted">
                    {" "}
                    ({fmtMoney(r.change_ci[0])} to {fmtMoney(r.change_ci[1])})
                  </span>
                )}
              </span>
              <span title="What is left after the grid's own luck: the best random change's typical reach taken off">
                gains <span className="num">{fmtMoney(r.gain ?? null)}</span>, <span className="num">{fmtMoney(r.gain_shrunk ?? null)}</span>{" "}
                after the grid's luck
              </span>
              <span className="muted">
                <span title={`${(r.p ?? 1).toFixed(3)} against random changes of its own alone`}>
                  vs random <span className="num">{(r.p_menu ?? 1).toFixed(3)}</span>
                </span>{" "}
                · {plateauWords(r.plateau?.verdict)} · {r.stability?.folds_positive ?? 0}/4 folds ·{" "}
                {r.gates?.pays_at_1_5x ? "holds" : "gone"} at 1.5x costs
              </span>
            </div>
          </li>
        ))}
      </ol>
      {(unscored.length > 0 || refused.length > 0) && (
        <p className="muted builder-grid-unscored">
          Not scored: {[...unscored.map((r) => `${r.setting} (${r.why ?? "not scored"})`), ...refused.map(([k, why]) => `${k} (refused: ${why})`)].join("; ")}.
        </p>
      )}
      {declared.map((r) => (
        <details key={r.id} className="builder-idea-declaration">
          <summary>Register row draft · {r.setting}</summary>
          <p className="muted">
            Add it to research/hypotheses.json with its id, study slug and mechanism. It carries no figures, only the setting, the
            split and the rule.
          </p>
          <pre>{JSON.stringify(r.declaration, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function GridPanel({ name, id, onClose }: { name: string; id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useGridResult(name, id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (f: () => Promise<{ ok: boolean; error?: string }>) => {
    setBusy(true);
    setError(null);
    try {
      const out = await f();
      if (!out.ok) setError(out.error ?? "refused");
      await qc.invalidateQueries({ queryKey: ["automations-builder-grid", name, id] });
      await qc.invalidateQueries({ queryKey: ["automations-builder-grids", name] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <button type="button" className="link-button" onClick={onClose}>
        ← all grids
      </button>
      {q.isLoading && <div className="muted">Reading the grid…</div>}
      {(q.error || (!q.isLoading && !q.data)) && <div className="error">Could not read the grid.</div>}
      {error && <div className="error">{error}</div>}
      {q.data && (
        <GridView
          grid={q.data}
          busy={busy}
          onDeclare={(setting) => void act(() => gridDeclare(name, id, setting))}
          onTest={(setting) => {
            if (window.confirm("Test this setting on the held-out sessions? Each setting gets one test, and every use is logged.")) {
              void act(() => gridHoldout(name, id, setting));
            }
          }}
        />
      )}
    </div>
  );
}

/** "Try settings" and the automation's grids, one opened whole when asked. */
export function GridsPanel({ name }: { name: string }) {
  const qc = useQueryClient();
  const q = useGrids(name);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (open !== null) return <GridPanel name={name} id={open} onClose={() => setOpen(null)} />;
  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      const out = await builderTrySettings(name);
      setNote(out.ok ? `Queued (task ${String(out.task ?? "?")}), split fixed at ${out.split ?? "?"}. Each setting is a full replay.` : (out.error ?? "refused"));
      await qc.invalidateQueries({ queryKey: ["automations-builder-grids", name] });
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <button type="button" className="btn" disabled={busy} onClick={() => void run()}>
        {busy ? "Queuing…" : "Try settings"}
      </button>
      {note && <p className="builder-check">{note}</p>}
      {q.isLoading && <div className="muted">Reading the grids…</div>}
      {q.error && <div className="error">Could not read the grids.</div>}
      {q.data && <GridList data={q.data} onOpen={setOpen} />}
    </div>
  );
}
