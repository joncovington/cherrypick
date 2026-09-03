/**
 * The rect+text readout markup shared by the hand-SVG chart family -- extracted 2026-09 from two
 * near-identical shapes carried separately by ForestCard.tsx, MeicForestCard.tsx, TimelineCard.tsx
 * and JournalCard.tsx.
 *
 * `SpotMarker` is the amber spot/settlement line + outlined label tag: byte-identical in
 * ForestCard.tsx (:266-283) and MeicForestCard.tsx (:211-236) modulo the label text.
 *
 * `HoverReadout` is the hover tooltip box: byte-identical in ForestCard.tsx (:289-313) and
 * TimelineCard.tsx (:353-376) — box top `y=8`, text baseline `20 + i*12` — and the same shape
 * again in JournalCard.tsx (:104-123) with `y=4`/`16 + i*12` (a shorter card has less to clear
 * above the bars), which is why `boxTop` is a parameter rather than a second copy.
 * MeicForestCard's hover state is a crosshair only, no box, so it does not call this.
 */
import { SPOT_COLOR } from "./tokens";

export function SpotMarker({
  x,
  label,
  top,
  bottom,
  left,
  right,
  width,
}: {
  x: number;
  label: string;
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
}) {
  const lw = label.length * 5.6 + 12;
  const lx = Math.min(Math.max(x - lw / 2, left), width - right - lw);
  return (
    <>
      <line x1={x} y1={top} x2={x} y2={bottom} stroke={SPOT_COLOR} strokeWidth={2} opacity={0.9} />
      <rect x={lx} y={1} width={lw} height={15} rx={4} fill="#101216" stroke={SPOT_COLOR} strokeWidth={1} />
      <text x={lx + lw / 2} y={12} fontSize={9.5} fontWeight={700} fill={SPOT_COLOR} textAnchor="middle" fontFamily="Consolas, monospace">
        {label}
      </text>
    </>
  );
}

export function HoverReadout({
  x,
  width,
  lines,
  lineColor,
  boxTop = 8,
}: {
  x: number;
  width: number;
  lines: string[];
  lineColor: (i: number) => string;
  boxTop?: number;
}) {
  const bw = Math.max(...lines.map((l) => l.length)) * 5.8 + 12;
  const bh = lines.length * 12 + 8;
  const bx = Math.min(Math.max(x + 12, 4), width - bw - 4);
  const textY0 = boxTop + 12;
  return (
    <>
      <rect x={bx} y={boxTop} width={bw} height={bh} rx={5} fill="#101216f0" stroke="#2a2f3a" />
      {lines.map((l, i) => (
        <text key={i} x={bx + 6} y={textY0 + i * 12} fontSize={9.5} fill={lineColor(i)} fontFamily="Consolas, monospace">
          {l}
        </text>
      ))}
    </>
  );
}
