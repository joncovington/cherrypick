import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { ResearchProgress, ResearchQueue, ResearchTask } from "@console/shared";
import { VERDICT_STATUSES, indexStudies, studyHref, useStudies, type StudyIndex } from "./studyIndex";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";
import { getPref, usePrefsVersion, writePref } from "../../lib/prefs";

/**
 * The Backtest page's queue tab (2026-10-24; laid out again 2026-10-27, docs/progress-queue-arms-plan.md
 * phase 2): the research tasks the supervisor's `research-queue` job runs one at a time, and the runs
 * started by hand beside them, read through `/api/backtest/queue` (the orchestrator's `queue-list`).
 *
 * Three sections, so what is happening is never below what happened:
 *
 * - **Now**: running tasks with their meters, then queued ones in the order the runner takes them
 *   (ready first, oldest first), each with the orchestrator's own reason for waiting; and runs started
 *   by hand that are still going.
 * - **Recent**: finished in the last 30 days, newest first.
 * - **History**: older, collapsed, grouped by study.
 *
 * Five columns; a row opens to the rest (arguments, timings, the result's fields, the log's tail).
 * Read-only: rows are added with `run.py queue-add` and cancelled with `run.py queue-cancel`.
 */

const STATUS_CHIP: Record<ResearchTask["status"], string> = {
  queued: "chip",
  running: "chip chip-ok",
  done: "chip chip-ok",
  failed: "chip chip-warn",
  stopped: "chip",
  cancelled: "chip muted",
};

const PROGRESS_CHIP: Record<ResearchProgress["state"], string> = {
  running: "chip chip-ok",
  done: "chip chip-ok",
  failed: "chip chip-warn",
  stalled: "chip chip-warn",
  orphaned: "chip chip-warn",
};

export const RECENT_DAYS = 30;
const DAY_MS = 86_400_000;

/** "2026-10-26T18:30:05" -> "Oct 26 18:30" (ET, as the orchestrator records it). */
export function stamp(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  const [d, t] = iso.split("T");
  const day = new Date(`${d}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return t ? `${day} ${t.slice(0, 5)}` : day;
}

/** An ET wall-clock stamp as milliseconds on the same naive scale as `etNowMs`. */
export function etMs(iso: string | null): number | null {
  if (iso === null || iso === "") return null;
  const t = Date.parse(`${iso.length === 10 ? `${iso}T00:00:00` : iso}Z`);
  return Number.isNaN(t) ? null : t;
}

/** Now, as ET wall clock on a naive millisecond scale, so it compares with the orchestrator's stamps. */
export function etNowMs(now = Date.now()): number {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
      .formatToParts(new Date(now))
      .map((p) => [p.type, p.value]),
  );
  return Date.parse(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`);
}

/** "just now" / "12 min ago" / "3 h ago" / "4 d ago". */
export function ago(ms: number | null, nowMs: number): string {
  if (ms === null) return "—";
  const mins = Math.max(0, Math.round((nowMs - ms) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

/** The task's arguments in one line: a file's name rather than its full path, other values as given. */
export function argsLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      if (typeof v === "string" && /[\\/]/.test(v)) return `${k} ${v.split(/[\\/]/).pop() ?? v}`;
      if (Array.isArray(v)) return `${k} ${v.join(" ")}`;
      return `${k} ${String(v)}`;
    })
    .join(" · ");
}

