import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { BridgedResult, ResearchProgress, ResearchQueue, ResearchTask, StudyResult, StudyRow } from "@console/shared";
import {
  VERDICT_STATUSES,
  indexStudies,
  resultLabel,
  splitTitle,
  statusWord,
  studyHref,
  useStudies,
  verdictChip,
  type StudyIndex,
} from "./studyIndex";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";
import { getPref, usePrefsVersion, writePref } from "../../lib/prefs";

/**
 * The Backtest page's queue tab (2026-10-24; laid out again 2026-10-27, docs/progress-queue-arms-plan.md
 * phase 2; by study 2026-11-02): the research tasks the supervisor's `research-queue` job runs one at a
 * time, and the runs started by hand beside them, read through `/api/backtest/queue` (the
 * orchestrator's `queue-list`).
 *
 * Two sections, so what is happening is never below what happened:
 *
 * - **Now**: running tasks with their meters, then queued ones in the order the runner takes them
 *   (ready first, oldest first), each with the orchestrator's own reason for waiting; and runs started
 *   by hand that are still going.
 * - **Studies**: one line per study with finished rows, the most recently finished first. A study is
 *   named by its register title, with its slug beside it; the line carries each result file's verdict
 *   and the decision taken, both read from the studies board (`hypotheses-board`), never worked out
 *   here. It opens to the study's rows. Cancelled rows are left out unless asked for.
 *
 * A row opens to the rest (arguments, timings, the result's fields, the log's tail).
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

const REGISTER_CHIP: Record<string, string> = {
  passed: "chip chip-ok",
  failed: "chip chip-missing",
  running: "chip chip-ok",
};

export const RECENT_DAYS = 30;

/** How often the tab re-reads the queue: every 2 s while a row or a by-hand job runs (its progress
 *  file changes at most once a second), 5 s while rows wait (the next can start within the
 *  research-queue job's minute), else 30 s. */
export function queuePollMs(data: BridgedResult<ResearchQueue> | undefined): number {
  const q = data?.ok ? data.data : null;
  if (q === null || q === undefined) return 30_000;
  if (q.tasks.some((t) => t.status === "running") || q.byHand.some((p) => p.state === "running")) return 2_000;
  return q.tasks.some((t) => t.status === "queued") ? 5_000 : 30_000;
}

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

/**
 * A result file's chip label: "design", "holdout" or a span's years. A free-text window
 * ("2018-02-19..2023-12-29; holdout ...") becomes its first and last year. A file with neither is named
 * only when the study has more than one, since "results.json" says nothing on its own.
 */
export function chipLabel(r: StudyResult, many: boolean): string {
  if (r.window && !/\d/.test(r.window)) return r.window;
  const years = (r.window ?? "").match(/\b(19|20)\d{2}\b/g);
  if (r.span || (years && years.length > 0)) {
    if (r.span) return resultLabel({ ...r, window: null });
    const [a, b] = [years![0], years![years!.length - 1]];
    return a === b ? a : `${a}–${b}`;
  }
  return many ? r.name.replace(/\.json$/, "") : "";
}

/** What a result chip says: its label and the study's own word, "missing" when the file is not here. */
export function verdictText(r: StudyResult, many: boolean): string {
  const word = !r.present ? "missing" : (r.summary ?? "no verdict word");
  return [chipLabel(r, many), word].filter((x) => x !== "").join(" ");
}

// --------------------------------------------------------------------------- the sections
const FINISHED: ReadonlyArray<ResearchTask["status"]> = ["done", "failed", "stopped", "cancelled"];

/** One study's finished work: its rows (cancelled ones only when asked for), newest first. */
export interface StudyLine {
  study: string;
  tasks: ResearchTask[];
  /** Its newest shown row: `tasks[0]`. */
  latest: ResearchTask;
  /** Its cancelled rows left out of `tasks`. */
  cancelled: number;
  /** When its newest shown row finished. */
  lastMs: number | null;
}

