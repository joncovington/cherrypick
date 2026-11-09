import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { BridgedResult, ResearchProgress, ResearchQueue, ResearchTask } from "@console/shared";
import { VERDICT_STATUSES, indexStudies, splitTitle, statusWord, studyHref, useStudies, verdictChip, type StudyIndex } from "./studyIndex";
import { HelpSection } from "../../components/CardHelp";
import { Card } from "../../components/DataTable";
import { ListViewContext, ViewSwitch, useListView, useViewChoice } from "../../components/ViewSwitch";
import { useBridged } from "../../lib/bridged";
import { BtRows, type BtRow } from "./BtRows";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "./CardLightbox";

/**
 * The Backtest page's queue tab (2026-10-24; laid out again 2026-10-27, docs/history/progress-queue-arms-plan.md (tag archive/docs-2026-11)
 * phase 2; by study 2026-11-02): the research tasks the supervisor's `research-queue` job runs one at a
 * time, and the runs started by hand beside them, read through `/api/backtest/queue` (the
 * orchestrator's `queue-list`).
 *
 * What is happening, not what was found (2026-11-03): **Now** holds running tasks with their meters,
 * then queued ones in the order the runner takes them (ready first, oldest first), each with the
 * orchestrator's own reason for waiting, and runs started by hand that are still going; below it, the
 * runs started by hand in the last month. A study's finished rows live on its card on the studies
 * tab, beside its verdict, so this tab no longer lists them by study.
 *
 * As cards (2026-11-03): the running task large with its phase meters, runs by hand beside it, then
 * up next (numbered, each with the orchestrator's own reason for waiting), the last week's failures
 * and the last month's runs by hand. A card opens a lightbox (`?task=`): overview, arguments,
 * result and the log's tail.
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

// --------------------------------------------------------------------------- the sections
export interface QueuePlan {
  running: ResearchTask[];
  /** In the order the runner takes them: ready first, then waiting; oldest first within each. */
  queued: ResearchTask[];
  byHandLive: ResearchProgress[];
  /** Rows that failed or were stopped in the last ATTENTION_DAYS, newest first: the queue's own trouble. */
  attention: ResearchTask[];
  byHandRecent: ResearchProgress[];
}

/** How far back a failed or stopped row stays on this tab. Older ones are on their study's card. */
export const ATTENTION_DAYS = 7;

/** When a finished row finished, for sorting: its finish, else its start, else when queued. */
function endedMs(t: ResearchTask): number | null {
  return etMs(t.finishedAt) ?? etMs(t.startedAt) ?? etMs(t.createdAt);
}

export function plan(q: ResearchQueue, nowEpochS = Date.now() / 1000, nowMs = etNowMs(nowEpochS * 1000)): QueuePlan {
  const running = q.tasks.filter((t) => t.status === "running").sort((a, b) => a.id - b.id);
  const queued = q.tasks
    .filter((t) => t.status === "queued")
    .sort((a, b) => Number(b.waits === "ready") - Number(a.waits === "ready") || a.id - b.id);
  const attention = q.tasks
    .filter((t) => (t.status === "failed" || t.status === "stopped") && (endedMs(t) ?? 0) >= nowMs - ATTENTION_DAYS * 86_400_000)
    .sort((a, b) => (endedMs(b) ?? 0) - (endedMs(a) ?? 0) || b.id - a.id);
  const live = (p: ResearchProgress) => p.state === "running" || p.state === "stalled";
  const byHandLive = q.byHand.filter(live);
  const byHandRecent = q.byHand
    .filter((p) => !live(p) && (p.updatedAt ?? 0) >= nowEpochS - RECENT_DAYS * 86_400)
    .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
  return { running, queued, byHandLive, attention, byHandRecent };
}

