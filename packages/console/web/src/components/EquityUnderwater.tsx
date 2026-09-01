/**
 * The cumulative-equity curve with its underwater (drawdown) plot beneath, drawn as one pair —
 * the tearsheet's canonical panel. Extracted from the flies performance tab (2026-08-23,
 * docs/metrics-plan.md step 0) so every module's performance/history surface can carry it.
 * Deliberately spare like the source: the pair answers "how did it grow and how much did holding
 * it hurt" at a glance; axes and richer annotation stay with the page that needs them.
 */
export interface EquityPoint {
  date: string;
  equity: number;
  /** Depth below the running peak at this point, >= 0 (the underwater series). */
  drawdown: number;
}

export function EquityUnderwater({ equity }: { equity: EquityPoint[] }) {
  if (equity.length < 2) return <p className="muted">not enough history yet</p>;
  const width = 720;
  const h = 150;
  const hd = 70;
  const m = { l: 56, r: 12, t: 10, b: 18 };
  const eq = equity.map((e) => e.equity);
  const lo = Math.min(...eq);
  const hi = Math.max(...eq);
  const span = hi - lo || 1;
  const maxDd = Math.max(...equity.map((e) => e.drawdown), 1);
  const X = (i: number): number => m.l + (i * (width - m.l - m.r)) / Math.max(equity.length - 1, 1);
  const Y = (v: number): number => m.t + (1 - (v - lo) / span) * (h - m.t - m.b);
  const Yd = (v: number): number => m.t + (v / maxDd) * (hd - m.t - m.b);

  return (
    <>
      <svg
        viewBox={`0 0 ${String(width)} ${String(h)}`}
        role="img"
        aria-label="cumulative net P&L"
        style={{ width: "100%", height: "auto", display: "block" }}
      >
        <polyline
          fill="none"
          stroke="#43b57a"
          strokeWidth="1.5"
          points={equity.map((e, i) => `${X(i).toFixed(1)},${Y(e.equity).toFixed(1)}`).join(" ")}
        />
      </svg>
      <svg
        viewBox={`0 0 ${String(width)} ${String(hd)}`}
        role="img"
        aria-label="drawdown"
        style={{ width: "100%", height: "auto", display: "block" }}
      >
        <polyline
          fill="rgba(217, 92, 74, 0.2)"
          stroke="#d95c4a"
          strokeWidth="1.2"
          points={
            `${X(0).toFixed(1)},${Yd(0).toFixed(1)} ` +
            equity.map((e, i) => `${X(i).toFixed(1)},${Yd(e.drawdown).toFixed(1)}`).join(" ") +
            ` ${X(equity.length - 1).toFixed(1)},${Yd(0).toFixed(1)}`
          }
        />
      </svg>
    </>
  );
}
