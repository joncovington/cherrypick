import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fmtMoney } from "../../components/DataTable";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { ARM_COLORS, SPOT_COLOR } from "../../components/chart/tokens";
import { niceTicks } from "../../components/chart/scales";
import { minuteOf, hhmm } from "../../components/chart/time";

interface Tick {
  ts: string;
  spot: number | null;
  centers: Record<string, number>;
  settleNow: Record<string, number>;
}

interface Timeline {
  date: string | null;
  arms: string[];
  ticks: Tick[];
  events: Array<{ kind: "entry" | "completion"; ts: string; arm: string; center: number | null; spot: number | null }>;
  spans: Array<{ arm: string; center: number | null; from: string; to: string; latencyMin: number | null }>;
  waiting: Array<{ arm: string; center: number | null; from: string }>;
  feed: Array<{ ts: string; status: string }>;
  feedSummary: { total: number; ok: number; refusals: Record<string, number> };
}

function useTimeline(mode: TradingMode, filter: FliesFilter) {
  return useQuery<Timeline>({
    queryKey: ["flies-timeline", mode, filter.date, filter.symbol, filter.era],
    queryFn: async () => {
      const res = await fetch(`/api/flies/timeline?${fliesQuery(mode, { arm: null, date: filter.date, symbol: filter.symbol, era: filter.era })}`);
      if (!res.ok) throw new Error(`timeline: HTTP ${res.status}`);
      return (await res.json()) as Timeline;
    },
    refetchInterval: 30_000,
  });
}

/**
 * The session timeline — how the day actually went. Top panel: spot, each
 * arm's wanted centre as a step line, leg-ins as bars running to completion
 * (dashed to the right edge while still waiting), ○ entries and ◆
 * completions. Bottom panel: the book replayed at each tick — settled if the
 * day ended here. Gaps in the record are shaded and named, never interpolated.
 * X axis runs from the first recorded activity (the entry window) to the
 * 16:00 close.
 */