export interface QueuePlan {
  running: ResearchTask[];
  /** In the order the runner takes them: ready first, then waiting; oldest first within each. */
  queued: ResearchTask[];
  byHandLive: ResearchProgress[];
  /** Studies with finished rows, the most recently finished first. */
  studies: StudyLine[];
  /** Studies whose finished rows are all cancelled, left out unless cancelled rows are shown. */
  cancelledOnly: number;
  byHandRecent: ResearchProgress[];
}

/** When a finished row finished, for sorting: its finish, else its start, else when queued. */
function endedMs(t: ResearchTask): number | null {
  return etMs(t.finishedAt) ?? etMs(t.startedAt) ?? etMs(t.createdAt);
}

export function plan(q: ResearchQueue, nowEpochS = Date.now() / 1000, withCancelled = false): QueuePlan {
  const running = q.tasks.filter((t) => t.status === "running").sort((a, b) => a.id - b.id);
  const queued = q.tasks
    .filter((t) => t.status === "queued")
    .sort((a, b) => Number(b.waits === "ready") - Number(a.waits === "ready") || a.id - b.id);
  const groups = new Map<string, ResearchTask[]>();
  for (const t of q.tasks.filter((x) => FINISHED.includes(x.status))) groups.set(t.study, [...(groups.get(t.study) ?? []), t]);
  const studies: StudyLine[] = [];
  let cancelledOnly = 0;
  for (const [study, all] of groups) {
    const tasks = all
      .filter((t) => withCancelled || t.status !== "cancelled")
      .sort((a, b) => (endedMs(b) ?? 0) - (endedMs(a) ?? 0) || b.id - a.id);
    const latest = tasks[0];
    if (latest === undefined) {
      cancelledOnly += 1;
      continue;
    }
    studies.push({ study, tasks, latest, cancelled: all.length - tasks.length, lastMs: endedMs(latest) });
  }
  studies.sort((a, b) => (b.lastMs ?? 0) - (a.lastMs ?? 0) || a.study.localeCompare(b.study));
  const live = (p: ResearchProgress) => p.state === "running" || p.state === "stalled";
  const byHandLive = q.byHand.filter(live);
  const byHandRecent = q.byHand
    .filter((p) => !live(p) && (p.updatedAt ?? 0) >= nowEpochS - RECENT_DAYS * 86_400)
    .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
  return { running, queued, byHandLive, studies, cancelledOnly, byHandRecent };
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

export interface QueueFilter {
  status: StatusFilter;
  study: string;
  /** Show cancelled rows in the studies' lines. */
  cancelled: boolean;
}

export function keep(t: ResearchTask, status: StatusFilter, study: string): boolean {
  if (study !== "" && t.study !== study) return false;
  if (status === "active") return t.status === "running" || t.status === "queued";
  if (status === "failed") return t.status === "failed";
  if (status === "done") return t.status === "done";
  return true;
}

function useFilter(): [QueueFilter, (f: QueueFilter) => void] {
  usePrefsVersion();
  const raw = getPref(FILTER_KEY) as Partial<QueueFilter> | undefined;
  const value = { status: raw?.status ?? "all", study: raw?.study ?? "", cancelled: raw?.cancelled ?? false };
  return [value, (f) => void writePref(FILTER_KEY, f)];
}

// --------------------------------------------------------------------------- naming a study
/**
 * A study by what it tests: the register title's heading in bold and the rest beside it, its slug as a
 * small tag. `compact` keeps the heading only (the rest in the tooltip). A study the register lacks
 * is named by its slug, and says so.
 */
export function StudyName({ study, studies, compact = false }: { study: string; studies?: StudyIndex; compact?: boolean }) {
  const row = studies?.byStudy.get(study);
  if (!row) {
    // Without the board (still loading, or unreadable) the slug is all there is to say.
    return (
      <span className="queue-study">
        <strong>{study}</strong>
        {studies ? <span className="muted"> · not in the register</span> : null}
      </span>
    );
  }
  const [head, rest] = splitTitle(row.title);
  return (
    <span className="queue-study" title={row.title}>
      <strong>{head}</strong>
      {rest && !compact ? <span className="muted">: {rest}</span> : null} <span className="chip muted queue-slug">{study}</span>
    </span>
  );
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
  return (
    <Link
      to={studyHref(study)}
      className={row.status === "failed" ? "chip chip-missing" : row.status === "passed" ? "chip chip-ok" : "chip"}
      title={`${row.title}: the study's verdict (open it on the studies tab)`}
    >
      {statusWord(row.status)}
    </Link>
  );
}

/**
 * One queue row. Under Now it names its study; inside a study's line the study is already named, so
 * it shows its step alone.
 */
function TaskRows({ t, nowMs, studies, inStudy = false }: { t: ResearchTask; nowMs: number; studies?: StudyIndex; inStudy?: boolean }) {
  const [open, setOpen] = useState(false);
  const when = t.status === "running" ? etMs(t.startedAt) : t.status === "queued" ? etMs(t.createdAt) : endedMs(t);
  const wait = waitText(t);
  return (
    <Fragment>
      <tr className={inStudy ? "queue-step-row" : undefined}>
        <td>
          <Toggle open={open} label={t.name} onClick={() => setOpen(!open)} />
        </td>
        <td title={`#${t.id} ${t.name}`}>
          {inStudy ? (
            <span className="queue-step">{t.step}</span>
          ) : (
            <>
              <StudyName study={t.study} studies={studies} compact /> <span className="muted">·</span> {t.step}
            </>
          )}
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

/** Each result file's verdict as the study wrote it; a mixed one names its arms in the tooltip. */
export function Verdicts({ row }: { row: StudyRow }) {
  const many = row.results.length > 1;
  return (
    <span className="queue-verdicts">
      {row.results.map((r) => (
        <span
          key={r.name}
          className={verdictChip(r.present ? r.summary : null)}
          title={`${r.name}${r.verdicts ? `: ${r.verdicts.map((v) => `${v.arm ?? "verdict"} ${v.verdict}`).join(" · ")}` : ""}`}
        >
          {verdictText(r, many)}
        </span>
      ))}
      {row.results.some((r) => r.provisional) ? <span className="chip chip-warn">provisional</span> : null}
      {row.results.some((r) => r.version?.stale) ? <span className="chip chip-warn">stale</span> : null}
    </span>
  );
}

/** A study's per-arm verdicts, the decision taken and the way to the rest of it. */
function StudyLineDetail({ line, row }: { line: StudyLine; row?: StudyRow }) {
  return (
    <div className="queue-detail">
      <dl className="queue-fields">
        {row ? (
          <>
            {row.outcome ? <Field k="decision">{row.outcome}</Field> : null}
            {row.results.map((r) => (
              <Field key={r.name} k={resultLabel(r)}>
                {!r.present ? (
                  <span className="pnl-neg">{r.name} is missing on this machine</span>
                ) : r.verdicts === null ? (
                  <span className="muted">no verdict word in {r.name}</span>
                ) : (
                  r.verdicts.map((v) => (
                    <span key={`${v.arm ?? ""}`} className={verdictChip(v.verdict)}>
                      {v.arm ? `${v.arm} ${v.verdict}` : v.verdict}
                    </span>
                  ))
                )}
              </Field>
            ))}
            <Field k="the study">
              <Link to={studyHref(line.study)}>open it on the studies tab</Link>{" "}
              <span className="muted">— what it measures, its pass test, rivals and write-up</span>
            </Field>
          </>
        ) : (
          <Field k="the study">
            <span className="muted">{line.study} has no row in the hypothesis register, so there is no verdict to show</span>
          </Field>
        )}
        {line.cancelled > 0 ? (
          <Field k="cancelled">
            <span className="muted">
              {line.cancelled} cancelled row{line.cancelled === 1 ? "" : "s"} not shown
            </span>
          </Field>
        ) : null}
      </dl>
    </div>
  );
}

function StudyLineRows({ line, nowMs, studies }: { line: StudyLine; nowMs: number; studies?: StudyIndex }) {
  const [open, setOpen] = useState(false);
  const row = studies?.byStudy.get(line.study);
  const latest = line.latest;
  return (
    <Fragment>
      <tr className="queue-study-row">
        <td>
          <Toggle open={open} label={line.study} onClick={() => setOpen(!open)} />
        </td>
        {/* Full width: the name, then what it found, then its steps. As columns the verdicts were squeezed off the card. */}
        <td colSpan={COLS - 2}>
          <StudyName study={line.study} studies={studies} />{" "}
          {row ? <span className={REGISTER_CHIP[row.status] ?? "chip muted"}>{statusWord(row.status)}</span> : null}
          <div className="queue-found">
            {row && row.results.length > 0 ? (
              <Verdicts row={row} />
            ) : latest.result ? (
              <span className={latest.result.outputExists === false ? "pnl-neg" : "muted"}>{resultLine(latest)}</span>
            ) : (
              <span className="muted">no result yet</span>
            )}
            {row?.outcome ? <span className="queue-outcome">{row.outcome}</span> : null}
          </div>
          <div className="muted queue-steps">
            {line.tasks.length} step{line.tasks.length === 1 ? "" : "s"}: {line.tasks.map((t) => `${t.step} ${t.status}`).join(" · ")}
          </div>
        </td>
        <td className="muted" title={stamp(latest.finishedAt ?? latest.startedAt ?? latest.createdAt)}>
          {ago(line.lastMs, nowMs)}
        </td>
      </tr>
      {open ? (
        <>
          <tr className="queue-detail-row">
            <td colSpan={COLS}>
              <StudyLineDetail line={line} row={row} />
            </td>
          </tr>
          {line.tasks.map((t) => (
            <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} inStudy />
          ))}
        </>
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

function SubHead({ text }: { text: string }) {
  return (
    <tr className="row-subgroup">
      <td colSpan={COLS} className="muted">
        {text}
      </td>
    </tr>
  );
}

function Section({ title, count, note, children }: { title: string; count: number; note?: string; children: ReactNode }) {
  return (
    <>
      <tr className="row-group">
        <th colSpan={COLS} scope="rowgroup">
          {title} <span className="muted">· {count}</span>
          {note ? <span className="muted queue-section-note"> — {note}</span> : null}
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
  cancelled = false,
  studies,
}: {
  q: ResearchQueue;
  nowMs?: number;
  status?: StatusFilter;
  study?: string;
  cancelled?: boolean;
  studies?: StudyIndex;
}) {
  const shown = { ...q, tasks: q.tasks.filter((t) => keep(t, status, study)) };
  const p = plan(shown, undefined, cancelled);
  // A run started by hand has no queue status: its live rows pass "all" and "active", its finished ones only "all".
  const hand = study === "" && (status === "all" || status === "active");
  const handFinished = study === "" && status === "all";
  const nowCount = p.running.length + p.queued.length + (hand ? p.byHandLive.length : 0);
  // Ready and waiting are the orchestrator's words (`waits`); the page only splits on them.
  const ready = p.queued.filter((t) => t.waits === "ready");
  const waiting = p.queued.filter((t) => t.waits !== "ready");
  const finishedCount = p.studies.length + (handFinished ? p.byHandRecent.length : 0);
  if (nowCount + (status === "active" ? 0 : finishedCount) === 0) {
    if (q.tasks.length === 0 && q.byHand.length === 0) return <p className="muted">The queue is empty.</p>;
    return <p className="muted">{p.cancelledOnly > 0 ? "Only cancelled rows match: show cancelled to see them." : "Nothing matches the filter."}</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table queue-table">
        <thead>
          <tr>
            <th aria-label="Open" />
            <th title="The study, by its register title, and the step">Study · step</th>
            <th>Kind</th>
            <th>State</th>
            <th title="Each result file's verdict as the study wrote it, and the decision taken; for a row, what it produced">Result</th>
            <th title="Started (running), queued (waiting) or finished, ET">When</th>
          </tr>
        </thead>
        <tbody>
          <Section title="Now" count={nowCount} note="one task at a time; when it ends, the oldest ready row starts">
            {p.running.map((t) => (
              <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
            ))}
            {hand ? p.byHandLive.map((x) => <ByHandRows key={x.file} p={x} />) : null}
            {ready.length > 0 ? <SubHead text={`ready · ${ready.length} · oldest first: the top one starts next`} /> : null}
            {ready.map((t) => (
              <TaskRows key={t.id} t={t} nowMs={nowMs} studies={studies} />
            ))}
            {waiting.length > 0 ? <SubHead text={`waiting · ${waiting.length} · each starts once its reason clears and it is the oldest ready`} /> : null}
            {waiting.map((t) => (
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
            <Section
              title="Studies"
              count={p.studies.length}
              note={`most recently finished first${!cancelled && p.cancelledOnly > 0 ? `; ${p.cancelledOnly} with only cancelled rows not shown` : ""}`}
            >
              {p.studies.map((line) => (
                <StudyLineRows key={line.study} line={line} nowMs={nowMs} studies={studies} />
              ))}
            </Section>
          )}
          {handFinished && p.byHandRecent.length > 0 ? (
            <Section title={`Started by hand (${RECENT_DAYS} days)`} count={p.byHandRecent.length}>
              {p.byHandRecent.map((x) => (
                <ByHandRows key={x.file} p={x} />
              ))}
            </Section>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function Filters({ q, studies, value, onChange }: { q: ResearchQueue; studies?: StudyIndex; value: QueueFilter; onChange: (v: QueueFilter) => void }) {
  const slugs = [...new Set(q.tasks.map((t) => t.study))].sort();
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
      <button
        type="button"
        className={value.cancelled ? "chip chip-ok" : "chip"}
        aria-pressed={value.cancelled}
        onClick={() => onChange({ ...value, cancelled: !value.cancelled })}
      >
        show cancelled
      </button>
      <select aria-label="Study" value={value.study} onChange={(e) => onChange({ ...value, study: e.target.value })}>
        <option value="">every study</option>
        {slugs.map((s) => {
          const row = studies?.byStudy.get(s);
          return (
            <option key={s} value={s}>
              {row ? `${splitTitle(row.title)[0]} (${s})` : s}
            </option>
          );
        })}
      </select>
    </div>
  );
}

export function BacktestQueueTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useBridged<ResearchQueue>(["backtest", "queue"], "/api/backtest/queue", queuePollMs);
  const [filter, setFilter] = useFilter();
  const studies = useStudies();
  // No index until the board has loaded: an empty one would call every study unregistered.
  const index = studies.data?.ok && studies.data.data ? indexStudies(studies.data.data) : undefined;
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "research queue read unavailable"}</p>;
  else {
    const q = data.data;
    const nowMs = etNowMs();
    body = (
      <>
        <p style={{ margin: "0 0 4px" }}>{headline(plan(q))}</p>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          ThetaData session {q.sessionHeld ? "held by another process" : "free"}
          {q.blackout ? ` · no ThetaData task runs ${q.blackout[0]}–${q.blackout[1]} ET on trading days (the nightly backtest-fetch)` : ""}
        </p>
        <Filters q={q} studies={index} value={filter} onChange={setFilter} />
        <QueueTable q={q} nowMs={nowMs} status={filter.status} study={filter.study} cancelled={filter.cancelled} studies={index} />
      </>
    );
  }
  return (
    <Card title="Research queue" collapseKey="backtest-queue" updatedAt={dataUpdatedAt} isError={isError}>
      {body}
      <p className="muted" style={{ marginTop: 8 }}>
        The supervisor runs one task at a time: on each pass it takes the oldest queued row that is ready, by the order rows were
        added. A row waits while its <code>--after</code> step is not done, before its <code>--not-before</code>, or, for a ThetaData
        fetch, while the session is held or inside the nightly window (it resumes after). Kinds are allow-listed: a fetch
        (fetch-chains, fetch-flow, fetch-oi), a validation, or a script checked into <code>scripts/</code>. Add one with{" "}
        <code>run.py queue-add --study &lt;study&gt; [--step &lt;step&gt;] --task &lt;kind&gt; --task-args &apos;&#123;…&#125;&apos;</code>;
        cancel a queued one with <code>run.py queue-cancel --task-id N</code>. Verdicts and decisions are the studies board&apos;s. A
        run started by hand shows here through its progress file. Read-only here.
      </p>
    </Card>
  );
}
