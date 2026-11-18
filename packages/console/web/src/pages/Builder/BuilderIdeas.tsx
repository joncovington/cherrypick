import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { IdeaGate, IdeaRow, IdeaSearch, IdeaSearches } from "@console/shared";
import { builderFindIdeas, ideaDeclare, ideaHoldout, useIdeaSearch, useIdeaSearches } from "../../lib/api";
import { fmtMoney } from "../../lib/format";

/**
 * The idea search (#1294): entry filters for one saved automation, read on the part of its newest long
 * Builder run before a split fixed when the search was queued. Every row is a hypothesis, never a change
 * to the automation. A row that survives every check and that the later part can test may be declared
 * (its register row drafted, the split fixed), then tested once on the later part.
 */

/** Why a row failed, one phrase per check it missed. */
export const GATE_WORDS: Record<IdeaGate, string> = {
  loses: "the skipped days did not plainly lose",
  beats_menu: "no better than the best random filter",
  plateau: "a lone peak: its neighbouring bins disagree",
  stable: "not losing across the folds",
  adds: "a part of it adds nothing",
  pays_at_1_5x: "the rest does not pay at 1.5x costs",
};

export function failedChecks(row: IdeaRow): string[] {
  return (Object.keys(GATE_WORDS) as IdeaGate[]).filter((g) => row.gates[g] === false).map((g) => GATE_WORDS[g]);
}

/** What a person can do with a row now: declare it, test it once, or why neither. */
export function rowState(row: IdeaRow): "declare" | "test" | "tested" | "untestable" | "failed" {
  if (row.held_out_read) return "tested";
  if (row.declaration) return "test";
  if (!row.survives) return "failed";
  return row.held_out.testable ? "declare" : "untestable";
}

function share(x: number): string {
  return `${Math.round(x * 100)}%`;
}

export function IdeaSearchList({ data, onOpen }: { data: IdeaSearches; onOpen: (id: string) => void }) {
  const live = data.tasks.filter((t) => t.status === "queued" || t.status === "running");
  const failed = data.tasks.filter((t) => t.status === "failed");
  return (
    <div className="builder-ideas" data-searches={data.searches.length}>
      {live.map((t) => (
        <p key={t.id ?? t.search} className="builder-run-live" data-status={t.status}>
          task {t.id ?? "?"} · {t.search} · {t.status}
        </p>
      ))}
      {failed.slice(0, 2).map((t) => (
        <p key={t.id ?? t.search} className="error">
          {t.search} failed{t.note ? `: ${t.note}` : ""}
        </p>
      ))}
      {data.searches.map((s) => (
        <p key={s.name} className="builder-idea-brief">
          <button type="button" className="link-button" onClick={() => onOpen(s.name)}>
            {s.name.slice(-10)}
          </button>{" "}
          · {s.tried} tried before {s.split}, {s.survivors} survive{s.survivors === 1 ? "s" : ""}
          {s.held_out_used > 0 ? ` · held-out part used ${s.held_out_used}×` : ""}
        </p>
      ))}
      {data.tasks.length === 0 && data.searches.length === 0 && <p className="muted">No idea search yet.</p>}
      {data.withheld > 0 && <p className="muted">{data.withheld} licensed search(es) not shown.</p>}
      {data.results_unavailable && <p className="error">The searches could not be read.</p>}
    </div>
  );
}

function Action({
  row,
  busy,
  onDeclare,
  onTest,
}: {
  row: IdeaRow;
  busy: boolean;
  onDeclare: (id: string) => void;
  onTest: (id: string) => void;
}) {
  const state = rowState(row);
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
      <span className={`builder-idea-verdict ${r.verdict}`} title={`${r.skipped} held-out sessions skipped, net ${fmtMoney(-r.uplift)}`}>
        {words}
      </span>
    );
  }
  if (state === "untestable") return <span className="muted">Not testable on our history</span>;
  return <span className="muted">{failedChecks(row).join("; ")}</span>;
}