function fileName(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

/** What a finished row produced: a fetch's counts, and the file it points to -- a script's declared
 *  conclusion (marked when missing), else the fetch's input -- then its log, each by name. */
export function resultLine(t: ResearchTask): string {
  const r = t.result;
  if (r === null) return t.output ? `will write ${fileName(t.output)}` : "—";
  const parts: string[] = [];
  if (r.report) {
    const keys = ["complete", "empty", "refused", "rows"] as const;
    const counts = keys.filter((k) => typeof r.report?.[k] === "number").map((k) => `${String(r.report?.[k])} ${k}`);
    if (counts.length > 0) parts.push(counts.join(", "));
  }
  if (r.output) parts.push(r.outputExists === false ? `${fileName(r.output)} (missing)` : fileName(r.output));
  else if (r.input) parts.push(`from ${fileName(r.input)}`);
  parts.push(fileName(r.log));
  return parts.join(" · ");
}

/** The job's own one-line reading, with "stalled" or "orphaned" said plainly when the reader concluded
 *  it (the orchestrator puts both in `line`; a finished job adds its state). */
export function progressLine(p: ResearchProgress): string {
  const pct = p.fraction !== null ? `${(p.fraction * 100).toFixed(1)}%` : null;
  const parts = [pct, p.line || null].filter((x): x is string => x !== null);
  if (p.state === "done" || p.state === "failed") parts.push(p.state);
  if (p.note) parts.push(p.note);
  return parts.join(" · ");
}

// --------------------------------------------------------------------------- the sections
const FINISHED: ReadonlyArray<ResearchTask["status"]> = ["done", "failed", "stopped", "cancelled"];

export interface QueuePlan {
  running: ResearchTask[];
  /** In the order the runner takes them: ready first, then waiting; oldest first within each. */
  queued: ResearchTask[];
  byHandLive: ResearchProgress[];
  recent: ResearchTask[];
  byHandRecent: ResearchProgress[];
  /** Older than `RECENT_DAYS`, by study in name order, newest first within one. */
  history: Array<[string, ResearchTask[]]>;
}

/** When a finished row finished, for sorting and the 30-day cut: its finish, else its start, else when queued. */
function endedMs(t: ResearchTask): number | null {
  return etMs(t.finishedAt) ?? etMs(t.startedAt) ?? etMs(t.createdAt);
}

export function plan(q: ResearchQueue, nowMs: number, nowEpochS = Date.now() / 1000): QueuePlan {
  const cut = nowMs - RECENT_DAYS * DAY_MS;
  const running = q.tasks.filter((t) => t.status === "running").sort((a, b) => a.id - b.id);
  const queued = q.tasks
    .filter((t) => t.status === "queued")
    .sort((a, b) => Number(b.waits === "ready") - Number(a.waits === "ready") || a.id - b.id);
  const finished = q.tasks.filter((t) => FINISHED.includes(t.status));
  const recent = finished
    .filter((t) => (endedMs(t) ?? 0) >= cut)
    .sort((a, b) => (endedMs(b) ?? 0) - (endedMs(a) ?? 0) || b.id - a.id);
  const old = finished.filter((t) => (endedMs(t) ?? 0) < cut);
  const groups = new Map<string, ResearchTask[]>();
  for (const t of old) groups.set(t.study, [...(groups.get(t.study) ?? []), t]);
  const history = [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([s, ts]) => [s, [...ts].sort((x, y) => y.id - x.id)] as [string, ResearchTask[]]);
  const live = (p: ResearchProgress) => p.state === "running" || p.state === "stalled";
  const byHandLive = q.byHand.filter(live);
  const byHandRecent = q.byHand
    .filter((p) => !live(p) && (p.updatedAt ?? 0) >= nowEpochS - RECENT_DAYS * 86_400)
    .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
  return { running, queued, byHandLive, recent, byHandRecent, history };
}

/** "1 running · 2 queued · next: item19/fetch-resume (outside the 18:30–20:30 window)". */
export function headline(p: QueuePlan): string {
  const parts = [`${p.running.length} running`, `${p.queued.length} queued`];
  if (p.byHandLive.length > 0) parts.push(`${p.byHandLive.length} by hand`);
  const next = p.queued[0];
  if (next) parts.push(`next: ${next.name}${next.waits && next.waits !== "ready" ? ` (${next.waits})` : ""}`);
  return parts.join(" · ");
}

// --------------------------------------------------------------------------- filters
export type StatusFilter = "all" | "active" | "failed" | "done";
export const FILTER_KEY = "backtest-queue-filter";

export function keep(t: ResearchTask, status: StatusFilter, study: string): boolean {
  if (study !== "" && t.study !== study) return false;
  if (status === "active") return t.status === "running" || t.status === "queued";
  if (status === "failed") return t.status === "failed";
  if (status === "done") return t.status === "done";
  return true;
}

function useFilter(): [{ status: StatusFilter; study: string }, (f: { status: StatusFilter; study: string }) => void] {
  usePrefsVersion();
  const raw = getPref(FILTER_KEY) as { status?: StatusFilter; study?: string } | undefined;
  const value = { status: raw?.status ?? "all", study: raw?.study ?? "" };
  return [value, (f) => void writePref(FILTER_KEY, f)];
}

// --------------------------------------------------------------------------- rows
function Meter({ p }: { p: ResearchProgress }) {
  const frac = p.fraction ?? 0;
  const detail = p.phases.map((ph) => `${ph.name}: ${ph.done}${ph.total !== null ? ` of ${ph.total}` : ""}`).join("; ");
  return (
    <div className="queue-meter" title={detail}>
      {p.fraction !== null ? (
        <div className="queue-meter-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}>
          <div className="queue-meter-fill" style={{ width: `${(frac * 100).toFixed(1)}%` }} />
        </div>
      ) : null}
      <span className="muted">{progressLine(p)}</span>
    </div>
  );
}

function waitText(t: ResearchTask): string | null {
  if (t.status === "queued") return t.waits === "ready" ? "ready: starts at the next tick" : t.waits;
  return t.note;
}

const COLS = 6;

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

function LogTail({ id }: { id: number }) {
  const { data, isLoading } = useBridged<{ lines: string[] }>(["backtest", "queue-log", String(id)], `/api/backtest/queue/${id}/log`, 30_000);
  const lines = data?.data?.lines ?? [];
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "60%" }} />;
  if (lines.length === 0) return <p className="muted">No log yet.</p>;
  return <pre className="queue-log">{lines.join("\n")}</pre>;
}

