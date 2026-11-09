import { useState } from "react";
import type { StudyAdviceKind, StudyMeasureKey, StudyReport, StudyReportArm, StudyRow, StudyStat } from "@console/shared";
import { useBridged } from "../../lib/bridged";
import { fmtNum } from "../../lib/format";
import { inline } from "../../lib/miniMarkdown";
import { useMeasure } from "../../lib/useMeasure";
import { Insights, ScorecardTable } from "./Scorecard";

/**
 * A study's Report tab (orchestrator `study-report`): what the figures say, what to do next, the
 * gates in plain words, every arm charted on the measure you pick, and the arm table. Every figure,
 * sentence and piece of advice is the orchestrator's (`studyreport.py`); this draws and prints them
 * and works nothing out, so a reading here can never disagree with the one the CLI gives.
 */

export const ADVICE_WORD: Record<StudyAdviceKind, string> = {
  rerun: "re-run",
  data: "fix data",
  new_study: "new study",
  ruleset: "ruleset",
  reread: "re-read later",
  register: "record it",
};

const WINDOW_FILL: Record<string, string> = { design: "var(--chart-special)", holdout: "var(--chart-orange)" };
const OTHER_FILLS = ["var(--chart-neutral)", "var(--warn)", "var(--pos)"];

export function windowFill(windows: string[], w: string): string {
  return WINDOW_FILL[w] ?? OTHER_FILLS[windows.filter((x) => !(x in WINDOW_FILL)).indexOf(w) % OTHER_FILLS.length] ?? "var(--chart-neutral)";
}

/** The value an arm shows on a measure, as the orchestrator stated it (return on buying power as a %). */
export function measureValue(a: StudyReportArm, key: StudyMeasureKey): number | null {
  switch (key) {
    case "paired":
      return a.paired?.mean ?? null;
    case "paired_both":
      return a.pairedBoth?.mean ?? null;
    case "mean":
      return a.mean;
    case "net_per_trade":
      return a.netPerTrade;
    case "per_risk":
      return a.perRisk === null ? null : a.perRisk * 100;
    case "entry_rate":
      return a.entryRate;
  }
}

/** The row that carries the baseline's own return on buying power. */
export const BASELINE_ROW = "baseline";

/** The baseline's return on buying power in `window`, in %, as the orchestrator stated it beside the arms. */
export function baselineRisk(report: StudyReport, window: string): number | null {
  const base = report.arms.find((a) => a.window === window && a.perRiskBase !== null)?.perRiskBase;
  return base === undefined || base === null ? null : base * 100;
}

export function measureT(a: StudyReportArm, key: StudyMeasureKey | null): number | null {
  if (key === "paired") return a.paired?.t ?? null;
  if (key === "paired_both") return a.pairedBoth?.t ?? null;
  if (key === "mean") return a.t;
  return null;
}

function fmt(v: number | null): string {
  if (v === null) return "–";
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(2) : v.toPrecision(2);
}

const ROW_H = 13;
const PAD = { left: 150, right: 56, top: 6, bottom: 18 };

