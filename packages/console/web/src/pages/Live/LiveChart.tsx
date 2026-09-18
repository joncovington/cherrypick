import type { LiveFliesPayload } from "@console/shared";
import { hhmm } from "../../components/chart/time";
import { niceTicks, timeTicks } from "../../components/chart/scales";
import { useHoverX } from "../../components/chart/useHoverX";
import { AXIS_FONT_FAMILY, AXIS_MUTED_HEX, SPOT_COLOR } from "../../components/chart/tokens";
import { fmtMoney } from "../../lib/format";

/**
 * The live day on one session clock, two panels. Top: SPX % change from the prior close (left
 * axis) with VIX and VIX3M (right axis). Bottom: the summed mid mark of every open live position
 * (left) and the loop's open worst-case exposure -- the buying-power gate's own figure -- as a
 * step (right). Gaps in the mark path are drawn as gaps and named from the feed's own refusal
 * reason, never interpolated, the same rule `Flies/TimelineCard` follows. A mark is a mid, not a
 * fill; the card title says so.
 */
const VIX_COLOR = "#8a6fd1";
const VIX3M_COLOR = "#5b8ad1";
const MARK_COLOR = "#d95b5b";
const BP_COLOR = "#d9a13b";

type Pt = { m: number; v: number };

function polyline(pts: Pt[], X: (m: number) => number, Y: (v: number) => number, gapLimit: number): string[] {
  const segs: string[] = [];
  let cur: string[] = [];
  let prev: number | null = null;
  for (const p of pts) {
    if (prev !== null && p.m - prev > gapLimit && cur.length > 0) {
      if (cur.length > 1) segs.push(cur.join(" "));
      cur = [];
    }
    cur.push(`${X(p.m).toFixed(1)},${Y(p.v).toFixed(1)}`);
    prev = p.m;
  }
  if (cur.length > 1) segs.push(cur.join(" "));
  return segs;
}

function stepline(pts: Pt[], X: (m: number) => number, Y: (v: number) => number): string {
  const out: string[] = [];
  let prevY: number | null = null;
  for (const p of pts) {
    const x = X(p.m).toFixed(1);
    const y = Y(p.v).toFixed(1);
    if (prevY !== null) out.push(`${x},${prevY}`);
    out.push(`${x},${y}`);
    prevY = y as unknown as number;
  }
  return out.join(" ");
}

function extent(series: Pt[][], pad = 0.12): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of series) for (const p of s) {
    lo = Math.min(lo, p.v);
    hi = Math.max(hi, p.v);
  }
  if (!Number.isFinite(lo)) return [0, 1];
  const span = hi - lo || 1;
  return [lo - span * pad, hi + span * pad];
}

