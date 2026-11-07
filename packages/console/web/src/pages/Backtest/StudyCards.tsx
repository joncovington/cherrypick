import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type {
  BacktestRunReport,
  BridgedResult,
  ResearchQueue,
  ResearchTask,
  StudiesBoard,
  StudyGroup,
  StudyResult,
  StudyRow,
  StudyRunFigures,
  StudyRunSummaries,
  StudyVersion,
} from "@console/shared";
import { isBacktestRunId } from "@console/shared";
import { useBridged } from "../../lib/bridged";
import { fmtCash, fmtCount } from "../../lib/format";
import { useRun } from "./BacktestPage";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "./CardLightbox";
import { GridBars } from "./GridBars";
import { ReportTab } from "./StudyReport";
import { WeekSummary, mondayOf, useStudyDigest } from "./StudyWeek";
import { TaskDetail, ago, etMs, etNowMs, queuePollMs, resultLine, stamp } from "./QueueTab";
import { VERDICT_STATUSES, resultLabel, statusWord, studyHref, verdictChip } from "./studyIndex";

/**
 * The studies tab as a mosaic (2026-11-03): one tile per hypothesis-register item, the study running
 * now as a large tile, each opening to a lightbox with four tabs (overview, declaration, grid, queue).
 *
 * Every word is the orchestrator's or the study's: the register's status is the big word on a
 * finished tile, a result file's per-arm verdicts give the "2 pass · 5 fail" line, and a stale or
 * provisional mark is read from the result, never worked out from figures.
 *
 * A tile's figures (2026-11-03) come from the same run its lightbox opens (`figuresRun`), read for
 * every tile at once in brief (`/api/backtest/summaries`): each arm's running net as a small line and
 * the arm with the highest mean net a session, as the run's report states it. The board's words
 * never wait on them; a tile whose run is not on this machine simply shows none.
 *
 * The open study is `?study=` (the link the other tabs already use), so the back button closes it.
 */

const REPO_BLOB = "https://github.com/joncovington/cherrypick/blob/main/";

export const GROUP_ORDER: StudyGroup[] = ["in_progress", "declared", "finished", "observation"];

export const GROUP_WORD: Record<StudyGroup, string> = {
  in_progress: "In progress",
  declared: "Declared",
  finished: "Finished",
  observation: "Observations",
};

// --------------------------------------------------------------------------- results in words

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
      {row.results.some((r) => r.source === "queue") ? (
        <span className="chip muted" title="The study wrote this verdict; the hypothesis register does not record it yet">
          not yet recorded
        </span>
      ) : null}
      {row.results.some((r) => r.provisional) ? <span className="chip chip-warn">provisional</span> : null}
      {row.results.some((r) => r.version?.stale) ? <span className="chip chip-warn">stale</span> : null}
    </span>
  );
}

/**
 * Which version of its script's replay wrote a result. Stale when a correction has landed since: the
 * file predates it, so its verdict is not the current replay's. Each correction it predates is listed.
 */
export function VersionNote({ v }: { v: StudyVersion }) {
  if (v.error) return <span className="pnl-neg"> replay log unreadable: {v.error}</span>;
  if (!v.stale) {
    return v.version !== null ? <span className="muted"> · replay v{v.version}</span> : null;
  }
  const n = v.since.length;
  return (
    <>
      <span className="chip chip-warn">
        {" "}
        stale: {v.version === null ? "written before stamping" : `replay v${v.version}`}, now v{v.current}
      </span>
      <ul className="muted" style={{ margin: "0.25em 0 0", paddingLeft: "1.1em" }}>
        {v.since.map((c) => (
          <li key={`${c.on} ${c.what}`}>
            {c.on}: {c.what}
            {c.beforeRead ? "" : " (made after a read)"}
          </li>
        ))}
      </ul>
      {n === 0 ? null : <span className="muted"> {n === 1 ? "one correction" : `${n} corrections`} since: rerun for the current replay</span>}
    </>
  );
}

/** `results.json` -> `results.md`: the file a study's notes sit in. */
export function notesName(resultName: string): string {
  return resultName.replace(/\.json$/, "") + ".md";
}

/** The studies tab showing one result file's notes full width (`?study=&notes=`). */
export function notesHref(study: string, resultName: string): string {
  return `/backtest/studies?study=${encodeURIComponent(study)}&notes=${encodeURIComponent(resultName)}`;
}

/** The plan section on GitHub, from the register's `<file>#<heading>` pointer. */
export function planUrl(pointer: string | null): string | null {
  return pointer ? `${REPO_BLOB}${pointer}` : null;
}