export function TaskDetail({ t, withLog = true }: { t: ResearchTask; withLog?: boolean }) {
  const r = t.result;
  return (
    <div className="queue-detail">
      <dl className="queue-fields">
        <Field k="arguments">
          <code>{JSON.stringify(t.args)}</code>
        </Field>
        {t.needs ? <Field k="needs">{t.needs}</Field> : null}
        {t.notBefore ? <Field k="not before">{stamp(t.notBefore)} ET</Field> : null}
        {t.stopBy ? <Field k="stop by">{stamp(t.stopBy)} ET</Field> : null}
        <Field k="queued / started / finished">
          {stamp(t.createdAt)} · {stamp(t.startedAt)} · {stamp(t.finishedAt)} ET
        </Field>
        <Field k="runs / exit">
          {t.runs} · <span className={t.exitCode !== null && t.exitCode !== 0 ? "pnl-neg" : undefined}>{t.exitCode ?? "—"}</span>
        </Field>
        {t.output ? <Field k="declares">{t.output}</Field> : null}
        {r?.report
          ? Object.entries(r.report).map(([k, v]) => (
              <Field key={k} k={k}>
                {String(v)}
              </Field>
            ))
          : null}
        {r?.input ? <Field k="input">{r.input}</Field> : null}
        {r?.output ? (
          <Field k="output">
            {r.output}
            {r.outputExists === false ? <span className="pnl-neg"> (missing)</span> : null}
          </Field>
        ) : null}
        {r?.log ? <Field k="log">{r.log}</Field> : null}
        {t.progress ? (
          <Field k="progress">
            {t.progress.phases.map((ph) => `${ph.name} ${ph.done}${ph.total !== null ? `/${ph.total}` : ""}${ph.current ? ` (${ph.current})` : ""}`).join(" · ") ||
              "—"}
          </Field>
        ) : null}
      </dl>
      {withLog ? <LogTail id={t.id} /> : null}
    </div>
  );
}

/** A study's verdict beside its task, linking to the board: a row can be done while its study failed. */
export function StudyChip({ study, studies }: { study: string; studies?: StudyIndex }) {
  const row = studies?.byStudy.get(study);
  if (!row || !VERDICT_STATUSES.includes(row.status)) return null;
  const word = row.status.replace(/_/g, " ");
  return (
    <Link
      to={studyHref(study)}
      className={row.status === "failed" ? "chip chip-missing" : row.status === "passed" ? "chip chip-ok" : "chip"}
      title={`${row.title}: the study's verdict (open it on the studies tab)`}
    >
      {word}
    </Link>
  );
}