/** "1 running · 2 queued · next: item19/fetch-resume (outside the 18:30–20:30 window)". */
export function headline(p: QueuePlan): string {
  const parts = [`${p.running.length} running`, `${p.queued.length} queued`];
  if (p.byHandLive.length > 0) parts.push(`${p.byHandLive.length} by hand`);
  const next = p.queued[0];
  if (next) parts.push(`next: ${next.name}${next.waits && next.waits !== "ready" ? ` (${next.waits})` : ""}`);
  return parts.join(" · ");
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
/**
 * Why a queued row has not started, in the orchestrator's words; for a ready row, where it stands:
 * only the first ready row starts next (one task at a time). `pos` is its place in up next.
 */
export function waitText(t: ResearchTask, pos?: number): string | null {
  if (t.status !== "queued") return t.note;
  if (t.waits !== "ready") return t.waits;
  return pos === 1 ? "ready: starts next, at the tick after the running task ends" : "ready, in line behind the rows above";
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
      className={verdictChip(statusWord(row.status))}
      title={`${row.title}: the study's verdict (open it on the studies tab)`}
    >
      {statusWord(row.status)}
    </Link>
  );
}

// --------------------------------------------------------------------------- the cards (2026-11-03)

/** One card on the tab: a queue row, or a run started by hand (its progress file). */
export type QueueItem =
  | { kind: "task"; key: string; section: QueueSection; t: ResearchTask; pos?: number }
  | { kind: "hand"; key: string; section: QueueSection; p: ResearchProgress };

export type QueueSection = "running" | "hand-live" | "next" | "attention" | "hand-recent";

/** Every card in the order the tab lays them out, which is the order the lightbox steps through. */
export function queueItems(q: ResearchQueue, nowMs = etNowMs(), nowEpochS = Date.now() / 1000): QueueItem[] {
  const p = plan(q, nowEpochS, nowMs);
  const task = (section: QueueSection) => (t: ResearchTask, i: number): QueueItem => ({
    kind: "task",
    key: String(t.id),
    section,
    t,
    ...(section === "next" ? { pos: i + 1 } : {}),
  });
  const hand = (section: QueueSection) => (x: ResearchProgress): QueueItem => ({ kind: "hand", key: `hand:${x.file}`, section, p: x });
  return [
    ...p.running.map(task("running")),
    ...p.byHandLive.map(hand("hand-live")),
    ...p.queued.map(task("next")),
    ...p.attention.map(task("attention")),
    ...p.byHandRecent.map(hand("hand-recent")),
  ];
}

/** "about 1 h 5 min left", "under a minute left"; null without an estimate. */
export function etaText(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  const mins = Math.round(seconds / 60);
  if (mins < 1) return "under a minute left";
  if (mins < 60) return `about ${String(mins)} min left`;
  const h = Math.floor(mins / 60);
  return `about ${String(h)} h ${String(mins % 60)} min left`;
}

/** "57.2 s/session", "4.0 symbol-days/s" (the orchestrator's wording); null without a rate. */
export function rateText(rate: ResearchProgress["rate"]): string | null {
  if (rate === null || !(rate.secondsEach > 0)) return null;
  const { phase, secondsEach } = rate;
  if (secondsEach >= 1) {
    const one = phase.endsWith("s") && !phase.endsWith("ss") ? phase.slice(0, -1) : phase;
    return `${secondsEach.toFixed(1)} s/${one}`;
  }
  return `${(1 / secondsEach).toFixed(1)} ${phase}/s`;
}

/** The rate and the time left side by side ("57.2 s/session · about 3 h 50 min left"). */
export function paceText(p: ResearchProgress | null | undefined): string {
  if (!p) return "";
  return [rateText(p.rate), etaText(p.etaSeconds)].filter((x) => x !== null).join(" · ");
}

/** A study's register title, heading only, else its slug. */
function studyTitle(study: string, studies?: StudyIndex): string {
  const row = studies?.byStudy.get(study);
  return row ? splitTitle(row.title)[0] : study;
}

function PhaseMeters({ p }: { p: ResearchProgress }) {
  if (p.phases.length === 0) return null;
  return (
    <span className="q-meters">
      {p.phases.map((ph) => (
        <span key={ph.name} className="q-meter">
          <span className="q-meter-name">{ph.name}</span>
          <span className="q-meter-track" aria-hidden="true">
            <span className="q-meter-fill" style={{ width: ph.total ? `${String(Math.min(100, Math.round((ph.done / ph.total) * 100)))}%` : "0%" }} />
          </span>
          <span className="q-meter-count">
            {ph.done}
            {ph.total !== null ? ` / ${String(ph.total)}` : ""} {ph.unit}
          </span>
        </span>
      ))}
    </span>
  );
}