// --------------------------------------------------------------------------- the tile's model

export type TileKind = "verdict" | "running" | "declared" | "observation";

export interface TileModel {
  kind: TileKind;
  /** The register's status in words: the big word on a finished tile. */
  word: string;
  tone: "pos" | "neg" | null;
  /** The newest result's arms when they disagree: "2 pass · 5 fail". */
  arms: string | null;
  stale: boolean;
  provisional: boolean;
  /** Its window, provisional, and "not yet recorded", as one muted line. */
  meta: string;
  /** For a study in progress: the step running, else the next queued, and how far through. */
  step: string | null;
  done: number;
  total: number;
  running: boolean;
}

/** The newest result file on this machine, by when it was written. */
export function newestResult(row: StudyRow): StudyResult | null {
  const present = row.results.filter((r) => r.present);
  present.sort((a, b) => (etMs(b.modified) ?? 0) - (etMs(a.modified) ?? 0));
  return present[0] ?? null;
}

/** "2 pass · 5 fail" when a result's arms disagree; null when they agree or there is one word. */
export function armsLine(r: StudyResult | null): string | null {
  const vs = (r?.verdicts ?? []).filter((v) => v.arm !== null);
  if (vs.length < 2) return null;
  const counts = new Map<string, number>();
  for (const v of vs) counts.set(v.verdict, (counts.get(v.verdict) ?? 0) + 1);
  if (counts.size < 2) return null;
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([word, n]) => `${String(n)} ${word}`)
    .join(" · ");
}

export function tileModel(row: StudyRow): TileModel {
  const newest = newestResult(row);
  const running = row.tasks.find((t) => t.status === "running");
  const queued = row.tasks.find((t) => t.status === "queued");
  const kind: TileKind =
    row.group === "in_progress" ? "running" : row.group === "declared" ? "declared" : row.group === "observation" ? "observation" : "verdict";
  const provisional = row.results.some((r) => r.present && r.provisional === true);
  const meta = [
    newest?.window && /^(design|holdout)$/.test(newest.window) ? newest.window : null,
    provisional ? "provisional" : null,
    row.results.some((r) => r.source === "queue") ? "not yet recorded" : null,
  ].filter((x): x is string => x !== null);
  return {
    kind,
    word: statusWord(row.status),
    tone: row.status === "passed" ? "pos" : row.status === "failed" ? "neg" : null,
    arms: armsLine(newest),
    stale: row.results.some((r) => r.version?.stale === true),
    provisional,
    meta: meta.join(" · "),
    step: running ? `${running.step} running` : queued ? `${queued.step} queued` : null,
    done: row.tasks.filter((t) => t.status === "done").length,
    total: row.tasks.filter((t) => t.status !== "cancelled").length,
    running: running !== undefined,
  };
}

/** The tiles in board order within each group, the groups in progress first. */
export function orderedRows(board: StudiesBoard, group: StudyGroup | null): StudyRow[] {
  return GROUP_ORDER.filter((g) => group === null || g === group).flatMap((g) => board.rows.filter((r) => r.group === g));
}

/** The large tile: the first study with a task running now, else none. */
export function heroStudy(rows: StudyRow[]): StudyRow | null {
  return rows.find((r) => r.tasks.some((t) => t.status === "running")) ?? null;
}

// --------------------------------------------------------------------------- tile figures

/** The run ids every tile's figures come from (`figuresRun`), sorted, each once. */
export function figuresRunIds(rows: StudyRow[]): string[] {
  const ids = rows.map((r) => figuresRun(r)?.runId ?? null).filter((x): x is string => x !== null);
  return [...new Set(ids)].sort();
}

/** Every tile's run in brief, one read for the board. A run's figures never change once written. */
export function useRunSummaries(ids: string[]) {
  const q = ids.join(",");
  return useQuery<BridgedResult<StudyRunSummaries>>({
    queryKey: ["bridged", "backtest", "summaries", q],
    enabled: ids.length > 0,
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const res = await fetch(`/api/backtest/summaries?ids=${encodeURIComponent(q)}`);
      if (!res.ok) throw new Error(`summaries: HTTP ${String(res.status)}`);
      return (await res.json()) as BridgedResult<StudyRunSummaries>;
    },
  });
}

export interface TileLine {
  arm: string;
  path: number[];
  lead: boolean;
  control: boolean;
}

export interface TileFigures {
  lines: TileLine[];
  /** The arm with the highest mean net a session, control left out while any other arm has one. */
  lead: { arm: string; mean: number } | null;
  /** Arms with a mean: "best of 4" reads against it. */
  armsWithMean: number;
  /** Control's line is left off: it trades many times as often as the others (`controlScaleOf`). */
  controlLeftOff: boolean;
}

