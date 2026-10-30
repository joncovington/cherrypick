import { Fragment, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { BacktestOverview, StudiesBoard, StudyGroup, StudyResult, StudyRow } from "@console/shared";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";
import { writePref } from "../../lib/prefs";
import { FILTER_KEY, ago, etMs, etNowMs, stamp } from "./QueueTab";

/**
 * The Backtest page's studies tab (2026-10-29, docs/studies-board-plan.md): what we tested and what
 * we found. One row per hypothesis-register item, read through `/api/backtest/studies` (the
 * orchestrator's `hypotheses-board`): its status, each result file's verdict, the decision taken,
 * and links to its plan section, its queue rows and its run.
 *
 * Every word is the orchestrator's: the register's status, the study's own verdict, the group. A
 * file with no verdict word says so; nothing is worked out here from figures.
 */

const REPO_BLOB = "https://github.com/joncovington/cherrypick/blob/main/";

const GROUP_TITLE: Record<StudyGroup, string> = {
  in_progress: "In progress",
  declared: "Declared, not started",
  finished: "Finished",
  observation: "Observations",
};

const STATUS_CHIP: Record<string, string> = {
  passed: "chip chip-ok",
  failed: "chip chip-missing",
  no_verdict: "chip",
  running: "chip chip-ok",
  declared: "chip muted",
  observation: "chip muted",
};

const COLS = 6;

/** The register's status in words: `no_verdict` -> "no verdict". */
export function statusWord(status: string): string {
  return status.replace(/_/g, " ");
}

/** The plan section on GitHub, from the register's `<file>#<heading>` pointer. */
export function planUrl(pointer: string | null): string | null {
  return pointer ? `${REPO_BLOB}${pointer}` : null;
}

/** What a result file covers: its window, else its span's years, else its name. */
export function resultLabel(r: StudyResult): string {
  if (r.window) return r.window;
  if (r.span) {
    const [a, b] = [r.span[0].slice(0, 4), r.span[1].slice(0, 4)];
    return a === b ? a : `${a}–${b}`;
  }
  return r.name;
}

/** One line for a row's results: "design fail · holdout fail". */
export function resultsLine(row: StudyRow): string {
  if (row.results.length === 0) return "—";
  return row.results
    .map((r) => `${resultLabel(r)} ${!r.present ? "missing" : (r.summary ?? "see plan")}`)
    .join(" · ");
}

/** "27 studies: 2 in progress, 2 declared, 20 finished (18 failed, 2 no verdict), 3 observations." */
export function headline(board: StudiesBoard): string {
  const count = (g: StudyGroup) => board.rows.filter((r) => r.group === g).length;
  const finished = board.rows.filter((r) => r.group === "finished");
  const by = ["passed", "failed", "no_verdict"]
    .map((s) => [s, finished.filter((r) => r.status === s).length] as const)
    .filter(([, n]) => n > 0)
    .map(([s, n]) => `${n} ${statusWord(s)}`);
  const parts = [
    `${count("in_progress")} in progress`,
    `${count("declared")} declared`,
    `${finished.length} finished${by.length ? ` (${by.join(", ")})` : ""}`,
    `${count("observation")} observation${count("observation") === 1 ? "" : "s"}`,
  ];
  return `${board.rows.length} studies: ${parts.join(", ")}.`;
}

/** The study context the overview holds: the windows, and why replays are provisional. */
export function contextLine(o: BacktestOverview | null): string | null {
  if (o === null) return null;
  const w = o.windows;
  const windows =
    w.design && w.holdout ? `Design window ${w.design[0]} to ${w.design[1]}; holdout ${w.holdout[0]} to ${w.holdout[1]}.` : "";
  const locked = o.validation.length > 0 && o.validation.every((v) => !v.unlocks_history);
  const why = locked ? " MEIC and flies replays are provisional: validation has not unlocked their history (see the validation tab)." : "";
  return windows + why || null;
}

function Toggle({ open, label, onClick }: { open: boolean; label: string; onClick: () => void }) {
  return (
    <button type="button" className="btn btn-quiet" aria-expanded={open} aria-label={`${open ? "Close" : "Open"} ${label}`} onClick={onClick}>
      {open ? "▾" : "▸"}
    </button>
  );
}

function Field({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

function QueueLink({ study }: { study: string }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="btn btn-quiet"
      onClick={() => {
        void writePref(FILTER_KEY, { status: "all", study });
        navigate("/backtest/queue");
      }}
    >
      its queue rows
    </button>
  );
}

export function StudyDetail({ row }: { row: StudyRow }) {
  const url = planUrl(row.pointer);
  return (
    <div className="queue-detail">
      <dl className="queue-fields">
        {row.legacy ? (
          <Field k="declared">before the register: the question, test and rivals are in the plan section</Field>
        ) : null}
        {row.rule ? <Field k="pass test">{row.rule}</Field> : null}
        {row.measure ? <Field k="measures">{row.measure}</Field> : null}
        {row.expected ? <Field k="expects">{row.expected}</Field> : null}
        {row.sample ? <Field k="sample">{row.sample}</Field> : null}
        {row.mechanism ? <Field k="why it might work">{row.mechanism}</Field> : null}
        {row.rivals.length > 0 ? (
          <Field k="rival explanations">
            <ul style={{ margin: 0, paddingLeft: "1.1em" }}>
              {row.rivals.map((r) => (
                <li key={r.explanation}>
                  {r.explanation} <span className="muted">— checked by: {r.check}</span>
                </li>
              ))}
            </ul>
          </Field>
        ) : null}
        {row.results.map((r) => (
          <Field key={r.name} k={resultLabel(r)}>
            {!r.present ? (
              <span className="pnl-neg">{r.name} is missing on this machine</span>
            ) : r.verdicts === null ? (
              <span className="muted">no verdict word in {r.name}; the plan section has the result</span>
            ) : (
              r.verdicts.map((v) => `${v.arm ?? "verdict"}: ${v.verdict}`).join(" · ")
            )}
            {r.runId ? (
              <>
                {" "}
                <Link to={`/backtest/runs?run=${r.runId}`}>run {r.runId}</Link>
              </>
            ) : null}
            {r.provisional ? <span className="chip chip-warn"> provisional</span> : null}
          </Field>
        ))}
        {row.tasks.length > 0 ? (
          <Field k="queue">
            {row.tasks.map((t) => `${t.step} ${t.status}`).join(" · ")} <QueueLink study={row.study} />
          </Field>
        ) : null}
        {url ? (
          <Field k="write-up">
            <a href={url} target="_blank" rel="noreferrer">
              the plan section
            </a>
          </Field>
        ) : null}
      </dl>
    </div>
  );
}

function StudyRows({ row, nowMs }: { row: StudyRow; nowMs: number }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr>
        <td>
          <Toggle open={open} label={row.study} onClick={() => setOpen(!open)} />
        </td>
        <td>
          <span className="muted">{row.id}</span> {row.title}
        </td>
        <td>
          <span className={STATUS_CHIP[row.status] ?? "chip"}>{statusWord(row.status)}</span>
          {row.queueActive ? <span className="chip chip-ok"> task running</span> : null}
        </td>
        <td>{resultsLine(row)}</td>
        <td>{row.outcome ?? <span className="muted">—</span>}</td>
        <td className="muted" title={stamp(row.lastActivity)}>
          {ago(etMs(row.lastActivity), nowMs)}
        </td>
      </tr>
      {open ? (
        <tr className="queue-detail-row">
          <td colSpan={COLS}>
            <StudyDetail row={row} />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

export function StudiesTable({ board, nowMs = etNowMs() }: { board: StudiesBoard; nowMs?: number }) {
  if (board.rows.length === 0) return <p className="muted">The register has no rows.</p>;
  const groups = (Object.keys(GROUP_TITLE) as StudyGroup[]).map((g) => [g, board.rows.filter((r) => r.group === g)] as const);
  return (
    <div className="table-scroll">
      <table className="data-table queue-table">
        <thead>
          <tr>
            <th aria-label="Open" />
            <th>Study</th>
            <th title="The register's status">Status</th>
            <th title="Each result file's verdict, as the study wrote it">Verdicts</th>
            <th title="The decision taken">Outcome</th>
            <th title="The last queue task or result file, ET">When</th>
          </tr>
        </thead>
        <tbody>
          {groups
            .filter(([, rows]) => rows.length > 0)
            .map(([g, rows]) => (
              <Fragment key={g}>
                <tr className="row-group">
                  <th colSpan={COLS} scope="rowgroup">
                    {GROUP_TITLE[g]} <span className="muted">· {rows.length}</span>
                  </th>
                </tr>
                {rows.map((r) => (
                  <StudyRows key={r.study} row={r} nowMs={nowMs} />
                ))}
              </Fragment>
            ))}
        </tbody>
      </table>
    </div>
  );
}

export function BacktestStudiesTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useBridged<StudiesBoard>(["backtest", "studies"], "/api/backtest/studies", 30_000);
  const overview = useBridged<BacktestOverview>(["backtest", "overview"], "/api/backtest/overview", 60_000);
  const context = contextLine(overview.data?.ok ? overview.data.data : null);
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "studies board read unavailable"}</p>;
  else {
    const b = data.data;
    body = (
      <>
        <p style={{ margin: "0 0 4px" }}>{headline(b)}</p>
        {context ? (
          <p className="muted" style={{ margin: "0 0 8px" }}>
            {context}
          </p>
        ) : null}
        {b.unregistered.length > 0 ? (
          <p className="pnl-neg" style={{ margin: "0 0 8px" }}>
            Ran with no register row: {b.unregistered.join(", ")}. Add each to <code>docs/hypotheses.json</code>.
          </p>
        ) : null}
        <StudiesTable board={b} />
      </>
    );
  }
  return (
    <Card title="Studies" collapseKey="backtest-studies" updatedAt={dataUpdatedAt} isError={isError}>
      {body}
      <p className="muted" style={{ marginTop: 8 }}>
        One row per item in the hypothesis register (<code>docs/hypotheses.json</code>). A verdict is the word the study wrote in its
        result file; the files stay on this machine. Open a row for its pass test, rivals, per-arm verdicts and links.
      </p>
    </Card>
  );
}