function stateChip(t: ResearchTask): { word: string; cls: string } {
  if (t.status === "queued") return t.waits === "ready" ? { word: "ready", cls: "chip q-ready" } : { word: "waiting", cls: "chip q-waiting" };
  if (t.status === "failed" || t.status === "stopped") return { word: t.status, cls: "chip chip-warn" };
  return { word: t.status, cls: STATUS_CHIP[t.status] };
}

function RunningTile({ t, studies, nowMs, onOpen }: { t: ResearchTask; studies?: StudyIndex; nowMs: number; onOpen: () => void }) {
  const pct = t.progress?.fraction !== null && t.progress?.fraction !== undefined ? Math.round(t.progress.fraction * 100) : null;
  return (
    <button type="button" className="study-tile study-hero q-hero" data-task={t.id} aria-label={`Open ${t.name}`} onClick={onOpen}>
      <span className="study-tile-top">
        <span className="study-tile-status">
          <span className="study-dot is-running" />
          Running · started {ago(etMs(t.startedAt), nowMs)}
        </span>
        <span className="study-tile-slug">{t.name}</span>
      </span>
      <span className="study-tile-mid">
        <span className="study-hero-title">{studyTitle(t.study, studies)}</span>
        <span className="study-tile-sub">
          {t.step} · {t.kind}
          {t.needs.includes("thetadata") ? " · ThetaData" : ""}
        </span>
      </span>
      <span className="study-tile-foot">
        {pct !== null ? (
          <span className="q-pct">
            <span className="q-pct-num">{pct}%</span>
            <span className="study-tile-sub">{paceText(t.progress)}</span>
          </span>
        ) : null}
        {t.progress ? <PhaseMeters p={t.progress} /> : <span className="study-tile-sub">No progress file yet.</span>}
      </span>
    </button>
  );
}

function HandTile({ p, nowMs, onOpen, live }: { p: ResearchProgress; nowMs: number; onOpen: () => void; live: boolean }) {
  return (
    <button type="button" className="study-tile q-hand" data-task={`hand:${p.file}`} aria-label={`Open ${p.job}`} onClick={onOpen}>
      <span className="study-tile-top">
        <span className="study-tile-sub">Started by hand</span>
        <span className={PROGRESS_CHIP[p.state]}>{p.state}</span>
      </span>
      <span className="q-mono">{p.job}</span>
      <span className="study-tile-foot">
        {live && p.fraction !== null ? (
          <span className="q-meter-track" aria-hidden="true">
            <span className="q-meter-fill" style={{ width: `${(p.fraction * 100).toFixed(1)}%` }} />
          </span>
        ) : null}
        <span className="study-tile-sub">{live ? progressLine(p) : ago(p.updatedAt === null ? null : p.updatedAt * 1000 - (Date.now() - nowMs), nowMs)}</span>
      </span>
    </button>
  );
}

function TaskTile({ item, studies, nowMs, onOpen }: { item: Extract<QueueItem, { kind: "task" }>; studies?: StudyIndex; nowMs: number; onOpen: () => void }) {
  const t = item.t;
  const chip = stateChip(t);
  const attention = item.section === "attention";
  const wait = waitText(t, item.pos);
  return (
    <button type="button" className={attention ? "study-tile q-attn" : "study-tile"} data-task={t.id} aria-label={`Open ${t.name}`} onClick={onOpen}>
      <span className="study-tile-top">
        {item.pos !== undefined ? <span className={t.waits === "ready" ? "q-pos" : "q-pos is-waiting"}>{item.pos}</span> : <span className="study-tile-sub">{ago(endedMs(t), nowMs)}</span>}
        <span className={chip.cls}>{chip.word}</span>
      </span>
      <span className="study-tile-title">{studyTitle(t.study, studies)}</span>
      <span className="study-tile-foot">
        <span className="study-tile-sub">
          <span className="study-tile-slug">{t.study}</span> · {t.step} · {t.kind}
          {t.needs.includes("thetadata") ? " · ThetaData" : ""}
          {studies && !studies.byStudy.has(t.study) ? " · not in the register" : ""}
        </span>
        {attention ? (
          <span className={t.result?.outputExists === false ? "study-tile-sub pnl-neg" : "study-tile-sub"}>{t.note ?? resultLine(t)}</span>
        ) : wait ? (
          <span className="study-tile-sub q-wait">{wait}</span>
        ) : null}
      </span>
    </button>
  );
}