export function IdeaSearchView({
  search,
  busy = false,
  onDeclare = () => {},
  onTest = () => {},
}: {
  search: IdeaSearch;
  busy?: boolean;
  onDeclare?: (id: string) => void;
  onTest?: (id: string) => void;
}) {
  const declared = search.rows.filter((r) => r.declaration);
  return (
    <div className="builder-ideas-view" data-search={search.name}>
      <p className="builder-ideas-lead">
        {search.tried} filters tried on {search.base.sessions} sessions before {search.split} ({search.run}). The later{" "}
        {search.base.held_out_sessions} sessions were not read. <strong>{search.survivors}</strong> survive every check
        {search.held_out_used > 0 ? `; the held-out part has been used ${search.held_out_used}×` : ""}. Holm keeps{" "}
        {search.holm_survivors}, BH keeps {search.bh_survivors} of the {search.tried}.
      </p>
      <ol className="builder-ideas-list">
        {search.rows.map((r) => (
          <li key={r.id} data-state={rowState(r)} className={r.survives ? "builder-idea survives" : "builder-idea"}>
            <div className="builder-idea-head">
              <span className="builder-idea-name">{r.idea}</span>
              <Action row={r} busy={busy} onDeclare={onDeclare} onTest={onTest} />
            </div>
            <div className="builder-idea-figs">
              <span>
                skips <span className="num">{r.skipped}</span> days ({share(r.skipped_share)}), net{" "}
                <span className="num">{fmtMoney(r.skipped_mean)}</span>/day
                {r.skipped_ci && (
                  <span className="muted">
                    {" "}
                    ({fmtMoney(r.skipped_ci[0])} to {fmtMoney(r.skipped_ci[1])})
                  </span>
                )}
              </span>
              <span title="What is left after the search's own luck: the best random filter's typical reach taken off">
                gains <span className="num">{fmtMoney(r.uplift)}</span>,{" "}
                <span className="num">{fmtMoney(r.uplift_shrunk)}</span> after the search's luck
              </span>
              <span className="muted">
                <span title={`${r.p.toFixed(3)} against random filters of its size alone`}>
                  vs random <span className="num">{r.p_menu.toFixed(3)}</span>
                </span>{" "}
                · {r.plateau.verdict === "lone_peak" ? "lone peak" : r.plateau.verdict === "plateau" ? "plateau" : "no neighbours"} ·{" "}
                {r.stability.folds_negative}/4 folds{r.cost ? ` · ${r.cost.survives ? "pays" : "fails"} at 1.5x costs` : ""}
              </span>
            </div>
          </li>
        ))}
      </ol>
      {declared.map((r) => (
        <details key={r.id} className="builder-idea-declaration">
          <summary>Register row draft · {r.idea}</summary>
          <p className="muted">
            Add it to research/hypotheses.json with its id, study slug and mechanism. It carries no figures, only the idea, the
            split and the rule.
          </p>
          <pre>{JSON.stringify(r.declaration, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function SearchPanel({ name, id, onClose }: { name: string; id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useIdeaSearch(name, id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (f: () => Promise<{ ok: boolean; error?: string }>) => {
    setBusy(true);
    setError(null);
    try {
      const out = await f();
      if (!out.ok) setError(out.error ?? "refused");
      await qc.invalidateQueries({ queryKey: ["automations-builder-idea-search", name, id] });
      await qc.invalidateQueries({ queryKey: ["automations-builder-ideas", name] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <button type="button" className="link-button" onClick={onClose}>
        ← all searches
      </button>
      {q.isLoading && <div className="muted">Reading the search…</div>}
      {(q.error || (!q.isLoading && !q.data)) && <div className="error">Could not read the search.</div>}
      {error && <div className="error">{error}</div>}
      {q.data && (
        <IdeaSearchView
          search={q.data}
          busy={busy}
          onDeclare={(idea) => void act(() => ideaDeclare(name, id, idea))}
          onTest={(idea) => {
            if (window.confirm("Test this idea on the held-out sessions? Each idea gets one test, and every use is logged.")) {
              void act(() => ideaHoldout(name, id, idea));
            }
          }}
        />
      )}
    </div>
  );
}

/** "Find ideas" and the automation's searches, one opened whole when asked. */
export function IdeasPanel({ name }: { name: string }) {
  const qc = useQueryClient();
  const q = useIdeaSearches(name);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (open !== null) return <SearchPanel name={name} id={open} onClose={() => setOpen(null)} />;
  const find = async () => {
    setBusy(true);
    setNote(null);
    try {
      const out = await builderFindIdeas(name);
      setNote(out.ok ? `Queued (task ${String(out.task ?? "?")}), split fixed at ${out.split ?? "?"}.` : (out.error ?? "refused"));
      await qc.invalidateQueries({ queryKey: ["automations-builder-ideas", name] });
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <button type="button" className="btn" disabled={busy} onClick={() => void find()}>
        {busy ? "Queuing…" : "Find ideas"}
      </button>
      {note && <p className="builder-check">{note}</p>}
      {q.isLoading && <div className="muted">Reading the searches…</div>}
      {q.error && <div className="error">Could not read the searches.</div>}
      {q.data && <IdeaSearchList data={q.data} onOpen={setOpen} />}
    </div>
  );
}
