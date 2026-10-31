import { useState } from "react";
import type { BacktestPathPoint, BacktestTradePath } from "@console/shared";
import { useBridged } from "../../lib/bridged";
import { fmtCash } from "../../lib/format";

/**
 * One MEIC condor through its session (`read path`, `tradepath.py`, 2026-10-31): the position's P&L
 * at mid at each stored sample, and SPX against the four strikes beneath it, on one time axis. Each
 * side's exit is a dashed line. A sample missing a leg's quote breaks the P&L line: the store keeps a
 * band of strikes round spot, and a far leg can leave it. Every figure is the module's ledger and the
 * store's quotes; the only thing done here is drawing.
 */

/** The P&L polyline split at gaps: one run of [x, y] per unbroken stretch. Pure. */
export function segments(points: BacktestPathPoint[], pick: (p: BacktestPathPoint) => number | null): [number, number][][] {
  const out: [number, number][][] = [];
  let cur: [number, number][] = [];
  points.forEach((p, i) => {
    const v = pick(p);
    if (v === null) {
      if (cur.length > 0) out.push(cur);
      cur = [];
    } else cur.push([i, v]);
  });
  if (cur.length > 0) out.push(cur);
  return out;
}

const W = 720;
const M = { l: 56, r: 12, t: 10, b: 20 };