/** A queued, failed or stopped task's tile on one line: its place or age and state, its study, its step and wait. */
export function taskRow(item: Extract<QueueItem, { kind: "task" }>, studies: StudyIndex | undefined, nowMs: number, onOpen: () => void): BtRow {
  const t = item.t;
  const chip = stateChip(t);
  const attention = item.section === "attention";
  const wait = waitText(t, item.pos);
  const meta = [t.study, t.step, t.kind, t.needs.includes("thetadata") ? "ThetaData" : null, studies && !studies.byStudy.has(t.study) ? "not in the register" : null];
  return {
    key: item.key,
    label: `Open ${t.name}`,
    lead: (
      <>
        {item.pos !== undefined ? <span className={t.waits === "ready" ? "q-pos" : "q-pos is-waiting"}>{item.pos}</span> : null}
        <span className={chip.cls}>{chip.word}</span>
      </>
    ),
    title: studyTitle(t.study, studies),
    meta: meta.filter((x): x is string => x !== null).join(" · "),
    end: attention ? (
      <span className={t.result?.outputExists === false ? "pnl-neg" : undefined}>{t.note ?? resultLine(t)}</span>
    ) : (
      (wait ?? (item.pos === undefined ? ago(endedMs(t), nowMs) : null))
    ),
    attention,
    data: { "data-task": String(t.id) },
    onOpen,
  };
}

/** A run started by hand on one line: its state, its job, how far or how long ago. */
export function handRow(item: Extract<QueueItem, { kind: "hand" }>, nowMs: number, live: boolean, onOpen: () => void): BtRow {
  const p = item.p;
  return {
    key: item.key,
    label: `Open ${p.job}`,
    lead: <span className={PROGRESS_CHIP[p.state]}>{p.state}</span>,
    title: <span className="q-mono">{p.job}</span>,
    meta: "started by hand",
    end: live ? progressLine(p) : ago(p.updatedAt === null ? null : p.updatedAt * 1000 - (Date.now() - nowMs), nowMs),
    data: { "data-task": `hand:${p.file}` },
    onOpen,
  };
}

function CardSection({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="q-section">
      <h3 className="q-section-title">
        {title} {note ? <span className="muted">· {note}</span> : null}
      </h3>
      {children}
    </section>
  );
}

// ------------------------------------------------------------------ the lightbox

type TaskTab = "overview" | "args" | "result" | "log";

const TASK_TABS: LightboxTab<TaskTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "args", label: "Arguments" },
  { id: "result", label: "Result" },
  { id: "log", label: "Log" },
];

/** The task's arguments, one per line: a file shown by its name, never its full path. */
export function argRows(args: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(args).map(([k, v]) => {
    if (typeof v === "string" && /[\\/]/.test(v)) return [k, fileName(v)];
    if (Array.isArray(v)) return [k, v.map((x) => (typeof x === "string" && /[\\/]/.test(x) ? fileName(x) : String(x))).join(" ")];
    return [k, String(v)];
  });
}