/** What a tile draws from its run in brief; null when there is nothing to draw. Description only:
 *  the highest mean is a fact of the report, never the study's verdict. */
export function tileFigures(run: StudyRunFigures): TileFigures | null {
  const arms = Object.entries(run.arms);
  const rates = Object.fromEntries(arms.map(([a, f]) => [a, f.trades !== null && f.sessions ? f.trades / f.sessions : null]));
  const controlLeftOff = controlScaleOf(rates) !== null;
  const withMean = arms.filter((x): x is [string, (typeof x)[1] & { mean: number }] => x[1].mean !== null);
  const rivals = withMean.filter(([a]) => a !== "control");
  const pool = rivals.length > 0 ? rivals : withMean;
  const best = pool.reduce<[string, number] | null>((acc, [a, f]) => (acc === null || f.mean > acc[1] ? [a, f.mean] : acc), null);
  const lead = best === null ? null : { arm: best[0], mean: best[1] };
  const lines = arms
    .filter(([a, f]) => f.path.length > 1 && !(a === "control" && controlLeftOff))
    .map(([a, f]) => ({ arm: a, path: f.path, lead: a === lead?.arm, control: a === "control" }));
  if (lines.length === 0 && lead === null) return null;
  return { lines, lead, armsWithMean: pool.length, controlLeftOff };
}