export function TimelineCard({ mode, filter, arm }: { mode: TradingMode; filter: FliesFilter; arm: string | null }) {
  const { data, isLoading } = useTimeline(mode, filter);
  const [hover, setHover] = useState<number | null>(null);

  const arms = data?.arms ?? [];
  const shown = arm !== null ? arms.filter((a) => a === arm) : arms;
  const ticks = (data?.ticks ?? []).filter((t) => t.spot !== null);

  const width = 1150;
  const height = 430;
  const pad = { l: 62, r: 12, t: 12, b: 24 };
  const splitGap = 26;
  const priceBot = pad.t + (height - pad.t - pad.b - splitGap) * 0.62;
  const pnlTop = priceBot + splitGap;

  let body = null;
  if (ticks.length > 0 && data !== undefined) {
    const mins = ticks.map((t) => minuteOf(t.ts));
    // Session start = the entry window (first recorded activity, floored to
    // 10 min), end = the 16:00 close.
    const firstActivity = Math.min(mins[0]!, ...data.events.map((e) => minuteOf(e.ts)), 16 * 60);
    const tMin = Math.floor(firstActivity / 10) * 10;
    const tMax = 16 * 60;
    const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin || 1)) * (width - pad.l - pad.r);

    // Break lines across recording gaps instead of interpolating a calm shape.
    const steps = mins
      .slice(1)
      .map((m, i) => m - mins[i]!)
      .filter((d) => d > 0)
      .sort((a, b) => a - b);
    const median = steps.length > 0 ? steps[Math.floor(steps.length / 2)]! : 0;
    const gapLimit = Math.max(median * 3, 5);
    const isGap = (i: number) => i > 0 && mins[i]! - mins[i - 1]! > gapLimit;
    const feedMins = data.feed.map((f) => ({ m: minuteOf(f.ts), status: f.status }));
    const gapReason = (a: number, b: number): string => {
      const refused = feedMins.filter((f) => f.m > a && f.m < b && f.status !== "ok");
      if (refused.length === 0) return "loop silent";
      const counts: Record<string, number> = {};
      for (const f of refused) counts[f.status] = (counts[f.status] ?? 0) + 1;
      const top = Object.entries(counts).sort((x, y) => y[1] - x[1])[0]!;
      return `${top[0]} ×${top[1]}`;
    };

    // Price panel scale: spot + every centre a shown arm asked for + held structures.
    let pMin = Infinity;
    let pMax = -Infinity;
    for (const [i, t] of ticks.entries()) {
      void i;
      pMin = Math.min(pMin, t.spot!);
      pMax = Math.max(pMax, t.spot!);
      for (const a of shown) {
        const c = t.centers[a];
        if (c !== undefined) {
          pMin = Math.min(pMin, c);
          pMax = Math.max(pMax, c);
        }
      }
    }
    for (const e of [...data.events, ...data.waiting].filter((e) => shown.includes(e.arm))) {
      if (e.center !== null) {
        pMin = Math.min(pMin, e.center);
        pMax = Math.max(pMax, e.center);
      }
    }
    const pSpan = pMax - pMin || 1;
    pMin -= pSpan * 0.12;
    pMax += pSpan * 0.12;
    const PY = (v: number) => priceBot - ((v - pMin) / (pMax - pMin || 1)) * (priceBot - pad.t);

    // P&L panel scale.
    let vMin = 0;
    let vMax = 0;
    for (const t of ticks)
      for (const a of shown) {
        const v = t.settleNow[a];
        if (v !== undefined) {
          vMin = Math.min(vMin, v);
          vMax = Math.max(vMax, v);
        }
      }
    const vSpan = vMax - vMin || 1;
    vMin -= vSpan * 0.15;
    vMax += vSpan * 0.15;
    const VY = (v: number) => height - pad.b - ((v - vMin) / (vMax - vMin || 1)) * (height - pad.b - pnlTop);

    const colorOf = (a: string) => ARM_COLORS[arms.indexOf(a) % ARM_COLORS.length]!;

    // Step-line points per arm (broken at gaps).
    const centreSteps = (a: string): string[] => {
      const segs: string[] = [];
      let cur: string[] = [];
      let prevY: number | null = null;
      ticks.forEach((t, i) => {
        const c = t.centers[a];
        if (c === undefined) return;
        const x = X(mins[i]!);
        const y = PY(c);
        if (cur.length === 0 || isGap(i)) {
          if (cur.length > 1) segs.push(cur.join(" "));
          cur = [`${x.toFixed(1)},${y.toFixed(1)}`];
        } else {
          cur.push(`${x.toFixed(1)},${prevY!.toFixed(1)}`, `${x.toFixed(1)},${y.toFixed(1)}`);
        }
        prevY = y;
      });
      if (cur.length > 1) segs.push(cur.join(" "));
      return segs;
    };

    const spotSegs: string[] = [];
    {
      let cur: string[] = [];
      ticks.forEach((t, i) => {
        const pt = `${X(mins[i]!).toFixed(1)},${PY(t.spot!).toFixed(1)}`;
        if (cur.length > 0 && isGap(i)) {
          spotSegs.push(cur.join(" "));
          cur = [];
        }
        cur.push(pt);
      });
      if (cur.length > 1) spotSegs.push(cur.join(" "));
    }

    // The replayed book: fills per zero-crossing run when a single arm is shown.
    const pnlFills: Array<{ pts: string; pos: boolean }> = [];
    const pnlLines: Array<{ arm: string; segs: string[] }> = [];
    for (const a of shown) {
      const pts = ticks
        .map((t, i) => ({ m: mins[i]!, v: t.settleNow[a], gap: isGap(i) }))
        .filter((p): p is { m: number; v: number; gap: boolean } => p.v !== undefined);
      if (pts.length === 0) continue;
      if (shown.length === 1) {
        let run: Array<{ m: number; v: number }> = [];
        let runSign: number | null = null;
        const flush = () => {
          if (run.length === 0) return;
          const poly = [
            `${X(run[0]!.m).toFixed(1)},${VY(0).toFixed(1)}`,
            ...run.map((p) => `${X(p.m).toFixed(1)},${VY(p.v).toFixed(1)}`),
            `${X(run[run.length - 1]!.m).toFixed(1)},${VY(0).toFixed(1)}`,
          ].join(" ");
          pnlFills.push({ pts: poly, pos: runSign! >= 0 });
          run = [];
        };
        pts.forEach((p, i) => {
          const sign = p.v >= 0 ? 1 : -1;
          if (runSign === null) {
            runSign = sign;
            run.push(p);
            return;
          }
          if (sign === runSign) {
            run.push(p);
            return;
          }
          const prev = pts[i - 1]!;
          const t = prev.v / (prev.v - p.v);
          const cross = { m: prev.m + (p.m - prev.m) * t, v: 0 };
          run.push(cross);
          flush();
          runSign = sign;
          run.push(cross, p);
        });
        flush();
      }
      const segs: string[] = [];
      let cur: string[] = [];
      for (const p of pts) {
        const pt = `${X(p.m).toFixed(1)},${VY(p.v).toFixed(1)}`;
        if (cur.length > 0 && p.gap) {
          segs.push(cur.join(" "));
          cur = [];
        }
        cur.push(pt);
      }
      if (cur.length > 1) segs.push(cur.join(" "));
      pnlLines.push({ arm: a, segs });
    }

    const hoverIdx =
      hover !== null
        ? mins.reduce((best, m, j) => (Math.abs(X(m) - hover) < Math.abs(X(mins[best]!) - hover) ? j : best), 0)
        : null;

    body = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="session timeline"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const fx = ((e.clientX - rect.left) / rect.width) * width;
          setHover(fx >= pad.l && fx <= width - pad.r ? fx : null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {/* grids */}
        {niceTicks(pMin, pMax, 4).map((v) => (
          <g key={`p${v}`}>
            <line x1={pad.l} y1={PY(v)} x2={width - pad.r} y2={PY(v)} stroke="#15181e" />
            <text x={4} y={PY(v) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{v.toFixed(0)}</text>
          </g>
        ))}
        {niceTicks(vMin, vMax, 3).map((v) => (
          <g key={`v${v}`}>
            <line x1={pad.l} y1={VY(v)} x2={width - pad.r} y2={VY(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
            <text x={4} y={VY(v) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">{fmtMoney(v)}</text>
          </g>
        ))}
        {niceTicks(tMin, tMax, 7).map((m) => (
          <g key={`t${m}`}>
            <line x1={X(m)} y1={pad.t} x2={X(m)} y2={height - pad.b} stroke="#15181e" />
            <text x={X(m)} y={height - 7} fontSize={9} fill="#82878f" textAnchor="middle" fontFamily="Consolas, monospace">{hhmm(m)}</text>
          </g>
        ))}
        <text x={pad.l + 4} y={pnlTop - 7} fontSize={9} fill="#82878f">settled if the day ended here</text>

        {/* gap shading with reasons */}
        {ticks.map((t, i) => {
          if (!isGap(i)) return null;
          const x0 = X(mins[i - 1]!);
          const x1 = X(mins[i]!);
          const lines = [`no data · ${Math.round(mins[i]! - mins[i - 1]!)}m`, gapReason(mins[i - 1]!, mins[i]!)];
          return (
            <g key={`gap${i}`}>
              <rect x={x0} y={pad.t} width={x1 - x0} height={height - pad.b - pad.t} fill="rgba(217, 161, 59, 0.07)" />
              {lines.map((s, k) =>
                x1 - x0 > s.length * 5.5 + 6 ? (
                  <text key={k} x={(x0 + x1) / 2} y={pad.t + 11 + k * 12} fontSize={9} fill="rgba(217, 161, 59, 0.8)" textAnchor="middle">
                    {s}
                  </text>
                ) : null,
              )}
            </g>
          );
        })}

        {/* wanted centres (step lines, half-faded), then spot on top */}
        {shown.map((a) =>
          centreSteps(a).map((seg, k) => (
            <polyline key={`${a}${k}`} points={seg} fill="none" stroke={colorOf(a)} strokeWidth={1.3} opacity={0.6} />
          )),
        )}
        {spotSegs.map((seg, k) => (
          <polyline key={`spot${k}`} points={seg} fill="none" stroke={SPOT_COLOR} strokeWidth={1.8} />
        ))}

        {/* leg-in → completion bars; waiting spreads dashed to the right edge */}
        {data.spans
          .filter((s) => shown.includes(s.arm) && s.center !== null)
          .map((s, k) => (
            <line
              key={`span${k}`}
              x1={X(minuteOf(s.from))}
              y1={PY(s.center!)}
              x2={Math.max(X(minuteOf(s.to)), X(minuteOf(s.from)) + 2)}
              y2={PY(s.center!)}
              stroke={colorOf(s.arm)}
              strokeWidth={5}
              opacity={0.3}
            />
          ))}
        {data.waiting
          .filter((s) => shown.includes(s.arm) && s.center !== null)
          .map((s, k) => (
            <line
              key={`wait${k}`}
              x1={X(minuteOf(s.from))}
              y1={PY(s.center!)}
              x2={width - pad.r}
              y2={PY(s.center!)}
              stroke={colorOf(s.arm)}
              strokeWidth={5}
              opacity={0.3}
              strokeDasharray="7 5"
            />
          ))}

        {/* ○ entries, ◆ completions */}
        {data.events
          .filter((e) => shown.includes(e.arm))
          .map((e, k) => {
            const x = X(minuteOf(e.ts));
            if (e.kind === "entry") {
              const y = PY(e.spot ?? e.center ?? pMin);
              return <circle key={k} cx={x} cy={y} r={4} fill="none" stroke={colorOf(e.arm)} strokeWidth={1.6} />;
            }
            const y = PY(e.center ?? pMin);
            return <polygon key={k} points={`${x},${y - 5} ${x + 5},${y} ${x},${y + 5} ${x - 5},${y}`} fill={colorOf(e.arm)} />;
          })}

        {/* the replayed book */}
        {pnlFills.map((f, k) => (
          <polygon key={`fill${k}`} points={f.pts} fill={f.pos ? "rgba(67, 181, 122, 0.22)" : "rgba(217, 92, 74, 0.2)"} />
        ))}
        {pnlLines.map((l) =>
          l.segs.map((seg, k) => (
            <polyline key={`${l.arm}${k}`} points={seg} fill="none" stroke={colorOf(l.arm)} strokeWidth={1.5} />
          )),
        )}

        {/* hover crosshair + readout */}
        {hoverIdx !== null && (() => {
          const t = ticks[hoverIdx]!;
          const x = X(mins[hoverIdx]!);
          const lines = [
            `${hhmm(mins[hoverIdx]!)}   spot ${t.spot!.toFixed(2)}`,
            ...shown.map(
              (a) =>
                `${a}  centre ${t.centers[a] !== undefined ? t.centers[a]!.toFixed(0) : "–"}  ${t.settleNow[a] !== undefined ? fmtMoney(t.settleNow[a]!) : "–"}`,
            ),
          ];
          const bw = Math.max(...lines.map((l) => l.length)) * 5.8 + 12;
          const bx = Math.min(Math.max(x + 12, 4), width - bw - 4);
          return (
            <>
              <line x1={x} y1={pad.t} x2={x} y2={height - pad.b} stroke="#3d4653" />
              <rect x={bx} y={8} width={bw} height={lines.length * 12 + 8} rx={5} fill="#101216f0" stroke="#2a2f3a" />
              {lines.map((l, i) => (
                <text key={i} x={bx + 6} y={20 + i * 12} fontSize={9.5} fill={i === 0 ? "#eceff3" : colorOf(shown[i - 1]!)} fontFamily="Consolas, monospace">
                  {l}
                </text>
              ))}
            </>
          );
        })()}
      </svg>
    );
  }

  const refusalNote =
    data !== undefined && Object.keys(data.feedSummary.refusals).length > 0
      ? " · refused " +
        Object.entries(data.feedSummary.refusals)
          .map(([k, v]) => `${k} ×${v}`)
          .join(", ")
      : "";

  return (
    <section className="card">
      <h2>Session timeline — how the day actually went{data?.date !== null && data !== undefined ? ` (${data.date})` : ""}</h2>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : ticks.length === 0 ? (
        <p className="muted">no iterations recorded for this day</p>
      ) : (
        <>
          {body}
          <div className="forest-legend">
            <span><i style={{ background: SPOT_COLOR }} /> spot</span>
            {shown.map((a) => (
              <span key={a}>
                <i style={{ background: ARM_COLORS[arms.indexOf(a) % ARM_COLORS.length] }} /> {a} — wanted centre
              </span>
            ))}
            <span className="muted">○ credit spread sold · ◆ completed into a fly · ▬ solid bar = time to complete, dashed = still waiting</span>
          </div>
          {data !== undefined && data.feedSummary.total > 0 && (
            <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
              Feed: {data.feedSummary.ok}/{data.feedSummary.total} ticks built a snapshot (
              {Math.round((data.feedSummary.ok / data.feedSummary.total) * 100)}%){refusalNote}
            </p>
          )}
        </>
      )}
    </section>
  );
}