function TaskOverview({ t, studies, nowMs }: { t: ResearchTask; studies?: StudyIndex; nowMs: number }) {
  const wait = waitText(t);
  return (
    <>
      <div className="q-times">
        <div>
          <span>Queued</span>
          <strong>{stamp(t.createdAt)}</strong>
        </div>
        <div>
          <span>Started</span>
          <strong>{stamp(t.startedAt)}</strong>
        </div>
        <div>
          <span>Finished</span>
          <strong>{stamp(t.finishedAt)}</strong>
        </div>
      </div>
      {wait ? <p className="study-decision">{wait}</p> : null}
      {t.progress ? (
        <section>
          {t.progress.fraction !== null ? (
            <div className="q-pct">
              <span className="q-pct-num">{Math.round(t.progress.fraction * 100)}%</span>
              <span className="muted">{paceText(t.progress)}</span>
            </div>
          ) : null}
          <PhaseMeters p={t.progress} />
        </section>
      ) : null}
      <p className="muted">
        Study: <StudyName study={t.study} studies={studies} /> <StudyChip study={t.study} studies={studies} /> · its card on the studies tab holds
        the verdict. Queued {ago(etMs(t.createdAt), nowMs)}.
      </p>
    </>
  );
}

function KeyValues({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="study-fields">
      {rows.map(([k, v]) => (
        <Field key={k} k={k}>
          {v}
        </Field>
      ))}
    </dl>
  );
}

function TaskResult({ t }: { t: ResearchTask }) {
  const r = t.result;
  const rows: Array<[string, ReactNode]> = [
    ["runs / exit", <span key="x" className={t.exitCode !== null && t.exitCode !== 0 ? "pnl-neg" : undefined}>{`${String(t.runs)} · ${t.exitCode ?? "—"}`}</span>],
  ];
  if (t.output) rows.push(["declares", fileName(t.output)]);
  for (const [k, v] of Object.entries(r?.report ?? {})) rows.push([k, String(v)]);
  if (r?.input) rows.push(["input", fileName(r.input)]);
  if (r?.store) rows.push(["store", fileName(r.store)]);
  if (r?.output) rows.push(["output", <span key="o" className={r.outputExists === false ? "pnl-neg" : undefined}>{`${fileName(r.output)}${r.outputExists === false ? " (missing)" : ""}`}</span>]);
  if (r?.log) rows.push(["log", fileName(r.log)]);
  if (t.note) rows.push(["note", t.note]);
  return r === null && !t.output ? <p className="muted">Not finished: no result yet.</p> : <KeyValues rows={rows} />;
}

function HandOverview({ p }: { p: ResearchProgress }) {
  return (
    <>
      <p className="study-decision">{progressLine(p) || "No reading yet."}</p>
      {p.fraction !== null ? (
        <div className="q-pct">
          <span className="q-pct-num">{Math.round(p.fraction * 100)}%</span>
          <span className="muted">{paceText(p)}</span>
        </div>
      ) : null}
      <PhaseMeters p={p} />
      <KeyValues
        rows={[
          ["state", p.state],
          ["progress file", p.file],
          ["last wrote", p.ageSeconds === null ? "—" : `${String(Math.round(p.ageSeconds))} s ago`],
        ]}
      />
    </>
  );
}

