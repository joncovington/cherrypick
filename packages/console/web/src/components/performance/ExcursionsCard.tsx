import { fmtMoney } from "../../lib/format";
import type { ExcursionsResult } from "../../lib/api";
import { Card } from "../DataTable";
import { Tile } from "./Tile";
import { ARM_COLORS } from "../chart/tokens";
import { niceTicks } from "../chart/scales";
import { AXIS_MUTED } from "../Charts";

/**
 * MAE/MFE (docs/metrics-plan.md Phase 2) -- the deepest a position went against its entry and the
 * best it got before close, per `services/excursionsBridge.ts`. Distribution tiles first (the
 * suite-wide summary every module's reading already carries elsewhere), then one point per
 * position: x = MAE (always <= 0), y = MFE (always >= 0), colored by tag -- so a cluster near the
 * origin reads as "closed near entry" and a cluster in the far lower-right reads as "wide swings
 * both ways," without reading 94 rows of a table.
 */
export function ExcursionsCard({ excursions }: { excursions: ExcursionsResult }) {
  if (!excursions.ok || excursions.data === null) {
    return (
      <Card title="excursions (MAE / MFE)" collapseKey="performance-excursions">
        <p className="muted">{excursions.error ?? "unavailable"}</p>
      </Card>
    );
  }

  const { positions, maeDistribution, mfeDistribution } = excursions.data;
  const tags = [...new Set(positions.map((p) => p.tag))].sort();
  const colorOf = (tag: string) => ARM_COLORS[tags.indexOf(tag) % ARM_COLORS.length]!;

  const width = 1150;
  const height = 320;
  const pad = { l: 66, r: 12, t: 16, b: 30 };

  let scatter = null;
  if (positions.length > 0) {
    const maes = positions.map((p) => p.mae);
    const mfes = positions.map((p) => p.mfe);
    const xMin = Math.min(...maes, 0);
    const xMax = Math.max(...maes, 0);
    const yMin = Math.min(...mfes, 0);
    const yMax = Math.max(...mfes, 0);
    const xSpan = xMax - xMin || 1;
    const ySpan = yMax - yMin || 1;
    const X = (v: number) => pad.l + ((v - xMin) / xSpan) * (width - pad.l - pad.r);
    const Y = (v: number) => height - pad.b - ((v - yMin) / ySpan) * (height - pad.t - pad.b);

    scatter = (
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="MAE vs MFE scatter" style={{ width: "100%", height: "auto", display: "block" }}>
        {niceTicks(yMin, yMax, 5).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
            <text x={4} y={Y(v) + 3} fontSize={9} fill={AXIS_MUTED} fontFamily="Consolas, monospace">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {niceTicks(xMin, xMax, 6).map((v) => (
          <g key={`x${v}`}>
            <line x1={X(v)} y1={pad.t} x2={X(v)} y2={height - pad.b} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
            <text x={X(v)} y={height - 8} fontSize={9} fill={AXIS_MUTED} textAnchor="middle" fontFamily="Consolas, monospace">
              {v.toFixed(0)}
            </text>
          </g>
        ))}
        <text x={width - pad.r} y={height - 8} fontSize={9} fill={AXIS_MUTED} textAnchor="end">
          MAE ($)
        </text>
        <text
          x={10}
          y={pad.t + (height - pad.t - pad.b) / 2}
          fontSize={9}
          fill={AXIS_MUTED}
          textAnchor="middle"
          transform={`rotate(-90 10 ${pad.t + (height - pad.t - pad.b) / 2})`}
        >
          MFE ($)
        </text>
        {positions.map((p) => (
          <circle key={p.id} cx={X(p.mae)} cy={Y(p.mfe)} r={3.5} fill={colorOf(p.tag)} opacity={0.75}>
            <title>{`${p.tag} · ${p.symbol} — MAE ${fmtMoney(p.mae)}, MFE ${fmtMoney(p.mfe)}${p.n !== null ? ` (n=${p.n} marks)` : ""}`}</title>
          </circle>
        ))}
      </svg>
    );
  }

  return (
    <Card title="excursions (MAE / MFE)" collapseKey="performance-excursions">
      <div className="stats-grid">
        <Tile label="MAE median" value={fmtMoney(maeDistribution.median)} tone={maeDistribution.median === null ? "dim" : "neg"} n={maeDistribution.n} />
        <Tile label="MFE median" value={fmtMoney(mfeDistribution.median)} tone={mfeDistribution.median === null ? "dim" : "pos"} n={mfeDistribution.n} />
      </div>
      {positions.length === 0 ? (
        <p className="muted" style={{ marginTop: "0.6rem" }}>
          no closed position has a usable mark path in this window yet
        </p>
      ) : (
        <>
          {scatter}
          <div className="forest-legend" style={{ marginTop: "0.4rem" }}>
            {tags.map((tag) => (
              <span key={tag}>
                <i style={{ background: colorOf(tag) }} /> {tag}
              </span>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}
