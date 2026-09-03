import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { timeTicks } from "../../components/chart/scales";
import { minuteOf, hhmm } from "../../components/chart/time";
import { HoverReadout } from "../../components/chart/Tooltip";

interface JournalRow {
  arm: string;
  mode: string;
  reason: string;
  accepted: boolean;
  firstSeen: string | null;
  lastSeen: string | null;
  occurrences: number;
  centerLast: number | null;
  detail: string | null;
}

function useJournal(mode: TradingMode, filter: FliesFilter) {
  return useQuery<{ date: string | null; rows: JournalRow[] }>({
    queryKey: ["flies-journal", mode, filter],
    queryFn: async () => {
      const res = await fetch(`/api/flies/journal?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`journal: HTTP ${res.status}`);
      return (await res.json()) as { date: string | null; rows: JournalRow[] };
    },
    refetchInterval: 30_000,
  });
}


/**
 * The decision journal: a Gantt strip of collapsed runs (one lane per
 * arm·mode; a refusal that held all morning IS an interval, drawn as one
 * bar whose length says how long) over the day's clock, then the table.
 * Green = entry taken; translucent red = refused.
 */
export function JournalCard({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const { data, isLoading } = useJournal(mode, filter);
  const [hover, setHover] = useState<number | null>(null);

  const rows = data?.rows ?? [];
  const spanRows = rows.filter((r) => r.firstSeen !== null && r.lastSeen !== null);
  const lanes = [...new Set(spanRows.map((r) => `${r.arm}|${r.mode}`))].sort();

  const width = 1150;
  const pad = { l: 150, r: 12, t: 6, b: 18 };
  const laneH = 22;
  const height = Math.max(56, lanes.length * laneH + pad.t + pad.b);

  let gantt = null;
  if (spanRows.length > 0) {
    const times = spanRows.flatMap((r) => [minuteOf(r.firstSeen!), minuteOf(r.lastSeen!)]);
    const tMin = Math.min(...times);
    let tMax = Math.max(...times);
    if (tMax - tMin < 1) tMax = tMin + 1;
    const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin)) * (width - pad.l - pad.r);

    const bars = spanRows.map((r, i) => {
      const lane = lanes.indexOf(`${r.arm}|${r.mode}`);
      const cy = pad.t + lane * laneH + laneH / 2;
      const x0 = X(minuteOf(r.firstSeen!));
      const bw = Math.max(X(minuteOf(r.lastSeen!)) - x0, 3);
      return { i, r, x0, bw, cy };
    });
    const hovered = hover !== null ? bars[hover] : undefined;

    gantt = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="decision journal"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseLeave={() => setHover(null)}
      >
        {timeTicks(tMin, tMax, 8).map((m) => (
          <g key={m}>
            <line x1={X(m)} y1={pad.t} x2={X(m)} y2={height - pad.b} stroke="#15181e" />
            <text x={X(m)} y={height - 5} fontSize={9} fill="#82878f" textAnchor="middle" fontFamily="Consolas, monospace">
              {hhmm(m)}
            </text>
          </g>
        ))}
        {lanes.map((lane, li) => {
          const [arm, dmode] = lane.split("|");
          return (
            <text key={lane} x={4} y={pad.t + li * laneH + laneH / 2 + 3} fontSize={10} fill="#82878f">
              {arm} · {dmode}
            </text>
          );
        })}
        {bars.map((b) => (
          <rect
            key={b.i}
            x={b.x0}
            y={b.cy - 6}
            width={b.bw}
            height={12}
            fill={b.r.accepted ? "#43b57a" : "rgba(217, 92, 74, 0.5)"}
            onMouseEnter={() => setHover(b.i)}
          />
        ))}
        {hovered !== undefined && (() => {
          const r = hovered.r;
          const lines = [
            `${r.arm} · ${r.mode}`,
            `${r.accepted ? "✓ " : ""}${r.reason}`,
            `${r.firstSeen!.slice(11, 16)}–${r.lastSeen!.slice(11, 16)} · ${r.occurrences}× seen`,
          ];
          return <HoverReadout x={hovered.x0} width={width} lines={lines} lineColor={() => "#eceff3"} boxTop={4} />;
        })()}
      </svg>
    );
  }

  return (
    <section className="card">
      <h2>Decision journal{data?.date !== null && data !== undefined ? ` (${data.date})` : ""}</h2>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : rows.length === 0 ? (
        <p className="muted">no decisions recorded for this day</p>
      ) : (
        <>
          {gantt}
          <div className="forest-legend">
            <span><i style={{ background: "#43b57a" }} /> entry taken</span>
            <span><i style={{ background: "rgba(217, 92, 74, 0.6)" }} /> refused (bar spans how long)</span>
          </div>
          <div className="table-scroll" style={{ marginTop: "0.6rem", maxHeight: "18rem", overflowY: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>arm</th><th>mode</th><th>decision</th><th>consecutive rejections</th>
                  <th>from</th><th>to</th><th>centre</th><th>detail</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.arm}</td>
                    <td className="muted">{r.mode}</td>
                    <td>
                      {r.accepted ? <span className="chain-badge chain-badge-long">{r.reason}</span> : r.reason}
                    </td>
                    <td>{r.occurrences}</td>
                    <td className="muted">{r.firstSeen?.slice(11, 16) ?? "—"}</td>
                    <td className="muted">{r.lastSeen?.slice(11, 16) ?? "—"}</td>
                    <td>{r.centerLast !== null ? r.centerLast.toFixed(0) : "–"}</td>
                    <td className="muted" style={{ whiteSpace: "normal" }}>{r.detail ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