export function QueueDialogBody({
  item,
  tab,
  onTab,
  studies,
  nowMs,
  position,
  onPrev,
  onNext,
  onClose,
}: {
  item: QueueItem;
  tab: TaskTab;
  onTab: (t: TaskTab) => void;
  studies?: StudyIndex;
  nowMs: number;
  position: string;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  const nav = { position, onPrev, onNext, onClose };
  if (item.kind === "hand") {
    const p = item.p;
    return (
      <LightboxFrame kicker="Started by hand" title={p.job} chips={<span className={PROGRESS_CHIP[p.state]}>{p.state}</span>} tabs={[{ id: "overview", label: "Overview" }]} tab="overview" onTab={() => undefined} {...nav}>
        <HandOverview p={p} />
      </LightboxFrame>
    );
  }
  const t = item.t;
  const chip = stateChip(t);
  return (
    <LightboxFrame
      kicker={`#${String(t.id)} · ${t.name}`}
      title={studyTitle(t.study, studies)}
      chips={
        <>
          <span className={chip.cls}>{chip.word}</span>
          <span className="chip muted">{t.kind}</span>
          {t.needs.includes("thetadata") ? <span className="chip">needs ThetaData</span> : null}
        </>
      }
      tabs={TASK_TABS}
      tab={tab}
      onTab={onTab}
      {...nav}
    >
      {tab === "overview" ? <TaskOverview t={t} studies={studies} nowMs={nowMs} /> : null}
      {tab === "args" ? (
        <KeyValues
          rows={[
            ...argRows(t.args),
            ...(t.needs ? ([["needs", t.needs]] as Array<[string, string]>) : []),
            ...(t.notBefore ? ([["not before", `${stamp(t.notBefore)} ET`]] as Array<[string, string]>) : []),
            ...(t.stopBy ? ([["stop by", `${stamp(t.stopBy)} ET`]] as Array<[string, string]>) : []),
          ]}
        />
      ) : null}
      {tab === "result" ? <TaskResult t={t} /> : null}
      {tab === "log" ? <LogTail id={t.id} /> : null}
    </LightboxFrame>
  );
}

function QueueDialog({ items, index, studies, onStep, onClose }: { items: QueueItem[]; index: number; studies?: StudyIndex; onStep: (to: number) => void; onClose: () => void }) {
  const [tab, setTab] = useState<TaskTab>("overview");
  const item = items[index];
  if (item === undefined) return null;
  return (
    <CardLightbox count={items.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <QueueDialogBody item={item} tab={item.kind === "hand" ? "overview" : tab} onTab={setTab} studies={studies} nowMs={etNowMs()} position={nav.position} onPrev={nav.prev} onNext={nav.next} onClose={onClose} />
      )}
    </CardLightbox>
  );
}

function tasksOf(items: QueueItem[]): Array<Extract<QueueItem, { kind: "task" }>> {
  return items.filter((i): i is Extract<QueueItem, { kind: "task" }> => i.kind === "task");
}

function handsOf(items: QueueItem[]): Array<Extract<QueueItem, { kind: "hand" }>> {
  return items.filter((i): i is Extract<QueueItem, { kind: "hand" }> => i.kind === "hand");
}

/** The tab's cards: the running task large, then up next, the last week's failures, runs by hand. */
export function QueueCards({ q, nowMs = etNowMs(), studies }: { q: ResearchQueue; nowMs?: number; studies?: StudyIndex }) {
  const lightbox = useOpenParam("task");
  // Rows apply to the lists below the running task; the running tile and the hand runs beside it stay.
  const rows = useListView() === "rows";
  if (q.tasks.length === 0 && q.byHand.length === 0) return <p className="muted">The queue is empty.</p>;
  const items = queueItems(q, nowMs);
  const index = items.findIndex((i) => i.key === lightbox.open);
  const open = (i: QueueItem) => () => lightbox.show(i.key);
  const of = (s: QueueSection) => items.filter((i) => i.section === s);
  const running = of("running");
  const handLive = of("hand-live");
  const next = of("next");
  const attention = of("attention");
  const handRecent = of("hand-recent");
  return (
    <>
      <div className="q-top">
        {running.length > 0 ? (
          running.map((i) => (i.kind === "task" ? <RunningTile key={i.key} t={i.t} studies={studies} nowMs={nowMs} onOpen={open(i)} /> : null))
        ) : (
          <div className="study-tile study-hero q-hero q-idle">
            <span className="study-tile-status muted">Nothing running</span>
            <span className="study-tile-sub">{next.length > 0 ? "The top ready row starts at the next tick." : "Nothing is queued either."}</span>
          </div>
        )}
        <div className="q-side">
          {handLive.map((i) => (i.kind === "hand" ? <HandTile key={i.key} p={i.p} nowMs={nowMs} live onOpen={open(i)} /> : null))}
          <div className="q-note">One task at a time. When it ends, the top ready row starts at the next tick.</div>
        </div>
      </div>
      {next.length > 0 ? (
        <CardSection title="Up next" note="in the order the runner takes them">
          {rows ? (
            <BtRows rows={tasksOf(next).map((i) => taskRow(i, studies, nowMs, open(i)))} />
          ) : (
            <div className="study-tiles q-tiles">{tasksOf(next).map((i) => <TaskTile key={i.key} item={i} studies={studies} nowMs={nowMs} onOpen={open(i)} />)}</div>
          )}
        </CardSection>
      ) : null}
      {attention.length > 0 ? (
        <CardSection title="Failed or stopped" note={`last ${String(ATTENTION_DAYS)} days; each is also on its study's card`}>
          {rows ? (
            <BtRows rows={tasksOf(attention).map((i) => taskRow(i, studies, nowMs, open(i)))} />
          ) : (
            <div className="study-tiles q-tiles">{tasksOf(attention).map((i) => <TaskTile key={i.key} item={i} studies={studies} nowMs={nowMs} onOpen={open(i)} />)}</div>
          )}
        </CardSection>
      ) : null}
      {handRecent.length > 0 ? (
        <CardSection title="Started by hand" note={`last ${String(RECENT_DAYS)} days`}>
          {rows ? (
            <BtRows rows={handsOf(handRecent).map((i) => handRow(i, nowMs, false, open(i)))} />
          ) : (
            <div className="study-tiles q-tiles q-tiles-small">{handsOf(handRecent).map((i) => <HandTile key={i.key} p={i.p} nowMs={nowMs} live={false} onOpen={open(i)} />)}</div>
          )}
        </CardSection>
      ) : null}
      {index >= 0 ? <QueueDialog items={items} index={index} studies={studies} onStep={(to) => lightbox.step(items[to]?.key ?? "")} onClose={lightbox.close} /> : null}
    </>
  );
}

