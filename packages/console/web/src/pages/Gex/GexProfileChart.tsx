export interface GexStrikeRow {
  strike: number;
  call_iv: number;
  put_iv: number;
  call_oi: number;
  put_oi: number;
  call_vol: number;
  put_vol: number;
  total_vol: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
  abs_gex: number;
  call_gex_vol: number;
  put_gex_vol: number;
  net_gex_vol: number;
}

export type GexView = "net" | "oivol" | "abs";

export function fmtGexDollars(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

interface Props {
  series: GexStrikeRow[];
  view: GexView;
  spot: number;
  zeroGamma: number | null;
  callWall: number | null;
  putWall: number | null;
  /** Today's intraday spot trail (epoch seconds), drawn across the session width. */
  spotHistory?: Array<{ ts: number; spot: number }>;
  /** ET trading-session bounds for the trail's time axis (9:30–16:00). */
  spotSession?: { date: string; openTs: number; closeTs: number } | null;
  height?: number;
}

/**
 * GEX by strike — horizontal bars, strikes descending down the axis like the
 * gex module's page: green = call-heavy, red = put-heavy; overlays for spot
 * (blue), zero gamma (amber dashed), call/put walls (green/red ticks).
 */
export function GexProfileChart({ series, view, spot, zeroGamma, callWall, putWall, spotHistory, spotSession, height = 460 }: Props) {
  // Trim to strikes with data, nearest 40 to spot.
  const active = series.filter((s) => s.abs_gex !== 0 || s.net_gex_vol !== 0 || s.total_vol !== 0);
  const rows = [...active]
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
    .slice(0, 40)
    .sort((a, b) => b.strike - a.strike);
  if (rows.length === 0) return <p className="muted">no strikes with GEX data</p>;

  const width = 760;
  const hasTimeAxis = spotSession != null && spotHistory !== undefined && spotHistory.length > 1;
  const m = { l: 64, r: 16, t: 8, b: hasTimeAxis ? 22 : 8 };
  const plotW = width - m.l - m.r;
  const rowH = (height - m.t - m.b) / rows.length;
  const values = (r: GexStrikeRow): number[] =>
    view === "net" ? [r.net_gex] : view === "abs" ? [r.abs_gex] : [r.net_gex, r.net_gex_vol];
  const maxAbs = Math.max(...rows.flatMap((r) => values(r).map(Math.abs)), 1);
  const zeroX = m.l + plotW / 2;
  const sx = (v: number) => zeroX + (v / maxAbs) * (plotW / 2) * 0.96;
  const strikeY = (strike: number): number | null => {
    // Interpolate between category rows for the overlay lines.
    for (let i = 0; i < rows.length - 1; i++) {
      const hi = rows[i]!.strike;
      const lo = rows[i + 1]!.strike;
      if (strike <= hi && strike >= lo) {
        const t = hi === lo ? 0 : (hi - strike) / (hi - lo);
        return m.t + (i + 0.5 + t) * rowH;
      }
    }
    return null;
  };

  interface Overlay {
    y: number;
    color: string;
    dash?: string;
    label: string;
    side: "left" | "right";
    filled: boolean;
  }
  // Old-page tag styling: filled wall pills hug the left axis; spot and
  // zero-gamma ride the right edge as outlined value tags.
  const overlays: Overlay[] = [];
  const spotY = strikeY(spot);
  if (spotY !== null)
    overlays.push({ y: spotY, color: "#7aa2ff", label: `$${spot.toFixed(2)}`, side: "right", filled: false });
  if (zeroGamma !== null) {
    const y = strikeY(zeroGamma);
    if (y !== null)
      overlays.push({ y, color: "#d9a13b", dash: "3 3", label: `Zero Γ: $${zeroGamma.toFixed(2)}`, side: "right", filled: false });
  }
  if (callWall !== null) {
    const y = strikeY(callWall);
    if (y !== null)
      overlays.push({ y, color: "#43b57a", dash: "6 4", label: callWall.toFixed(2), side: "left", filled: true });
  }
  if (putWall !== null) {
    const y = strikeY(putWall);
    if (y !== null)
      overlays.push({ y, color: "#d95c4a", dash: "3 3", label: putWall.toFixed(2), side: "left", filled: true });
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="GEX by strike" style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={zeroX} y1={m.t} x2={zeroX} y2={height - m.b} stroke="#23262d" />
      {rows.map((r, i) => {
        const yMid = m.t + (i + 0.5) * rowH;
        const vals = values(r);
        return (
          <g key={r.strike}>
            {rowH >= 11 && (
              <text x={m.l - 6} y={yMid + 3} textAnchor="end" fontSize={10} fill="#a6adb8" fontFamily="Consolas, monospace">
                {r.strike}
              </text>
            )}
            {vals.map((v, j) => {
              const barH = Math.max(2, (rowH * 0.7) / vals.length);
              const y = yMid - (rowH * 0.35) + j * barH;
              const color =
                view === "abs" ? "#7aa2ff" : v >= 0 ? (j === 0 ? "#43b57a" : "#7fd4a8") : j === 0 ? "#d95c4a" : "#e89386";
              return (
                <rect
                  key={j}
                  x={Math.min(zeroX, sx(v))}
                  y={y}
                  width={Math.max(1, Math.abs(sx(v) - zeroX))}
                  height={barH - 1}
                  fill={color}
                >
                  <title>{`${r.strike}: ${fmtGexDollars(v)}`}</title>
                </rect>
              );
            })}
          </g>
        );
      })}
      {hasTimeAxis && (() => {
        // Trail across the plot width, anchored to the ET trading session:
        // x = (ts − 9:30 ET) / (16:00 − 9:30), y = spot interpolated on the
        // strike axis. A full session records thousands of ticks — decimate
        // to ~400 points so the SVG stays light.
        const { openTs, closeTs } = spotSession!;
        const span = Math.max(closeTs - openTs, 1);
        const stride = Math.max(1, Math.floor(spotHistory.length / 400));
        const sampled = spotHistory.filter((_, i) => i % stride === 0 || i === spotHistory.length - 1);
        const pts = sampled
          .map((h) => {
            const y = strikeY(h.spot);
            if (y === null) return null;
            const frac = Math.min(1, Math.max(0, (h.ts - openTs) / span));
            return `${(m.l + frac * plotW).toFixed(1)},${y.toFixed(1)}`;
          })
          .filter((s): s is string => s !== null);
        const hours = [
          ["9:30", 0],
          ["10:30", 1 / 6.5],
          ["11:30", 2 / 6.5],
          ["12:30", 3 / 6.5],
          ["13:30", 4 / 6.5],
          ["14:30", 5 / 6.5],
          ["15:30", 6 / 6.5],
          ["16:00", 1],
        ] as const;
        return (
          <>
            {pts.length > 1 && (
              <polyline points={pts.join(" ")} fill="none" stroke="#7aa2ff" strokeWidth={1} opacity={0.45} />
            )}
            {hours.map(([label, frac]) => {
              const x = m.l + frac * plotW;
              return (
                <g key={label}>
                  <line x1={x} y1={height - m.b} x2={x} y2={height - m.b + 4} stroke="#82878f" />
                  <text
                    x={x}
                    y={height - m.b + 14}
                    textAnchor={frac === 0 ? "start" : frac === 1 ? "end" : "middle"}
                    fontSize={9}
                    fill="#82878f"
                    fontFamily="Consolas, monospace"
                  >
                    {label}
                  </text>
                </g>
              );
            })}
            <text x={m.l} y={m.t + 9} fontSize={9} fill="#7aa2ff" opacity={0.8}>
              spot trail — {spotSession!.date} ET
            </text>
          </>
        );
      })()}
      {overlays.map((o, i) => {
        const tagH = 15;
        const tagW = o.label.length * 6.4 + 12;
        const tagX = o.side === "left" ? m.l - 4 : width - m.r - tagW;
        const tagY = o.y - tagH / 2;
        return (
          <g key={i}>
            <line x1={m.l} y1={o.y} x2={width - m.r} y2={o.y} stroke={o.color} strokeWidth={1.3} strokeDasharray={o.dash} />
            <rect
              x={tagX}
              y={tagY}
              width={tagW}
              height={tagH}
              rx={3}
              fill={o.filled ? o.color : "#101216"}
              stroke={o.color}
              strokeWidth={1}
            />
            <text
              x={tagX + tagW / 2}
              y={o.y + 3.5}
              textAnchor="middle"
              fontSize={10}
              fontWeight={700}
              fill={o.filled ? "#0b0c0f" : o.color}
              fontFamily="Consolas, monospace"
            >
              {o.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Generic horizontal mirrored-bar chart by strike: positive series right,
 * negative left. Drives the OI-by-strike and Volume-by-strike cards.
 */
export function StrikeBarsChart({
  series,
  spot,
  bars,
  height = 380,
}: {
  series: GexStrikeRow[];
  spot: number;
  bars: Array<{ label: string; color: string; value: (r: GexStrikeRow) => number }>;
  height?: number;
}) {
  const active = series.filter((s) => bars.some((b) => b.value(s) !== 0));
  const rows = [...active]
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
    .slice(0, 40)
    .sort((a, b) => b.strike - a.strike);
  if (rows.length === 0) return <p className="muted">no data</p>;
  const width = 760;
  const m = { l: 64, r: 16, t: 16, b: 8 };
  const plotW = width - m.l - m.r;
  const rowH = (height - m.t - m.b) / rows.length;
  const maxAbs = Math.max(...rows.flatMap((r) => bars.map((b) => Math.abs(b.value(r)))), 1);
  const zeroX = m.l + plotW / 2;
  const sx = (v: number) => zeroX + (v / maxAbs) * (plotW / 2) * 0.96;
  const spotIdx = rows.findIndex((r, i) => i < rows.length - 1 && spot <= r.strike && spot >= rows[i + 1]!.strike);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="by strike" style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={zeroX} y1={m.t} x2={zeroX} y2={height - m.b} stroke="#23262d" />
      {bars.map((b, i) => (
        <text key={b.label} x={m.l + i * 92} y={10} fontSize={10} fill={b.color}>
          {b.label}
        </text>
      ))}
      {rows.map((r, i) => {
        const yMid = m.t + (i + 0.5) * rowH;
        return (
          <g key={r.strike}>
            {rowH >= 11 && (
              <text x={m.l - 6} y={yMid + 3} textAnchor="end" fontSize={10} fill="#a6adb8" fontFamily="Consolas, monospace">
                {r.strike}
              </text>
            )}
            {bars.map((b, j) => {
              const v = b.value(r);
              if (v === 0) return null;
              const barH = Math.max(2, (rowH * 0.75) / bars.length);
              const y = yMid - (rowH * 0.375) + j * barH;
              return (
                <rect key={j} x={Math.min(zeroX, sx(v))} y={y} width={Math.max(1, Math.abs(sx(v) - zeroX))} height={barH - 0.5} fill={b.color}>
                  <title>{`${r.strike} ${b.label}: ${Math.abs(v).toLocaleString()}`}</title>
                </rect>
              );
            })}
          </g>
        );
      })}
      {spotIdx >= 0 && (
        <line x1={m.l} y1={m.t + (spotIdx + 1) * rowH} x2={width - m.r} y2={m.t + (spotIdx + 1) * rowH} stroke="#7aa2ff" strokeWidth={1.2} />
      )}
    </svg>
  );
}

/** Call IV vs put IV by strike — line chart with a spot marker. */
export function IvSkewChart({ series, spot, height = 240 }: { series: GexStrikeRow[]; spot: number; height?: number }) {
  const rows = series.filter((s) => s.call_iv > 0 || s.put_iv > 0).sort((a, b) => a.strike - b.strike);
  if (rows.length < 2) return <p className="muted">no IV data</p>;
  const width = 760;
  const m = { l: 44, r: 12, t: 10, b: 20 };
  const xs = rows.map((r) => r.strike);
  const ivs = rows.flatMap((r) => [r.call_iv, r.put_iv]).filter((v) => v > 0);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ivs);
  const y1 = Math.max(...ivs);
  const sx = (x: number) => m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r);
  const sy = (y: number) => m.t + ((y1 - y) / Math.max(y1 - y0, 0.01)) * (height - m.t - m.b);
  const line = (key: "call_iv" | "put_iv") =>
    rows
      .filter((r) => r[key] > 0)
      .map((r) => `${sx(r.strike).toFixed(1)},${sy(r[key]).toFixed(1)}`)
      .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="IV skew" style={{ width: "100%", height: "auto", display: "block" }}>
      {spot >= x0 && spot <= x1 && (
        <line x1={sx(spot)} y1={m.t} x2={sx(spot)} y2={height - m.b} stroke="#d9a13b" strokeDasharray="4 4" />
      )}
      <polyline points={line("call_iv")} fill="none" stroke="#43b57a" strokeWidth={1.6} />
      <polyline points={line("put_iv")} fill="none" stroke="#d95c4a" strokeWidth={1.6} />
      <text x={m.l} y={height - 6} fontSize={10} fill="#a6adb8">{x0}</text>
      <text x={width - m.r} y={height - 6} textAnchor="end" fontSize={10} fill="#a6adb8">{x1}</text>
      <text x={m.l} y={m.t + 8} fontSize={10} fill="#43b57a">call IV%</text>
      <text x={m.l + 60} y={m.t + 8} fontSize={10} fill="#d95c4a">put IV%</text>
    </svg>
  );
}