export function LiveChart({ data }: { data: LiveFliesPayload }) {
  const { spx, vix, vix3m, markPnl, openMargin, gaps } = data.series;
  const width = 1150;
  const height = 420;
  const pad = { l: 58, r: 58, t: 12, b: 24 };
  const split = 24;
  const topBot = pad.t + (height - pad.t - pad.b - split) * 0.55;
  const botTop = topBot + split;
  const { fx: hover, onMouseMove, onMouseLeave } = useHoverX(width, pad.l, pad.r);

  const all = [...spx, ...vix, ...vix3m, ...markPnl, ...openMargin];
  if (all.length === 0) {
    return <p className="muted">no intraday series for this session yet — the loop marks once it is armed and ticking</p>;
  }
  const tMin = Math.min(9 * 60 + 30, ...all.map((p) => p.m));
  const tMax = 16 * 60;
  const X = (m: number) => pad.l + ((m - tMin) / (tMax - tMin || 1)) * (width - pad.l - pad.r);

  const [pLo, pHi] = extent([spx]);
  const PY = (v: number) => topBot - ((v - pLo) / (pHi - pLo || 1)) * (topBot - pad.t);
  const [vLo, vHi] = extent([vix, vix3m]);
  const VY = (v: number) => topBot - ((v - vLo) / (vHi - vLo || 1)) * (topBot - pad.t);
  const [mLo0, mHi0] = extent([markPnl], 0.15);
  const mLo = Math.min(mLo0, 0);
  const mHi = Math.max(mHi0, 0);
  const MY = (v: number) => height - pad.b - ((v - mLo) / (mHi - mLo || 1)) * (height - pad.b - botTop);
  const bpHi = Math.max(data.buyingPower.cap ?? 0, ...openMargin.map((p) => p.v), 1);
  const BY = (v: number) => height - pad.b - (v / bpHi) * (height - pad.b - botTop);

  const markSteps = markPnl.slice(1).map((p, i) => p.m - markPnl[i]!.m).filter((d) => d > 0).sort((a, b) => a - b);
  const median = markSteps.length > 0 ? markSteps[Math.floor(markSteps.length / 2)]! : 1;
  const gapLimit = Math.max(median * 3, 5);

  const hoverM = hover !== null ? tMin + ((hover - pad.l) / (width - pad.l - pad.r)) * (tMax - tMin) : null;
  const at = (s: Pt[]): Pt | null => {
    if (hoverM === null || s.length === 0) return null;
    return s.reduce((b, p) => (Math.abs(p.m - hoverM) < Math.abs(b.m - hoverM) ? p : b), s[0]!);
  };
  const hSpx = at(spx);
  const hVix = at(vix);
  const hVix3m = at(vix3m);
  const hMark = at(markPnl);
  const hBp = at(openMargin);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="live intraday" style={{ width: "100%", height: "auto", display: "block" }} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave}>
      {/* top panel grid: SPX % left, VIX right */}
      {niceTicks(pLo, pHi, 4).map((v) => (
        <g key={`p${v}`}>
          <line x1={pad.l} y1={PY(v)} x2={width - pad.r} y2={PY(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
          <text x={4} y={PY(v) + 3} fontSize={9} fill={AXIS_MUTED_HEX} fontFamily={AXIS_FONT_FAMILY}>{`${v >= 0 ? "+" : ""}${v.toFixed(2)}%`}</text>
        </g>
      ))}
      {(vix.length > 0 || vix3m.length > 0) &&
        niceTicks(vLo, vHi, 4).map((v) => (
          <text key={`vx${v}`} x={width - pad.r + 6} y={VY(v) + 3} fontSize={9} fill={VIX_COLOR} fontFamily={AXIS_FONT_FAMILY}>{v.toFixed(1)}</text>
        ))}
      {/* bottom panel grid: mark $ left, BP right */}
      {niceTicks(mLo, mHi, 3).map((v) => (
        <g key={`m${v}`}>
          <line x1={pad.l} y1={MY(v)} x2={width - pad.r} y2={MY(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
          <text x={4} y={MY(v) + 3} fontSize={9} fill={AXIS_MUTED_HEX} fontFamily={AXIS_FONT_FAMILY}>{fmtMoney(v)}</text>
        </g>
      ))}
      {[0, bpHi / 2, bpHi].map((v) => (
        <text key={`bp${v}`} x={width - pad.r + 6} y={BY(v) + 3} fontSize={9} fill={BP_COLOR} fontFamily={AXIS_FONT_FAMILY}>{fmtMoney(v)}</text>
      ))}
      {timeTicks(tMin, tMax, 8).map((m) => (
        <g key={`t${m}`}>
          <line x1={X(m)} y1={pad.t} x2={X(m)} y2={height - pad.b} stroke="#15181e" />
          <text x={X(m)} y={height - 7} fontSize={9} fill={AXIS_MUTED_HEX} textAnchor="middle" fontFamily={AXIS_FONT_FAMILY}>{hhmm(m)}</text>
        </g>
      ))}
      {/* gaps: named refusals from the feed */}
      {gaps.map((g, i) => (
        <g key={`g${i}`}>
          <line x1={X(g.m)} y1={botTop} x2={X(g.m)} y2={height - pad.b} stroke="#5a2f2f" strokeDasharray="2 3" />
          {i % 4 === 0 && <text x={X(g.m) + 2} y={botTop + 10} fontSize={8} fill="#a05a5a" fontFamily={AXIS_FONT_FAMILY}>{g.reason}</text>}
        </g>
      ))}
      {/* buying power as a step, cap as a line */}
      {data.buyingPower.cap !== null && <line x1={pad.l} y1={BY(data.buyingPower.cap)} x2={width - pad.r} y2={BY(data.buyingPower.cap)} stroke={BP_COLOR} strokeDasharray="4 4" opacity={0.6} />}
      {openMargin.length > 1 && <polyline points={stepline(openMargin, X, BY)} fill="none" stroke={BP_COLOR} strokeWidth={1.2} opacity={0.85} />}
      {/* mark path */}
      {polyline(markPnl, X, MY, gapLimit).map((seg, i) => (
        <polyline key={`mk${i}`} points={seg} fill="none" stroke={MARK_COLOR} strokeWidth={1.6} />
      ))}
      {/* SPX and vol */}
      {polyline(spx, X, PY, 30).map((seg, i) => (
        <polyline key={`spx${i}`} points={seg} fill="none" stroke={SPOT_COLOR} strokeWidth={1.4} />
      ))}
      {polyline(vix, X, VY, 30).map((seg, i) => (
        <polyline key={`vix${i}`} points={seg} fill="none" stroke={VIX_COLOR} strokeWidth={1} opacity={0.9} />
      ))}
      {polyline(vix3m, X, VY, 30).map((seg, i) => (
        <polyline key={`v3${i}`} points={seg} fill="none" stroke={VIX3M_COLOR} strokeWidth={1} opacity={0.9} />
      ))}
      {/* legend */}
      <g fontSize={9} fontFamily={AXIS_FONT_FAMILY}>
        <text x={pad.l + 4} y={pad.t + 10} fill={SPOT_COLOR}>{`${data.arm.symbol ?? "SPX"} %${data.series.spxBaseline?.source === "first_tick" ? " (vs first tick)" : ""}`}</text>
        <text x={pad.l + 90} y={pad.t + 10} fill={VIX_COLOR}>VIX</text>
        <text x={pad.l + 120} y={pad.t + 10} fill={VIX3M_COLOR}>VIX3M</text>
        <text x={pad.l + 4} y={botTop + 10} fill={MARK_COLOR}>mark P&amp;L (mid)</text>
        <text x={pad.l + 110} y={botTop + 10} fill={BP_COLOR}>open exposure / cap</text>
      </g>
      {/* hover */}
      {hover !== null && hoverM !== null && (
        <g>
          <line x1={hover} y1={pad.t} x2={hover} y2={height - pad.b} stroke="#3d4653" />
          <text x={Math.min(hover + 6, width - 200)} y={pad.t + 24} fontSize={10} fill="#c9ccd1" fontFamily={AXIS_FONT_FAMILY}>
            {hhmm(hoverM)}
            {hSpx ? `  ${data.arm.symbol ?? "SPX"} ${hSpx.v >= 0 ? "+" : ""}${hSpx.v.toFixed(2)}%` : ""}
            {hVix ? `  VIX ${hVix.v.toFixed(2)}` : ""}
            {hVix3m ? `  VIX3M ${hVix3m.v.toFixed(2)}` : ""}
          </text>
          <text x={Math.min(hover + 6, width - 200)} y={botTop + 24} fontSize={10} fill="#c9ccd1" fontFamily={AXIS_FONT_FAMILY}>
            {hMark ? `mark ${fmtMoney(hMark.v)}` : "no mark"}
            {hBp ? `  open ${fmtMoney(hBp.v)}` : ""}
          </text>
        </g>
      )}
    </svg>
  );
}