export function BacktestQueueTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useBridged<ResearchQueue>(["backtest", "queue"], "/api/backtest/queue", queuePollMs);
  const studies = useStudies();
  const [view, setView] = useViewChoice("backtest-queue");
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
        <div className="q-strip">
          <span className="q-strip-chip">
            <span className={q.sessionHeld ? "study-dot" : "study-dot is-running"} />
            ThetaData session {q.sessionHeld ? "held by another process" : "free"}
          </span>
          {q.blackout ? <span className="q-strip-chip muted">Nightly window {q.blackout[0]}–{q.blackout[1]} ET</span> : null}
        </div>
        <p className="q-headline">{headline(plan(q, undefined, nowMs))}</p>
        <QueueCards q={q} nowMs={nowMs} studies={index} />
      </>
    );
  }
  return (
    <Card
      title="Research queue"
      collapseKey="backtest-queue"
      updatedAt={dataUpdatedAt}
      isError={isError}
      controls={<ViewSwitch view={view} onChange={setView} />}
      help={{
        title: "How the queue runs",
        body: (
          <>
            <HelpSection title="One task at a time">
              <p>
                The supervisor runs one task at a time: on each pass it takes the oldest queued row that is ready, by the order rows were
                added. A row waits while its <code>--after</code> step is not done, before its <code>--not-before</code>, or, for a
                ThetaData fetch, while the session is held or inside the nightly window (it resumes after).
              </p>
            </HelpSection>
            <HelpSection title="Kinds of task">
              <p>
                Kinds are allow-listed: a fetch (fetch-chains, fetch-flow, fetch-oi), a validation, or a script checked into{" "}
                <code>scripts/</code>.
              </p>
            </HelpSection>
            <HelpSection title="Adding and cancelling work">
              <p>
                Add one with{" "}
                <code>run.py queue-add --study &lt;study&gt; [--step &lt;step&gt;] --task &lt;kind&gt; --task-args &apos;&#123;…&#125;&apos;</code>;
                cancel a queued one with <code>run.py queue-cancel --task-id N</code>. Read-only here.
              </p>
            </HelpSection>
            <HelpSection title="Where results are">
              <p>
                A study&apos;s finished rows, with their results and logs, are on its card on the studies tab. A run started by hand shows
                here through its progress file.
              </p>
            </HelpSection>
            <HelpSection title="Cards or rows">
              <p>
                The switch in the card head lists up next, the failures and the hand runs as rows, one line a task; a row opens the same
                card. The running task stays large either way.
              </p>
            </HelpSection>
          </>
        ),
      }}
    >
      <ListViewContext.Provider value={view}>{body}</ListViewContext.Provider>
    </Card>
  );
}