/** Each arm's running net as a thin line, the leading arm drawn over the rest, control dashed. */
export function TileSpark({ fig }: { fig: TileFigures }) {
  if (fig.lines.length === 0) return null;
  const W = 200;
  const H = 48;
  const values = fig.lines.flatMap((l) => l.path);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const y = (v: number) => 2 + ((top - v) / span) * (H - 4);
  const pts = (path: number[]) => path.map((v, i) => `${((i / (path.length - 1)) * W).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const order = [...fig.lines].sort((a, b) => Number(a.lead) - Number(b.lead));
  return (
    <svg className="study-spark" viewBox={`0 0 ${String(W)} ${String(H)}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="study-spark-zero" vectorEffect="non-scaling-stroke" />
      {order.map((l) => (
        <polyline
          key={l.arm}
          points={pts(l.path)}
          className={l.lead ? "study-spark-lead" : "study-spark-line"}
          strokeDasharray={l.control ? "4 3" : undefined}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

/** "d10 · +$12.30 / session", "best of 4" beside it when there is more than one arm to choose from. */
export function TileFigureLine({ fig }: { fig: TileFigures }) {
  if (fig.lead === null) return null;
  return (
    <span className="study-tile-fig">
      <span className="study-tile-fig-arm">{fig.lead.arm}</span>
      <span className={`study-tile-fig-amt ${fig.lead.mean < 0 ? "pnl-neg" : fig.lead.mean > 0 ? "pnl-pos" : ""}`}>
        <span>{fmtCash(fig.lead.mean)}</span> <span>/ session</span>
      </span>
      {fig.armsWithMean > 1 ? <span className="muted">best of {fig.armsWithMean}</span> : null}
    </span>
  );
}

// --------------------------------------------------------------------------- the tiles

function StaleMark() {
  return (
    <span className="chip chip-warn study-tile-stale" title="A correction to its replay has landed since this result was written">
      stale
    </span>
  );
}

/** One segment per queue step, done steps filled, the running one bright. */
function StepSegments({ row }: { row: StudyRow }) {
  const steps = row.tasks.filter((t) => t.status !== "cancelled");
  if (steps.length === 0) return null;
  return (
    <span className="study-segments" aria-hidden="true">
      {steps.map((t) => (
        <span key={t.id} className={`study-segment is-${t.status}`} />
      ))}
    </span>
  );
}

export function StudyTile({
  row,
  nowMs,
  onOpen,
  hero = false,
  figures = null,
}: {
  row: StudyRow;
  nowMs: number;
  onOpen: () => void;
  hero?: boolean;
  figures?: TileFigures | null;
}) {
  const m = tileModel(row);
  const when = ago(etMs(row.lastActivity), nowMs);
  return (
    <button
      type="button"
      className={["study-tile", hero ? "study-hero" : "", figures ? "has-figures" : ""].filter((c) => c !== "").join(" ")}
      data-study={row.study}
      aria-label={`Open ${row.study}: ${row.title}`}
      onClick={onOpen}
    >
      <span className="study-tile-top">
        {m.kind === "verdict" ? (
          <span className={`study-tile-word${m.tone ? ` tone-${m.tone}` : ""}`}>{m.word}</span>
        ) : m.kind === "running" ? (
          <span className="study-tile-status">
            <span className={m.running ? "study-dot is-running" : "study-dot"} />
            {hero ? `Running now · ${m.step ?? "between steps"}` : (m.step ?? "between steps")}
          </span>
        ) : (
          <span className="study-tile-status muted">{m.kind === "declared" ? "Declared" : "Observation"}</span>
        )}
        <span className="study-tile-tags">
          {m.stale ? <StaleMark /> : null}
          <span className="study-tile-slug">{row.study}</span>
        </span>
      </span>

      <span className="study-tile-mid">
        {hero ? <span className="study-hero-title">{row.title}</span> : null}
        {m.kind === "verdict" && m.arms ? <span className="study-tile-sub">{m.arms}</span> : null}
        {m.kind === "running" ? (
          <span className="study-tile-sub">
            {m.done} of {m.total} steps done
          </span>
        ) : null}
        {(hero || ((m.kind === "declared" || m.kind === "observation") && !figures)) && (row.expected ?? row.outcome) ? (
          <span className={hero ? "study-hero-expect" : "study-tile-sub study-tile-clamp"}>{row.expected ?? row.outcome}</span>
        ) : null}
        {figures ? (
          <>
            <TileSpark fig={figures} />
            <TileFigureLine fig={figures} />
          </>
        ) : null}
      </span>

      <span className="study-tile-foot">
        {hero ? null : <span className="study-tile-title">{row.title}</span>}
        {m.kind === "running" ? <StepSegments row={row} /> : null}
        <span className="study-tile-sub">{[m.meta, when].filter((x) => x !== "" && x !== "—").join(" · ")}</span>
      </span>
    </button>
  );
}

// --------------------------------------------------------------------------- the lightbox: run figures

/** The run a study's figures come from: its newest result file that names one in the runner's shape.
 *  A study script's own label ("item30-2026-10-30") names no report the runner wrote, so it is passed
 *  over for an older file that does, never asked for. */
export function figuresRun(row: StudyRow): StudyResult | null {
  const withRun = row.results.filter((r) => r.present && r.runId !== null && isBacktestRunId(r.runId));
  withRun.sort((a, b) => (etMs(b.modified) ?? 0) - (etMs(a.modified) ?? 0));
  return withRun[0] ?? null;
}

/** An arm's trades a session over the run, from the report's own counts; null when either is missing. */
export function tradesPerSession(report: BacktestRunReport, arm: string): number | null {
  const a = report.arms[arm];
  if (!a || a.trades === null || a.trades === undefined || !a.summary.sessions) return null;
  return a.trades / a.summary.sessions;
}

/** How many times more often control trades than the other arms before it is a different design. */
export const CONTROL_SCALE = 10;

/**
 * Control's trades a session over the median of the other arms', when that exceeds CONTROL_SCALE:
 * MEIC's control is the module's sampling arm (hundreds of condors a session in paper and replay
 * alike), so its dollars a session do not compare with a one-condor rule's. Null otherwise.
 */
export function controlScale(report: BacktestRunReport): number | null {
  return controlScaleOf(Object.fromEntries(Object.keys(report.arms).map((a) => [a, tradesPerSession(report, a)])));
}

/** `controlScale` over each arm's trades a session, however they were read. */
export function controlScaleOf(rates: Record<string, number | null>): number | null {
  const ctl = rates["control"] ?? null;
  const others = Object.entries(rates)
    .filter(([a]) => a !== "control")
    .map(([, r]) => r)
    .filter((x): x is number => x !== null && x > 0)
    .sort((a, b) => a - b);
  if (ctl === null || others.length === 0) return null;
  const median = others[Math.floor(others.length / 2)]!;
  const ratio = ctl / median;
  return ratio > CONTROL_SCALE ? ratio : null;
}

const LINE_COLOURS = ["var(--accent)", "var(--chart-special)", "var(--amber-line)", "var(--chart-orange)", "var(--live)", "var(--pos)"];

/**
 * Each arm's running net over the run, control dashed. Every point is the module's `cards.py`. Each
 * legend entry hides or shows its line, and the scale follows the lines shown: one arm far wider
 * than the rest would otherwise flatten them.
 */
export function EquityLines({ report }: { report: BacktestRunReport }) {
  const scale = controlScale(report);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set(scale === null ? [] : ["control"]));
  const arms = Object.entries(report.cards?.arms ?? {}).filter(([, c]) => c.equity.length > 1);
  if (arms.length === 0) return <p className="muted">This run's report carries no equity path.</p>;
  let colour = 0;
  const all = arms.map(([arm, c]) => ({
    arm,
    equity: c.equity,
    stroke: arm === "control" ? "var(--text-muted)" : (LINE_COLOURS[colour++ % LINE_COLOURS.length] ?? "var(--accent)"),
  }));
  const shown = all.filter((l) => !hidden.has(l.arm));
  const W = 860;
  const H = 240;
  const P = 12;
  const values = shown.flatMap((l) => l.equity.map((e) => e.cum));
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const longest = Math.max(2, ...shown.map((l) => l.equity.length));
  const x = (i: number) => P + (i / (longest - 1)) * (W - 2 * P);
  const y = (v: number) => P + ((top - v) / span) * (H - 2 * P);
  const toggle = (arm: string) => {
    const next = new Set(hidden);
    if (next.has(arm)) next.delete(arm);
    else next.add(arm);
    setHidden(next);
  };
  return (
    <figure className="study-equity">
      <svg viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label="Each arm's running net over the run">
        <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="var(--chart-grid-strong)" strokeDasharray="4 4" />
        {shown.map((l) => (
          <polyline
            key={l.arm}
            points={l.equity.map((e, i) => `${x(i).toFixed(1)},${y(e.cum).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={l.stroke}
            strokeWidth={2}
            strokeDasharray={l.arm === "control" ? "6 4" : undefined}
          />
        ))}
      </svg>
      <figcaption className="study-legend">
        {all.map((l) => (
          <button key={l.arm} type="button" className="study-legend-item" aria-pressed={!hidden.has(l.arm)} onClick={() => toggle(l.arm)}>
            <span className="study-swatch" style={{ background: hidden.has(l.arm) ? "transparent" : l.stroke, borderColor: l.stroke }} />
            {l.arm}
          </button>
        ))}
        <span className="muted">
          {fmtCash(top)} top · {fmtCash(bottom)} bottom · press an arm to hide it
        </span>
      </figcaption>
      {scale !== null ? (
        <p className="muted study-section-note">
          Control starts hidden: it trades about {Math.round(scale)} times as often a session as the other arms (the module&apos;s own
          sampling arm, not this study&apos;s base), so its dollars a session do not compare with theirs.
        </p>
      ) : null}
    </figure>
  );
}

/** Each arm's mean net per session, its sessions, and the verdict the result file gave it. */
export function ArmFigures({ report, result }: { report: BacktestRunReport; result: StudyResult }) {
  const word = new Map((result.verdicts ?? []).map((v) => [v.arm, v.verdict]));
  const arms = Object.entries(report.arms).sort(([a], [b]) => Number(b === "control") - Number(a === "control") || a.localeCompare(b));
  return (
    <div className="study-arms">
      {arms.map(([arm, a]) => {
        const v = word.get(arm) ?? (arm === "control" ? null : (word.get(null) ?? null));
        return (
          <div key={arm} className="study-arm">
            <span className="study-arm-name">{arm}</span>
            <span className={`study-arm-mean ${a.summary.mean === null ? "" : a.summary.mean < 0 ? "pnl-neg" : "pnl-pos"}`}>
              {fmtCash(a.summary.mean)} / session
            </span>
            <span className="muted">
              {fmtCount(a.summary.sessions)} sessions
              {tradesPerSession(report, arm) === null ? "" : ` · ${fmtTrades(tradesPerSession(report, arm)!)} trades / session`}
            </span>
            {v ? <span className={verdictChip(v)}>{v}</span> : arm === "control" ? <span className="chip muted">control</span> : null}
          </div>
        );
      })}
    </div>
  );
}

/** "1", "0.9", "283": trades a session, to one decimal below ten. */
export function fmtTrades(v: number): string {
  return v >= 10 ? String(Math.round(v)) : String(Math.round(v * 10) / 10);
}

function RunFigures({ row }: { row: StudyRow }) {
  const result = figuresRun(row);
  const { data, isLoading, isError, error } = useRun(result?.runId ?? null, { capital: null });
  if (result === null) {
    return <p className="muted">{row.group === "declared" ? "Declared, not run yet." : "No result file here names a run the backtest runner wrote, so there are no figures to show."}</p>;
  }
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "50%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "run report unavailable"}</p>;
  else {
    body = (
      <>
        <ArmFigures report={data.data} result={result} />
        <EquityLines report={data.data} />
      </>
    );
  }
  return (
    <>
      <p className="muted study-section-note">
        From the {resultLabel(result)} run <Link to={`/backtest/runs?run=${result.runId ?? ""}`}>{result.runId}</Link>
        {result.provisional ? ", provisional" : ""}. Net of fees, whole-position dollars.
      </p>
      {body}
    </>
  );
}

// --------------------------------------------------------------------------- the lightbox: queue rows

const TASK_CHIP: Record<string, string> = {
  running: "chip chip-ok",
  queued: "chip",
  done: "chip muted",
  failed: "chip chip-missing",
  stopped: "chip chip-warn",
  cancelled: "chip muted",
};

/** The study's queue rows, newest first, each opening to its arguments, result and log. */
function StudyQueue({ row, nowMs }: { row: StudyRow; nowMs: number }) {
  const { data } = useBridged<ResearchQueue>(["backtest", "queue"], "/api/backtest/queue", queuePollMs);
  const tasks: ResearchTask[] = data?.ok && data.data ? data.data.tasks.filter((t) => t.study === row.study) : [];
  if (tasks.length === 0) {
    if (row.tasks.length === 0) return <p className="muted">Nothing queued for this study.</p>;
    return <p className="muted">{row.tasks.map((t) => `${t.step} ${t.status}`).join(" · ")}</p>;
  }
  const when = (t: ResearchTask) => etMs(t.finishedAt) ?? etMs(t.startedAt) ?? etMs(t.createdAt);
  const sorted = [...tasks].sort((a, b) => (when(b) ?? 0) - (when(a) ?? 0) || b.id - a.id);
  return (
    <ul className="study-queue">
      {sorted.map((t) => (
        <li key={t.id}>
          <details>
            <summary>
              <span className="study-queue-step">{t.step}</span>
              <span className="chip muted">{t.kind}</span>
              <span className={TASK_CHIP[t.status] ?? "chip"}>{t.status}</span>
              <span className={`study-queue-result ${t.result?.outputExists === false ? "pnl-neg" : "muted"}`}>{resultLine(t)}</span>
              <span className="muted" title={stamp(t.finishedAt ?? t.startedAt ?? t.createdAt)}>
                {ago(when(t), nowMs)}
              </span>
            </summary>
            <TaskDetail t={t} />
          </details>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------- the lightbox

export type DialogTab = "overview" | "report" | "declaration" | "grid" | "queue";

export const DIALOG_TABS: LightboxTab<DialogTab>[] = [
  { id: "overview", label: "Overview" },
  { id: "report", label: "Report" },
  { id: "declaration", label: "Declaration" },
  { id: "grid", label: "Grid" },
  { id: "queue", label: "Queue" },
];

function Field({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

function OverviewTab({ row }: { row: StudyRow }) {
  const stale = row.results.filter((r) => r.version?.stale);
  return (
    <>
      {stale.length > 0 ? (
        <section className="study-callout">
          <strong>A correction landed after this ran. Rerun before relying on the verdict.</strong>
          {stale.map((r) =>
            r.version ? (
              <div key={r.name}>
                {resultLabel(r)}: <VersionNote v={r.version} />
              </div>
            ) : null,
          )}
        </section>
      ) : null}
      {row.outcome ? <p className="study-decision">{row.outcome}</p> : null}
      <section>
        <h3>Figures</h3>
        <RunFigures row={row} />
      </section>
    </>
  );
}

function DeclarationTab({ row }: { row: StudyRow }) {
  const url = planUrl(row.pointer);
  return (
    <>
      <dl className="study-fields">
        {row.legacy ? <Field k="declared">before the register: the question, test and rivals are in the plan section</Field> : null}
        {row.measure ? <Field k="measures">{row.measure}</Field> : null}
        {row.expected ? <Field k="expects">{row.expected}</Field> : null}
        {row.sample ? <Field k="sample">{row.sample}</Field> : null}
        {row.rule ? <Field k="pass test">{row.rule}</Field> : null}
        {row.mechanism ? <Field k="why it might work">{row.mechanism}</Field> : null}
      </dl>
      {row.rivals.length > 0 ? (
        <>
          <h3>Rival explanations</h3>
          <ul className="study-list">
            {row.rivals.map((r) => (
              <li key={r.explanation}>
                {r.explanation} <span className="muted">— checked by: {r.check}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {url ? (
        <p>
          <a href={url} target="_blank" rel="noreferrer">
            The plan section
          </a>
        </p>
      ) : null}
    </>
  );
}

function GridTab({ row }: { row: StudyRow }) {
  const grids = row.results.filter((r) => r.grid !== null);
  if (grids.length === 0) return <p className="muted">No grid: this study tested its arms against control, not a range of one setting.</p>;
  return (
    <>
      {grids.map((r) =>
        r.grid ? (
          <section key={r.name}>
            <h3>{resultLabel(r)}</h3>
            <GridBars grid={r.grid} />
          </section>
        ) : null,
      )}
    </>
  );
}

function QueueTab({ row, nowMs }: { row: StudyRow; nowMs: number }) {
  return (
    <>
      <h3>Result files</h3>
      {row.results.length === 0 ? <p className="muted">No result file yet.</p> : null}
      <dl className="study-fields">
        {row.results.map((r) => (
          <Field key={r.name} k={resultLabel(r)}>
            {!r.present ? (
              <span className="pnl-neg">{r.name} is missing on this machine</span>
            ) : r.verdicts === null ? (
              <span className="muted">no verdict word in {r.name}; the plan section has the result</span>
            ) : (
              r.verdicts.map((v) => `${v.arm ?? "verdict"}: ${v.verdict}`).join(" · ")
            )}
            {r.source === "queue" ? <span className="muted"> · from the queue, not yet in the register</span> : null}
            {r.runId ? (
              <>
                {" "}
                · <Link to={`/backtest/runs?run=${r.runId}`}>run</Link>
              </>
            ) : null}
            {r.notes !== null ? (
              <>
                {" "}
                · <Link to={notesHref(row.study, r.name)}>{notesName(r.name)}</Link>
              </>
            ) : null}
          </Field>
        ))}
      </dl>
      <h3>Queue rows</h3>
      <StudyQueue row={row} nowMs={nowMs} />
    </>
  );
}

/** One study in the lightbox: a header, five tabs, and the way to the next study. */
export function StudyDialogBody({
  row,
  tab,
  onTab,
  position,
  nowMs,
  onPrev,
  onNext,
  onClose,
}: {
  row: StudyRow;
  tab: DialogTab;
  onTab: (t: DialogTab) => void;
  position: string;
  nowMs: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  return (
    <LightboxFrame
      kicker={
        <>
          {row.study} · last moved {ago(etMs(row.lastActivity), nowMs)}
        </>
      }
      title={row.title}
      chips={
        <>
          <span className={verdictChip(VERDICT_STATUSES.includes(row.status) ? statusWord(row.status) : null)}>{statusWord(row.status)}</span>
          <Verdicts row={row} />
          <Link to={studyHref(row.study)} className="study-dialog-link" title="This study's link">
            ?study={row.study}
          </Link>
        </>
      }
      tabs={DIALOG_TABS}
      tab={tab}
      onTab={onTab}
      position={position}
      onPrev={onPrev}
      onNext={onNext}
      onClose={onClose}
    >
      {tab === "overview" ? <OverviewTab row={row} /> : null}
      {tab === "report" ? <ReportTab row={row} /> : null}
      {tab === "declaration" ? <DeclarationTab row={row} /> : null}
      {tab === "grid" ? <GridTab row={row} /> : null}
      {tab === "queue" ? <QueueTab row={row} nowMs={nowMs} /> : null}
    </LightboxFrame>
  );
}

function StudyDialog({ rows, index, onStep, onClose }: { rows: StudyRow[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const [tab, setTab] = useState<DialogTab>("overview");
  const row = rows[index];
  if (row === undefined) return null;
  return (
    <CardLightbox count={rows.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <StudyDialogBody row={row} tab={tab} onTab={setTab} position={nav.position} nowMs={etNowMs()} onPrev={nav.prev} onNext={nav.next} onClose={onClose} />
      )}
    </CardLightbox>
  );
}

// --------------------------------------------------------------------------- the mosaic

function isGroup(v: string | null): v is StudyGroup {
  return v !== null && (GROUP_ORDER as string[]).includes(v);
}

/** `?group=week`: the studies active since Monday, beside the board's own groups. */
export const WEEK = "week";
export type BoardView = StudyGroup | typeof WEEK;

export function GroupChips({
  board,
  group,
  onPick,
  week = false,
}: {
  board: StudiesBoard;
  group: StudyGroup | null;
  onPick: (g: BoardView | null) => void;
  week?: boolean;
}) {
  const count = (g: StudyGroup) => board.rows.filter((r) => r.group === g).length;
  return (
    <div className="history-controls study-chips" role="group" aria-label="Show studies">
      <button type="button" className={group === null && !week ? "chip chip-ok" : "chip"} aria-pressed={group === null && !week} onClick={() => onPick(null)}>
        All {board.rows.length}
      </button>
      <button type="button" className={week ? "chip chip-ok" : "chip"} aria-pressed={week} onClick={() => onPick(WEEK)}>
        This week
      </button>
      {GROUP_ORDER.filter((g) => count(g) > 0).map((g) => (
        <button key={g} type="button" className={group === g ? "chip chip-ok" : "chip"} aria-pressed={group === g} onClick={() => onPick(g)}>
          {GROUP_WORD[g]} {count(g)}
        </button>
      ))}
    </div>
  );
}

/**
 * Which tiles the lightbox steps through and where the open study sits among them. A study named in
 * the URL but filtered out by the group still opens, stepping through every study instead.
 */
export function openIndex(board: StudiesBoard, group: StudyGroup | null, study: string | null): { rows: StudyRow[]; index: number } {
  const shown = laidOut(orderedRows(board, group));
  const rows = shown.some((r) => r.study === study) ? shown : laidOut(orderedRows(board, null));
  return { rows, index: rows.findIndex((r) => r.study === study) };
}

/** This week's stepping order: the week's tiles as laid out, or every study when the open one is not among them. */
export function weekIndex(rows: StudyRow[], board: StudiesBoard, study: string | null): { rows: StudyRow[]; index: number } {
  const shown = laidOut(rows);
  const all = shown.some((r) => r.study === study) ? shown : laidOut(orderedRows(board, null));
  return { rows: all, index: all.findIndex((r) => r.study === study) };
}

/** The tiles in the order the mosaic lays them out: the large tile first, then the rest. */
export function laidOut(rows: StudyRow[]): StudyRow[] {
  const hero = heroStudy(rows);
  return hero ? [hero, ...rows.filter((r) => r !== hero)] : rows;
}

/** The mosaic: the study running now as the large tile, then the rest in order. */
export function StudyTiles({
  rows,
  nowMs,
  onOpen,
  summaries = null,
}: {
  rows: StudyRow[];
  nowMs: number;
  onOpen: (study: string) => void;
  summaries?: StudyRunSummaries | null;
}) {
  if (rows.length === 0) return <p className="muted">No studies in this group.</p>;
  const hero = heroStudy(rows);
  const figuresOf = (r: StudyRow): TileFigures | null => {
    const id = figuresRun(r)?.runId;
    const run = id ? summaries?.runs[id] : undefined;
    return run ? tileFigures(run) : null;
  };
  return (
    <div className="study-tiles">
      {hero ? <StudyTile key={hero.study} row={hero} nowMs={nowMs} hero figures={figuresOf(hero)} onOpen={() => onOpen(hero.study)} /> : null}
      {rows
        .filter((r) => r !== hero)
        .map((r) => (
          <StudyTile key={r.study} row={r} nowMs={nowMs} figures={figuresOf(r)} onOpen={() => onOpen(r.study)} />
        ))}
    </div>
  );
}

export function StudyCards({ board }: { board: StudiesBoard }) {
  const [params, setParams] = useSearchParams();
  const lightbox = useOpenParam("study");
  const groupParam = params.get("group");
  const week = groupParam === WEEK;
  const group = isGroup(groupParam) ? groupParam : null;
  const digest = useStudyDigest(week ? mondayOf(new Date(etNowMs())) : null);
  const digestRows = digest.data?.ok ? digest.data.data : null;
  // This week: the digest's studies, in its order (newest first); the board's groups otherwise.
  const rows = week
    ? (digestRows?.studies ?? []).map((d) => board.rows.find((r) => r.study === d.study)).filter((r): r is StudyRow => r !== undefined)
    : orderedRows(board, group);
  // One read for the whole board, so picking a group never asks again.
  const summaries = useRunSummaries(figuresRunIds(board.rows));
  // The lightbox steps in the order the tiles are laid out: the large tile first.
  const { rows: stepRows, index } = week ? weekIndex(rows, board, lightbox.open) : openIndex(board, group, lightbox.open);
  return (
    <>
      <GroupChips
        board={board}
        group={group}
        week={week}
        onPick={(g) => {
          const next = new URLSearchParams(params);
          if (g === null) next.delete("group");
          else next.set("group", g);
          setParams(next, { replace: true });
        }}
      />
      {week ? <WeekSummary digest={digest.data} loading={digest.isLoading} onOpen={lightbox.show} /> : null}
      <StudyTiles rows={rows} nowMs={etNowMs()} onOpen={lightbox.show} summaries={summaries.data?.ok ? summaries.data.data : null} />
      {index >= 0 ? (
        <StudyDialog rows={stepRows} index={index} onStep={(to) => lightbox.step(stepRows[to]?.study ?? "")} onClose={lightbox.close} />
      ) : null}
    </>
  );
}
