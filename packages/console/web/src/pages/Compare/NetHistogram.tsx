import { fmtCash } from "../../lib/format";
import { useMeasure } from "../../lib/useMeasure";

export interface HistBin {
  lo: number;
  hi: number;
  count: number;
}

/** A 1/2/5 x 10^k step at least `raw` wide. */
function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

/**
 * Bins of one nice width whose edges are multiples of it, so ZERO IS ALWAYS AN EDGE: every bin lies
 * wholly on one side of it, and a bin's tone (loss or gain) is a fact about the bin, not a threshold
 * chosen here. Each bin is [lo, hi), so a trade of exactly zero sits in the first gain bin.
 */
export function histogramBins(values: number[]): HistBin[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const target = Math.min(24, Math.max(5, Math.ceil(Math.sqrt(values.length) * 1.5)));
  const w = max === min ? niceStep(Math.max(1, Math.abs(max)) / 5) : niceStep((max - min) / target);
  const first = Math.floor(min / w);
  const last = Math.floor(max / w) + 1;
  const bins: HistBin[] = [];
  for (let i = first; i < last; i++) bins.push({ lo: i * w, hi: (i + 1) * w, count: 0 });
  for (const v of values) {
    const i = Math.min(bins.length - 1, Math.floor(v / w) - first);
    const bin = bins[i];
    if (bin !== undefined) bin.count += 1;
  }
  return bins;
}

const HEIGHT = 150;
const PAD = { l: 34, r: 10, t: 10, b: 22 };

/**
 * Per-trade net, binned. Refuses to draw under two points: one trade is not a distribution, and an
 * axis around it would read as one. Bars are toned by which side of zero the bin lies (bins never
 * straddle it), never by a threshold.
 */
export function NetHistogram({ values, label }: { values: number[]; label: string }) {
  const [ref, width] = useMeasure<HTMLDivElement>(640);
  if (values.length < 2) {
    return (
      <p className="muted cmp-hist-refused">
        {values.length === 0 ? "No closed trade" : "One closed trade"} in scope — a histogram needs at least two.
      </p>
    );
  }
  const bins = histogramBins(values);
  const top = Math.max(...bins.map((b) => b.count));
  const innerW = Math.max(10, width - PAD.l - PAD.r);
  const innerH = HEIGHT - PAD.t - PAD.b;
  const bw = innerW / bins.length;
  const lo = bins[0]?.lo ?? 0;
  const hi = bins[bins.length - 1]?.hi ?? 0;
  const x = (v: number) => PAD.l + ((v - lo) / (hi - lo)) * innerW;
  const y = (c: number) => PAD.t + innerH - (c / top) * innerH;
  const ticks = [lo, ...(lo < 0 && hi > 0 ? [0] : []), hi];

  return (
    <div ref={ref} className="cmp-hist">
      <svg
        width={width}
        height={HEIGHT}
        viewBox={`0 0 ${String(width)} ${String(HEIGHT)}`}
        role="img"
        aria-label={`${label}: per-trade net, ${String(values.length)} trades`}
      >
        <line x1={PAD.l} x2={PAD.l + innerW} y1={PAD.t + innerH} y2={PAD.t + innerH} className="cmp-hist-axis" />
        <text x={PAD.l - 4} y={PAD.t + 8} textAnchor="end" className="cmp-hist-label">
          {top}
        </text>
        <text x={PAD.l - 4} y={PAD.t + innerH} textAnchor="end" className="cmp-hist-label">
          0
        </text>
        {bins.map((b, i) =>
          b.count === 0 ? null : (
            <rect
              key={i}
              x={PAD.l + i * bw + 1}
              y={y(b.count)}
              width={Math.max(1, bw - 2)}
              height={PAD.t + innerH - y(b.count)}
              className={b.hi <= 0 ? "cmp-hist-loss" : "cmp-hist-gain"}
            >
              <title>{`${fmtCash(b.lo)} to ${fmtCash(b.hi)}: ${String(b.count)} trade${b.count === 1 ? "" : "s"}`}</title>
            </rect>
          ),
        )}
        {lo < 0 && hi > 0 && <line x1={x(0)} x2={x(0)} y1={PAD.t} y2={PAD.t + innerH} className="cmp-hist-zero" />}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={HEIGHT - 6} textAnchor={t === lo ? "start" : t === hi ? "end" : "middle"} className="cmp-hist-label">
            {fmtCash(t)}
          </text>
        ))}
      </svg>
    </div>
  );
}
