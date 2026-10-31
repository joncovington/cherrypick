import type { StudyGrid } from "@console/shared";
import { fmtMoney } from "../../lib/format";
import { useMeasure } from "../../lib/useMeasure";

/**
 * A grid study's settings side by side (packages/backtest `grid.block`, 2026-10-31): one bar per
 * tested setting, its height the arm's own mean net per session, the live setting outlined. The
 * caption carries the study's own neighbourhood label for the live setting and each passing one.
 * Every figure and word is the study's; this draws them and works nothing out.
 */

/** The neighbourhood label in words. */
export const NEIGHBOURHOOD_WORD: Record<string, string> = {
  plateau: "on a plateau",
  lone_peak: "a lone peak",
  not_positive: "not positive",
  neighbour_better: "a neighbour does better",
  no_neighbours: "no neighbours to compare",
  unmeasured: "unmeasured",
};

/** "live 0.16: on a plateau · d20 (pass): a lone peak". */
export function neighbourhoodLine(grid: StudyGrid): string {
  return Object.entries(grid.neighbourhood)
    .map(([arm, word]) => {
      const row = grid.rows.find((r) => r.arm === arm);
      const who = arm === grid.declared ? `live ${row?.label ?? arm}` : `${row?.label ?? arm} (pass)`;
      return `${who}: ${NEIGHBOURHOOD_WORD[word] ?? word.replace(/_/g, " ")}`;
    })
    .join(" · ");
}

const HEIGHT = 140;
const PAD = { left: 44, right: 8, top: 10, bottom: 24 };

export function GridBars({ grid }: { grid: StudyGrid }) {
  const [ref, width] = useMeasure(480);
  const means = grid.rows.map((r) => r.mean ?? 0);
  const top = Math.max(0, ...means);
  const bottom = Math.min(0, ...means);
  const span = top - bottom || 1;
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const yOf = (v: number) => PAD.top + ((top - v) / span) * plotH;
  const slot = plotW / grid.rows.length;
  const barW = Math.min(48, Math.max(6, slot - 8));
  return (
    <div ref={ref} style={{ marginTop: "0.4rem" }}>
      <div className="card-subhead">
        {grid.parameter} · {grid.measure}
      </div>
      <svg width="100%" height={HEIGHT} viewBox={`0 0 ${String(width)} ${String(HEIGHT)}`} role="img" aria-label={`${grid.measure} by ${grid.parameter}`}>
        {grid.rows.map((r, i) => {
          const x = PAD.left + i * slot + (slot - barW) / 2;
          const v = r.mean ?? 0;
          const y = Math.min(yOf(v), yOf(0));
          const h = Math.max(r.mean === null ? 0 : 1, Math.abs(yOf(v) - yOf(0)));
          const tip = `${grid.parameter} ${r.label}${r.declared ? " (live)" : ""}: ${r.mean === null ? "not measured" : fmtMoney(r.mean)}${r.n === null ? "" : ` over ${String(r.n)} sessions`}${r.verdict ? ` · ${r.verdict}` : ""}`;
          return (
            <g key={r.arm}>
              <rect
                x={x}
                y={y}
                width={barW}
                height={h}
                rx={2}
                fill={v >= 0 ? "var(--pos)" : "var(--neg)"}
                opacity={r.verdict === "pass" || r.declared ? 0.9 : 0.55}
                stroke={r.declared ? "currentColor" : "none"}
                strokeWidth={2}
              >
                <title>{tip}</title>
              </rect>
              <text x={x + barW / 2} y={HEIGHT - 8} fontSize={10} fill="currentColor" opacity={r.declared ? 1 : 0.7} fontWeight={r.declared ? 700 : 400} textAnchor="middle">
                {r.label}
              </text>
            </g>
          );
        })}
        <line x1={PAD.left} x2={PAD.left + plotW} y1={yOf(0)} y2={yOf(0)} stroke="currentColor" strokeOpacity={0.4} />
        <text x={PAD.left - 4} y={PAD.top + 8} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
          {fmtMoney(top)}
        </text>
        {bottom < 0 ? (
          <text x={PAD.left - 4} y={PAD.top + plotH} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
            {fmtMoney(bottom)}
          </text>
        ) : null}
      </svg>
      <div className="muted" style={{ fontSize: "0.85em" }}>
        {neighbourhoodLine(grid)}. Description only: the verdicts above stand as the study wrote them.
      </div>
    </div>
  );
}
