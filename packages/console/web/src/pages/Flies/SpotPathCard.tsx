import type { TradingMode } from "@console/shared";
import type { FliesFilter } from "../../lib/api";
import { GridCard } from "../../components/grid/GridCard";
import { ARM_COLORS, SPOT_COLOR } from "../../components/chart/tokens";
import { niceTicks } from "../../components/chart/scales";
import { minuteOf, hhmm } from "../../components/chart/time";
import { useTimeline } from "./TimelineCard";

/**
 * The session's spot path with every entry (○) and completion (◆) on it, for the session page.
 *
 * A compact read of the timeline tab's top panel, off the same endpoint and query key, so the two
 * never disagree and opening the tab costs no second fetch. It answers the one question the payoff
 * chart beside it cannot: how the day unfolded to get there. Everything else the timeline draws
 * (wanted centres, leg-in bars, the replayed book, named gaps) stays on the tab it links to; a gap in
 * the record still breaks the line here rather than being drawn as a calm stretch.
 */
export function SpotPathCard({ mode, filter, arm }: { mode: TradingMode; filter: FliesFilter; arm: string | null }) {
  const { data, isLoading } = useTimeline(mode, filter);
  const ticks = (data?.ticks ?? []).filter((t) => t.spot !== null);
  const arms = data?.arms ?? [];
  const events = (data?.events ?? []).filter((e) => (arm === null || e.arm === arm) && e.spot !== null);

  const width = 760;
  const height = 236;
  const pad = { l: 44, r: 8, t: 8, b: 20 };

  let body = null;
  if (ticks.length > 0) {
    const mins = ticks.map((t) => minuteOf(t.ts));
    const first = Math.min(mins[0]!, ...events.map((e) => minuteOf(e.ts)));
    const tMin = Math.floor(first / 30) * 30;
    const tMax = 16 * 60;
    const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin || 1)) * (width - pad.l - pad.r);

    const spots = ticks.map((t) => t.spot!);
    const lo = Math.min(...spots, ...events.map((e) => e.spot!));
    const hi = Math.max(...spots, ...events.map((e) => e.spot!));
    const span = hi - lo || 1;
    const yMin = lo - span * 0.08;
    const yMax = hi + span * 0.08;
    const Y = (v: number) => height - pad.b - ((v - yMin) / (yMax - yMin)) * (height - pad.b - pad.t);

    // Same gap rule as the timeline: a step over three times the median cadence breaks the line.
    const steps = mins.slice(1).map((m, i) => m - mins[i]!).filter((d) => d > 0).sort((a, b) => a - b);
    const gapLimit = Math.max((steps[Math.floor(steps.length / 2)] ?? 0) * 3, 5);
    const segs: string[] = [];
    let cur: string[] = [];
    ticks.forEach((t, i) => {
      if (i > 0 && mins[i]! - mins[i - 1]! > gapLimit && cur.length > 0) {
        segs.push(cur.join(" "));
        cur = [];
      }
      cur.push(`${X(mins[i]!).toFixed(1)},${Y(t.spot!).toFixed(1)}`);
    });
    if (cur.length > 1) segs.push(cur.join(" "));

    const colorOf = (a: string) => ARM_COLORS[Math.max(0, arms.indexOf(a)) % ARM_COLORS.length]!;
    const hours: number[] = [];
    for (let m = Math.ceil(tMin / 60) * 60; m <= tMax; m += 60) hours.push(m);

    body = (
      <svg viewBox={`0 0 ${String(width)} ${String(height)}`} width="100%" role="img" aria-label="spot path with entries and completions">
        {niceTicks(yMin, yMax, 4).map((v) => (
          <g key={v}>
            <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke="#15181e" />
            <text x={4} y={Y(v) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{v.toFixed(0)}</text>
          </g>
        ))}
        {hours.map((m) => (
          <text key={m} x={X(m)} y={height - 6} fontSize={9} fill="#82878f" textAnchor="middle" fontFamily="Consolas, monospace">
            {hhmm(m)}
          </text>
        ))}
        {segs.map((s, k) => (
          <polyline key={k} points={s} fill="none" stroke={SPOT_COLOR} strokeWidth={1.6} />
        ))}
        {events.map((e, k) => {
          const x = X(minuteOf(e.ts));
          const y = Y(e.spot!);
          const title = `${e.arm} ${e.kind} at ${e.ts.slice(11, 16)}${e.center !== null ? ` · centre ${e.center.toFixed(0)}` : ""}`;
          return e.kind === "entry" ? (
            <circle key={k} cx={x} cy={y} r={3.5} fill="none" stroke={colorOf(e.arm)} strokeWidth={1.5}>
              <title>{title}</title>
            </circle>
          ) : (
            <path key={k} d={`M${x} ${y - 4.5}L${x + 4.5} ${y}L${x} ${y + 4.5}L${x - 4.5} ${y}Z`} fill={colorOf(e.arm)}>
              <title>{title}</title>
            </path>
          );
        })}
      </svg>
    );
  }

  const entries = events.filter((e) => e.kind === "entry").length;
  const completions = events.filter((e) => e.kind === "completion").length;
  return (
    <GridCard
      label="spot path"
      span={8}
      h={304}
      to="/flies/timeline"
      toLabel="the full session timeline"
      foot={
        data === undefined
          ? isLoading ? "reading…" : "—"
          : `${data.date ?? "no session"} · ○ ${String(entries)} entries · ◆ ${String(completions)} completions${arm !== null ? ` · ${arm}` : ""}`
      }
    >
      {body ?? <p className="muted">{isLoading ? "reading…" : "no spot recorded for this session"}</p>}
    </GridCard>
  );
}