function Panel({
  points,
  pick,
  height,
  label,
  lines = [],
  exits,
  colour,
  fmt,
  hover,
  onHover,
}: {
  points: BacktestPathPoint[];
  pick: (p: BacktestPathPoint) => number | null;
  height: number;
  label: string;
  lines?: { v: number; text: string; side?: "left" | "right" }[];
  exits: { i: number; text: string }[];
  colour: string;
  fmt: (v: number) => string;
  hover: number | null;
  onHover: (i: number | null) => void;
}) {
  const vals = [...points.map(pick).filter((v): v is number => v !== null), ...lines.map((l) => l.v)];
  if (vals.length === 0) return <p className="muted">No {label} recorded.</p>;
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const X = (i: number) => M.l + (i * (W - M.l - M.r)) / Math.max(points.length - 1, 1);
  const Y = (v: number) => M.t + (1 - (v - lo) / span) * (height - M.t - M.b);
  const step = (W - M.l - M.r) / Math.max(points.length, 1);
  return (
    <svg viewBox={`0 0 ${String(W)} ${String(height)}`} role="img" aria-label={label} style={{ width: "100%", height: "auto", display: "block" }}>
      {lines.map((l) => (
        <g key={l.text}>
          <line x1={M.l} x2={W - M.r} y1={Y(l.v)} y2={Y(l.v)} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="4 3" />
          {/* A wing's two strikes sit close: one label each end so they never overlap. */}
          <text
            x={l.side === "left" ? M.l + 4 : W - M.r}
            y={Y(l.v) - 3}
            fontSize={10}
            fill="currentColor"
            opacity={0.6}
            textAnchor={l.side === "left" ? "start" : "end"}
          >
            {l.text}
          </text>
        </g>
      ))}
      {exits.map((e) => (
        <g key={e.text}>
          <line x1={X(e.i)} x2={X(e.i)} y1={M.t} y2={height - M.b} stroke="currentColor" strokeOpacity={0.5} strokeDasharray="2 3" />
          <text x={X(e.i) - 3} y={M.t + 9} fontSize={10} fill="currentColor" opacity={0.75} textAnchor="end">
            {e.text}
          </text>
        </g>
      ))}
      {segments(points, pick).map((seg) => (
        <polyline
          key={seg[0]![0]}
          fill="none"
          stroke={colour}
          strokeWidth={2}
          points={seg.map(([i, v]) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}
        />
      ))}
      {hover !== null && <line x1={X(hover)} x2={X(hover)} y1={M.t} y2={height - M.b} stroke="currentColor" strokeOpacity={0.6} />}
      <text x={M.l - 4} y={M.t + 8} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
        {fmt(hi)}
      </text>
      <text x={M.l - 4} y={height - M.b} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
        {fmt(lo)}
      </text>
      <text x={M.l} y={height - 5} fontSize={10} fill="currentColor" opacity={0.7}>
        {points[0]?.time}
      </text>
      <text x={W - M.r} y={height - 5} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
        {points[points.length - 1]?.time}
      </text>
      {/* Hit targets wider than the line: one column per sample. */}
      {points.map((p, i) => (
        <rect key={p.ts} x={X(i) - step / 2} y={0} width={step} height={height} fill="transparent" onMouseEnter={() => onHover(i)} onMouseLeave={() => onHover(null)} />
      ))}
    </svg>
  );
}

/** "14:02": an exit stamp's clock time, ET as the ledger writes it. */
function clockOf(stamp: string | null): string {
  return stamp === null ? "" : stamp.slice(11, 16);
}

export function TradePathChart({ path }: { path: BacktestTradePath }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = path.points;
  if (pts.length === 0) return <p className="muted">No stored samples between this trade's entry and its exit.</p>;
  const exits = path.exits
    .map((e) => ({ i: pts.findIndex((p) => p.time >= clockOf(e.time)), text: `${e.side} ${e.reason ?? "exit"}` }))
    .filter((e) => e.i >= 0);
  const h = hover === null ? null : pts[hover]!;
  const [lp, sp] = path.strikes.put;
  const [sc, lc] = path.strikes.call;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="card-subhead">
        {path.session} · {path.position_id} · P&amp;L at mid, before any fill model · quotes from {path.store}
      </div>
      <div className="muted" style={{ fontSize: 12, minHeight: "1.3em" }}>
        {h === null
          ? `${String(pts.length)} samples${path.gaps > 0 ? `, ${String(path.gaps)} with a leg outside the stored strikes (the line breaks there)` : ""}. Hover for a minute's values.`
          : `${h.time} · P&L ${h.pnl === null ? "a leg not stored" : fmtCash(h.pnl)} · put side ${h.put_mark ?? "—"} · call side ${h.call_mark ?? "—"} · SPX ${h.spot ?? "—"} · VIX ${h.vix ?? "—"}`}
      </div>
      <Panel points={pts} pick={(p) => p.pnl} height={150} label="position P&L" lines={[{ v: 0, text: "$0" }]} exits={exits} colour="var(--pos)" fmt={fmtCash} hover={hover} onHover={setHover} />
      <Panel
        points={pts}
        pick={(p) => p.spot}
        height={120}
        label="SPX"
        lines={[
          { v: sp, text: `short put ${String(sp)}` },
          { v: lp, text: `long put ${String(lp)}`, side: "left" },
          { v: sc, text: `short call ${String(sc)}` },
          { v: lc, text: `long call ${String(lc)}`, side: "left" },
        ]}
        exits={exits}
        colour="currentColor"
        fmt={(v) => v.toFixed(0)}
        hover={hover}
        onHover={setHover}
      />
    </div>
  );
}

export function TradePath({ runId, arm, session, position }: { runId: string; arm: string; session: string; position: string }) {
  const q = new URLSearchParams({ arm, session, position });
  // A finished run's trade does not change: read once, no polling.
  const { data, isLoading, isError, error } = useBridged<BacktestTradePath>(
    ["backtest", "path", runId, arm, session, position],
    `/api/backtest/runs/${runId}/path?${q.toString()}`,
    0,
  );
  if (isLoading) return <p className="muted">Reading the trade's quotes…</p>;
  if (isError) return <p className="pnl-neg">Trade path unavailable: {String(error)}</p>;
  if (!data?.ok || data.data === null) return <p className="pnl-neg">Trade path unavailable: {data?.error ?? "no answer"}</p>;
  return <TradePathChart path={data.data} />;
}
