import { useMeasure } from "../../lib/useMeasure";
import { fmtMoney } from "../../lib/format";

/**
 * Per-trade net, as a histogram: `ModulePerformanceGroup.tradeNets`, which `core.metrics` serves
 * beside every reading and nothing had drawn. Each value is a closed trade's net after fees, as the
 * module computed it; the only thing done here is counting them into bins, which is drawing, not
 * a statistic. No mean, no fit, no verdict.
 *
 * Bins are a "nice" width (1, 2 or 5 × 10^k dollars) chosen for about √n bins (6 to 24), with edges
 * on multiples of the width so zero is always an edge: no bin mixes winners and losers, and a bar's
 * colour is the sign of every trade in it. A bar's tooltip gives its range and count.
 */

export interface NetBin {
  lo: number;
  hi: number;
  n: number;
}

function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

/** The bins, lowest first. Empty for no trades. Pure. */
export function binTradeNets(nets: number[]): NetBin[] {
  const vals = nets.filter((v) => Number.isFinite(v));
  if (vals.length === 0) return [];
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const target = Math.min(24, Math.max(6, Math.round(Math.sqrt(vals.length))));
  const width = niceStep((max - min) / target || Math.max(Math.abs(max), 1) / target);
  const first = Math.floor(min / width);
  const last = Math.floor(max / width);
  const bins: NetBin[] = [];
  for (let k = first; k <= last; k++) bins.push({ lo: k * width, hi: (k + 1) * width, n: 0 });
  for (const v of vals) {
    const bin = bins[Math.floor(v / width) - first];
    if (bin !== undefined) bin.n += 1;
  }
  return bins;
}

const HEIGHT = 120;
const PAD = { left: 28, right: 8, top: 8, bottom: 22 };

export function TradeNetHistogram({ nets }: { nets: number[] }) {
  const [ref, width] = useMeasure(560);
  const bins = binTradeNets(nets);
  if (bins.length === 0) return null;
  const maxN = Math.max(...bins.map((b) => b.n));
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const barW = plotW / bins.length;
  const lo = bins[0]!.lo;
  const hi = bins[bins.length - 1]!.hi;
  const xOf = (v: number) => PAD.left + ((v - lo) / (hi - lo)) * plotW;
  const wins = nets.filter((v) => v > 0).length;
  const losses = nets.filter((v) => v < 0).length;
  return (
    <div ref={ref} style={{ marginTop: "0.6rem" }}>
      <div className="card-subhead">
        per-trade net · {String(nets.length)} trades · {String(wins)} above zero, {String(losses)} below
      </div>
      <svg width="100%" height={HEIGHT} viewBox={`0 0 ${String(width)} ${String(HEIGHT)}`} role="img" aria-label="per-trade net histogram">
        {bins.map((b, i) => {
          const h = maxN === 0 ? 0 : (b.n / maxN) * plotH;
          const cls = b.lo >= 0 ? "var(--pos)" : "var(--neg)";
          return (
            <rect
              key={b.lo}
              x={PAD.left + i * barW + 1}
              y={PAD.top + plotH - h}
              width={Math.max(1, barW - 2)}
              height={h}
              fill={cls}
              opacity={b.n === 0 ? 0 : 0.85}
            >
              <title>{`${fmtMoney(b.lo)} to ${fmtMoney(b.hi)}: ${String(b.n)} trade${b.n === 1 ? "" : "s"}`}</title>
            </rect>
          );
        })}
        {lo < 0 && hi > 0 && (
          <line x1={xOf(0)} x2={xOf(0)} y1={PAD.top} y2={PAD.top + plotH} stroke="currentColor" strokeOpacity={0.5} strokeDasharray="3 3" />
        )}
        <line x1={PAD.left} x2={PAD.left + plotW} y1={PAD.top + plotH} y2={PAD.top + plotH} stroke="currentColor" strokeOpacity={0.3} />
        <text x={PAD.left} y={HEIGHT - 6} fontSize={10} fill="currentColor" opacity={0.7}>
          {fmtMoney(lo)}
        </text>
        <text x={PAD.left + plotW} y={HEIGHT - 6} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
          {fmtMoney(hi)}
        </text>
        {lo < 0 && hi > 0 && (
          <text x={xOf(0)} y={HEIGHT - 6} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="middle">
            $0
          </text>
        )}
        <text x={PAD.left - 4} y={PAD.top + 8} fontSize={10} fill="currentColor" opacity={0.7} textAnchor="end">
          {String(maxN)}
        </text>
      </svg>
    </div>
  );
}