function TaskRows({ t, nowMs, studies }: { t: ResearchTask; nowMs: number; studies?: StudyIndex }) {
  const [open, setOpen] = useState(false);
  const when = t.status === "running" ? etMs(t.startedAt) : t.status === "queued" ? etMs(t.createdAt) : endedMs(t);
  const wait = waitText(t);
  return (
    <Fragment>
      <tr>
        <td>
          <Toggle open={open} label={t.name} onClick={() => setOpen(!open)} />
        </td>
        <td title={`#${t.id}`}>
          {t.name} <StudyChip study={t.study} studies={studies} />
          {t.needs.includes("thetadata") ? (
            <span className="chip" title="Needs the vendor account's one ThetaData session">
              ThetaData
            </span>
          ) : null}
        </td>
        <td>
          <span className="chip muted">{t.kind}</span>
        </td>
        <td>
          <span className={STATUS_CHIP[t.status]}>{t.status}</span>
          {wait ? <span className="muted"> {wait}</span> : null}
          {t.progress && (t.status === "running" || t.status === "queued") ? <Meter p={t.progress} /> : null}
        </td>
        <td className={t.result?.outputExists === false ? "pnl-neg" : undefined}>{resultLine(t)}</td>
        <td className="muted" title={stamp(t.finishedAt ?? t.startedAt ?? t.createdAt)}>
          {ago(when, nowMs)}
        </td>
      </tr>
      {open ? (
        <tr className="queue-detail-row">
          <td colSpan={COLS}>
            <TaskDetail t={t} />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

function ByHandRows({ p }: { p: ResearchProgress }) {
  const [open, setOpen] = useState(false);
  const nowS = Date.now() / 1000;
  const when = p.updatedAt !== null ? `${Math.max(0, Math.round((nowS - p.updatedAt) / 60))} min ago` : "—";
  return (
    <Fragment>
      <tr>
        <td>
          <Toggle open={open} label={p.job} onClick={() => setOpen(!open)} />
        </td>
        <td title={p.file}>{p.job}</td>
        <td>
          <span className="chip muted" title="Started without the queue: found through its progress file">
            by hand
          </span>
        </td>
        <td>
          <span className={PROGRESS_CHIP[p.state]}>{p.state}</span>
          <Meter p={p} />
        </td>
        <td className="muted">{p.note ?? "—"}</td>
        <td className="muted">{when}</td>
      </tr>
      {open ? (
        <tr className="queue-detail-row">
          <td colSpan={COLS}>
            <dl className="queue-fields">
              <Field k="file">{p.file}</Field>
              {p.phases.map((ph) => (
                <Field key={ph.name} k={ph.name}>
                  {ph.done}
                  {ph.total !== null ? ` of ${ph.total}` : ""} {ph.unit}
                  {ph.current ? ` · on ${ph.current}` : ""}
                </Field>
              ))}
            </dl>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <>
      <tr className="row-group">
        <th colSpan={COLS} scope="rowgroup">
          {title} <span className="muted">· {count}</span>
        </th>
      </tr>
      {children}
    </>
  );
}

export function QueueTable({
  q,
  nowMs = etNowMs(),
  status = "all",
  study = "",
  studies,
}: {
  q: ResearchQueue;
  nowMs?: number;
  status?: StatusFilter;
  study?: string;
  studies?: StudyIndex;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const shown = { ...q, tasks: q.tasks.filter((t) => keep(t, status, study)) };
  const p = plan(shown, nowMs);
  // A run started by hand has no queue status: its live rows pass "all" and "active", its finished ones only "all".
  const hand = study === "" && (status === "all" || status === "active");
  const handFinished = study === "" && status === "all";
  const nowCount = p.running.length + p.queued.length + (hand ? p.byHandLive.length : 0);
  const recentCount = p.recent.length + (handFinished ? p.byHandRecent.length : 0);
  const historyCount = p.history.reduce((n, [, ts]) => n + ts.length, 0);
  if (nowCount + recentCount + historyCount === 0) {
    return <p className="muted">{q.tasks.length === 0 && q.byHand.length === 0 ? "The queue is empty." : "Nothing matches the filter."}</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table queue-table">
        <thead>
          <tr>
            <th aria-label="Open" />
            <th title="study/step: a study is the task's category">Task</th>
            <th>Kind</th>
            <th>State</th>
            <th title="What a finished row produced: its counts and the file it points to, then its log">Result</th>
            <th title="Started (running), queued (waiting) or finished, ET">When</th>
          </tr>
        </thead>
        <tbody>
          <Section title="Now" count={nowCount}>
            {p.running.map((t) => (
              <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
            ))}
            {hand ? p.byHandLive.map((x) => <ByHandRows key={x.file} p={x} />) : null}
            {p.queued.map((t) => (
              <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
            ))}
            {nowCount === 0 ? (
              <tr>
                <td colSpan={COLS} className="muted">
                  Nothing running or queued.
                </td>
              </tr>
            ) : null}
          </Section>
          {status === "active" ? null : (
            <Section title={`Recent (${RECENT_DAYS} days)`} count={recentCount}>
              {p.recent.map((t) => (
                <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
              ))}
              {handFinished ? p.byHandRecent.map((x) => <ByHandRows key={x.file} p={x} />) : null}
            </Section>
          )}
          {historyCount > 0 ? (
            <>
              <tr className="row-group">
                <th colSpan={COLS} scope="rowgroup">
                  <Toggle open={historyOpen} label="the history" onClick={() => setHistoryOpen(!historyOpen)} /> History{" "}
                  <span className="muted">
                    · {historyCount} older than {RECENT_DAYS} days, by study
                  </span>
                </th>
              </tr>
              {historyOpen
                ? p.history.map(([s, ts]) => (
                    <Fragment key={`h-${s}`}>
                      <tr className="row-subgroup">
                        <td colSpan={COLS} className="muted">
                          {s} · {ts.length} task{ts.length === 1 ? "" : "s"}
                        </td>
                      </tr>
                      {ts.map((t) => (
                        <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
                      ))}
                    </Fragment>
                  ))
                : null}
            </>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function Filters({ q, value, onChange }: { q: ResearchQueue; value: { status: StatusFilter; study: string }; onChange: (v: { status: StatusFilter; study: string }) => void }) {
  const studies = [...new Set(q.tasks.map((t) => t.study))].sort();
  const options: Array<[StatusFilter, string]> = [
    ["all", "all"],
    ["active", "running + queued"],
    ["failed", "failed"],
    ["done", "done"],
  ];
  return (
    <div className="history-controls" role="group" aria-label="Filter the queue">
      {options.map(([v, label]) => (
        <button key={v} type="button" className={value.status === v ? "chip chip-ok" : "chip"} aria-pressed={value.status === v} onClick={() => onChange({ ...value, status: v })}>
          {label}
        </button>
      ))}
      <select aria-label="Study" value={value.study} onChange={(e) => onChange({ ...value, study: e.target.value })}>
        <option value="">every study</option>
        {studies.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </div>
  );
}

export function BacktestQueueTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useBridged<ResearchQueue>(["backtest", "queue"], "/api/backtest/queue", 30_000);
  const [filter, setFilter] = useFilter();
  const studies = useStudies();
  const index = indexStudies(studies.data?.ok ? studies.data.data : null);
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "research queue read unavailable"}</p>;
  else {
    const q = data.data;
    const nowMs = etNowMs();
    body = (
      <>
        <p style={{ margin: "0 0 4px" }}>{headline(plan(q, nowMs))}</p>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          ThetaData session {q.sessionHeld ? "held by another process" : "free"}
          {q.blackout ? ` · no ThetaData task runs ${q.blackout[0]}–${q.blackout[1]} ET on trading days (the nightly backtest-fetch)` : ""}
        </p>
        <Filters q={q} value={filter} onChange={setFilter} />
        <QueueTable q={q} nowMs={nowMs} status={filter.status} study={filter.study} studies={index} />
      </>
    );
  }
  return (
    <Card title="Research queue" collapseKey="backtest-queue" updatedAt={dataUpdatedAt} isError={isError}>
      {body}
      <p className="muted" style={{ marginTop: 8 }}>
        The supervisor runs one task at a time, oldest ready first: an allow-listed fetch (fetch-chains, fetch-flow), a
        validation, or a script checked into <code>scripts/</code>. A fetch stopped by the nightly window resumes after it. Add
        one with <code>run.py queue-add --study &lt;study&gt; [--step &lt;step&gt;] --task &lt;kind&gt; --task-args &apos;&#123;…&#125;&apos;</code>;
        cancel a queued one with <code>run.py queue-cancel --task-id N</code>. A run started by hand shows here through its
        progress file. Read-only here.
      </p>
    </Card>
  );
}
