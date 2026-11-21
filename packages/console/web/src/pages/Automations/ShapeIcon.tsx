import { useId } from "react";
import type { AutomationShape } from "@console/shared";

/**
 * A small drawing of an automation's trade: its payoff at expiry from the module's generic shape (the groups
 * view's `shape`, `sketch.sketch`), the dashed line at zero, profit tinted above it and loss below. The
 * module gives the points; this only scales them into the box. No shape (no legs, or an older module): an
 * empty frame.
 */
const W = 44;
const H = 24;
const PAD = 3;

export function ShapeIcon({ shape, label }: { shape: AutomationShape | null | undefined; label: string }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const pts = shape?.curve ?? [];
  if (pts.length < 2) return <span className="shape-icon is-empty" role="img" aria-label={`${label}: no shape`} />;
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  let top = Math.max(0, ...ys);
  let bot = Math.min(0, ...ys);
  const span = top - bot || 1;
  top += span * 0.08;
  bot -= span * 0.08;
  const X = (x: number) => PAD + ((W - 2 * PAD) * (x - lo)) / (hi - lo || 1);
  const Y = (v: number) => PAD + ((H - 2 * PAD) * (top - v)) / (top - bot);
  const line = pts.map(([x, v]) => `${X(x).toFixed(2)},${Y(v).toFixed(2)}`).join(" ");
  const z = Y(0);
  const area = `${X(lo).toFixed(2)},${z.toFixed(2)} ${line} ${X(hi).toFixed(2)},${z.toFixed(2)}`;
  return (
    <svg className="shape-icon" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: its payoff at expiry`}>
      <defs>
        <clipPath id={`${id}a`}>
          <rect x="0" y="0" width={W} height={z} />
        </clipPath>
        <clipPath id={`${id}b`}>
          <rect x="0" y={z} width={W} height={H} />
        </clipPath>
      </defs>
      <polygon className="shape-gain" points={area} clipPath={`url(#${id}a)`} />
      <polygon className="shape-loss" points={area} clipPath={`url(#${id}b)`} />
      <line className="shape-zero" x1={PAD} x2={W - PAD} y1={z} y2={z} />
      <polyline className="shape-line" points={line} />
    </svg>
  );
}