/** One bar per arm and window on `values`, zero marked, with dashed lines at `refs`. */
export function ArmBars({
  arms,
  windows,
  values,
  refs,
  label,
}: {
  arms: string[];
  windows: string[];
  values: (arm: string, window: string) => number | null;
  refs: number[];
  label: string;
}) {
  const [ref, width] = useMeasure(640);
  const all = arms.flatMap((a) => windows.map((w) => values(a, w))).filter((v): v is number => v !== null);
  const lo = Math.min(0, ...all, ...refs);
  const hi = Math.max(0, ...all, ...refs);
  const span = hi - lo || 1;
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const xOf = (v: number) => PAD.left + ((v - lo) / span) * plotW;
  const group = windows.length * ROW_H + 8;
  const height = PAD.top + arms.length * group + PAD.bottom;
  return (
    <div ref={ref}>
      <svg width="100%" height={height} viewBox={`0 0 ${String(width)} ${String(height)}`} role="img" aria-label={label}>
        {arms.map((a, i) => {
          const y0 = PAD.top + i * group;
          return (
            <g key={a}>
              <text x={PAD.left - 6} y={y0 + (windows.length * ROW_H) / 2 + 4} fontSize={11} fill="currentColor" textAnchor="end">
                {a.length > 24 ? `${a.slice(0, 23)}…` : a}
              </text>
              {windows.map((w, k) => {
                const v = values(a, w);
                if (v === null) return null;
                const x = Math.min(xOf(v), xOf(0));
                return (
                  <rect key={w} x={x} y={y0 + k * ROW_H} width={Math.max(1, Math.abs(xOf(v) - xOf(0)))} height={ROW_H - 2} rx={2} fill={windowFill(windows, w)}>
                    <title>{`${a} · ${w}: ${fmt(v)}`}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}
        <line x1={xOf(0)} x2={xOf(0)} y1={PAD.top} y2={height - PAD.bottom} stroke="currentColor" strokeOpacity={0.5} />
        {refs
          .filter((r) => r !== 0)
          .map((r) => (
            <line key={r} x1={xOf(r)} x2={xOf(r)} y1={PAD.top} y2={height - PAD.bottom} stroke="currentColor" strokeOpacity={0.5} strokeDasharray="4 4" />
          ))}
        <text x={PAD.left} y={height - 4} fontSize={10} fill="currentColor" opacity={0.7}>
          {fmt(lo)}
        </text>
        <text x={PAD.left + plotW} y={height - 4} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
          {fmt(hi)}
        </text>
      </svg>
    </div>
  );
}

function Legend({ windows }: { windows: string[] }) {
  return (
    <div className="muted study-report-legend">
      {windows.map((w) => (
        <span key={w}>
          <i style={{ background: windowFill(windows, w) }} /> {w}
        </span>
      ))}
    </div>
  );
}

function PairCell({ s }: { s: StudyStat | null }) {
  if (s === null) return <>–</>;
  return (
    <>
      <span className={s.mean > 0 ? "pos" : s.mean < 0 ? "neg" : undefined}>{fmt(s.mean)}</span>
      {s.t === null ? null : <span className="muted"> t {fmtNum(s.t)}</span>}
    </>
  );
}

function rate(v: number | null): string {
  if (v === null) return "–";
  return v > 1.05 ? v.toFixed(1) : `${(v * 100).toFixed(0)}%`;
}

export function ReportBody({ report }: { report: StudyReport }) {
  const [picked, setPicked] = useState<StudyMeasureKey | null>(null);
  const measure = report.measures.find((m) => m.key === picked) ?? report.measures[0] ?? null;
  const names = [...new Set(report.arms.map((a) => a.arm))];
  const find = (arm: string, w: string) => report.arms.find((a) => a.arm === arm && a.window === w);
  const shown = measure ? names.filter((a) => report.windows.some((w) => { const r = find(a, w); return r !== undefined && measureValue(r, measure.key) !== null; })) : [];
  const tKey = measure?.t ?? null;
  const hasT = tKey !== null && shown.some((a) => report.windows.some((w) => { const r = find(a, w); return r !== undefined && measureT(r, tKey) !== null; }));
  // Return on buying power: the baseline's own figure differs by window, so it is a row of its own.
  const withBase = measure?.key === "per_risk" && report.windows.some((w) => baselineRisk(report, w) !== null);
  const rows = withBase ? [BASELINE_ROW, ...shown] : shown;
  const valueOf = (a: string, w: string): number | null => {
    if (a === BASELINE_ROW && withBase) return baselineRisk(report, w);
    const r = find(a, w);
    return r && measure ? measureValue(r, measure.key) : null;
  };
  return (
    <div className="study-report">
      <section>
        <h4>What the figures say</h4>
        {report.reading.map((p, i) => (
          <p key={i} className={`study-reading study-reading-${p.kind}`}>
            {inline(p.text)}
          </p>
        ))}
      </section>

      <section>
        <h4>What to do next</h4>
        {report.advice.length === 0 ? (
          <p className="muted">Nothing to act on: the figures prompt no change, re-run or new study.</p>
        ) : (
          <ul className="study-advice">
            {report.advice.map((a, i) => (
              <li key={i}>
                <span className={a.kind === "ruleset" || a.kind === "new_study" ? "chip chip-ok" : "chip chip-warn"}>{ADVICE_WORD[a.kind]}</span> {inline(a.text)}
                <div className="muted">{`Why: ${a.why}.`}</div>
              </li>
            ))}
          </ul>
        )}
        <p className="muted study-report-note">Suggestions only, by fixed rules: nothing is queued, declared or changed from here.</p>
      </section>

      {report.gates.length > 0 ? (
        <section>
          <h4>Gates in this study</h4>
          <p className="muted">A gate is a yes/no check run just before a trade opens. If it says no, the rule sits out. Gated arms have it on, ungated arms don't.</p>
          <dl className="study-gates">
            {report.gates.map((g) => (
              <div key={g.key}>
                <dt>{g.name}</dt>
                <dd>{inline(g.text)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {measure ? (
        <section>
          <h4>Every arm, by window</h4>
          <div className="history-controls" role="group" aria-label="Measure">
            {report.measures.map((m) => (
              <button key={m.key} type="button" className={m.key === measure.key ? "chip chip-ok" : "chip"} aria-pressed={m.key === measure.key} onClick={() => setPicked(m.key)}>
                {m.label}
              </button>
            ))}
          </div>
          <p className="muted">{measure.note}</p>
          <Legend windows={report.windows} />
          <ArmBars arms={rows} windows={report.windows} label={measure.label} refs={[]} values={valueOf} />
          {hasT && tKey !== null ? (
            <>
              <div className="card-subhead">How strong is the evidence: t-statistic, dashed lines at ±2</div>
              <ArmBars arms={shown} windows={report.windows} label={`t-statistic of ${measure.label}`} refs={[-2, 2]} values={(a, w) => { const r = find(a, w); return r ? measureT(r, tKey) : null; }} />
              <p className="muted">Past +2, the average is unlikely to be luck on its own. A study testing many rules at once also corrects for that, so a rule can clear 2 here and still fail.</p>
            </>
          ) : null}
        </section>
      ) : null}

      {report.arms.length > 0 ? (
        <section className="study-report-table">
          <h4>Arm table</h4>
          <table className="data-table">
            <thead>
              <tr>
                <th>arm</th>
                <th>window</th>
                <th>mean</th>
                <th>t</th>
                <th>entries</th>
                <th>per session</th>
                <th>net/trade</th>
                <th>return on BP</th>
                <th>vs baseline</th>
                <th>both entered</th>
                <th>verdict</th>
              </tr>
            </thead>
            <tbody>
              {report.arms.map((a) => (
                <tr key={`${a.arm}|${a.window}|${a.file}`}>
                  <td>{a.arm}</td>
                  <td>{a.window}</td>
                  <td className={a.mean > 0 ? "pos" : a.mean < 0 ? "neg" : undefined}>{fmt(a.mean)}</td>
                  <td>{a.t === null ? "–" : fmtNum(a.t)}</td>
                  <td>{a.n ?? "–"}</td>
                  <td>{rate(a.entryRate)}</td>
                  <td>{a.netPerTrade === null ? "–" : `${a.netPerTradeDerived ? "≈" : ""}${fmt(a.netPerTrade)}`}</td>
                  <td>
                    {a.perRisk === null ? "–" : `${(a.perRisk * 100).toFixed(2)}%`}
                    {a.perRiskBase === null ? null : <span className="muted">{` (${(a.perRiskBase * 100).toFixed(2)}%)`}</span>}
                  </td>
                  <td>
                    <PairCell s={a.paired} />
                    {a.pairedVs ? <div className="muted">vs {a.pairedVs}</div> : null}
                  </td>
                  <td>
                    <PairCell s={a.pairedBoth} />
                  </td>
                  <td>{a.verdict ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">≈ net per trade worked out as net per session × sessions ÷ entries. Return on BP in brackets: the baseline's.</p>
        </section>
      ) : null}
      <StudyScorecards arms={report.arms} />
      {report.provisional ? <p className="muted">Provisional: the replay this study runs on isn't validated yet.</p> : null}
    </div>
  );
}

/** Each window's stats table (arms as columns) and every arm's insights, for arms whose script wrote
 *  a scorecard; nothing for a study written before 2026-11-07. */
export function StudyScorecards({ arms }: { arms: StudyReportArm[] }) {
  const withCard = arms.filter((a) => a.scorecard !== null);
  if (withCard.length === 0) return null;
  const windows = [...new Set(withCard.map((a) => a.window))];
  return (
    <section className="study-report-table">
      {windows.map((w) => {
        const rows = withCard.filter((a) => a.window === w);
        return (
          <div key={w}>
            <h4>Stats{windows.length > 1 ? `: ${w}` : ""}</h4>
            <ScorecardTable cards={Object.fromEntries(rows.map((a) => [a.arm, a.scorecard!]))} />
            {rows.map((a) => (
              <div key={`${a.arm}|${a.file}`}>
                <div className="card-subhead">insights: {a.arm}</div>
                <Insights card={a.scorecard!} />
              </div>
            ))}
          </div>
        );
      })}
    </section>
  );
}

export function ReportTab({ row }: { row: StudyRow }) {
  const url = `/api/backtest/studies/${encodeURIComponent(row.study)}/report`;
  const { data, isLoading, error } = useBridged<StudyReport>(["backtest", "study-report", row.study], url, 300_000);
  if (isLoading) return <p className="muted">Reading the study's result files…</p>;
  if (error) return <p className="muted">The report could not be read: {String(error)}</p>;
  if (!data?.ok || data.data === null) return <p className="muted">The report could not be read: {data?.error ?? "no answer"}</p>;
  return <ReportBody report={data.data} />;
}
