import type { DatedValue, EquityReading, NavReading } from "@console/shared";
import { Tile } from "../performance/Tile";
import { TileGrid } from "../performance/TileGrid";
import { TimeLineChart, type TimeLineSeries } from "../chart/TimeLineChart";
import { fmtMoney } from "../../lib/format";
import {
  inWindow,
  rolling,
  underwater,
  type SeriesKind,
  type StressWindow,
} from "../../lib/navSeries";

/**
 * The daily-series surfaces shared by the contango pages and the curve redesign
 * (docs/metrics-plan.md, "Daily-series readings"). Each renders what `core.metrics.nav` computed or
 * a display transform of the series it was computed over -- nothing here invents a metric.
 *
 * A reading's null is a refusal (too few days), shown as "—" with the reason on hover, never 0.
 */

const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "—" : `${v >= 0 ? "" : "-"}${Math.abs(v * 100).toFixed(digits)}%`;
const num = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined ? "—" : v.toFixed(digits);
const tone = (v: number | null | undefined): "pos" | "neg" | "dim" =>
  v === null || v === undefined ? "dim" : v >= 0 ? "pos" : "neg";

const REFUSED = "needs at least 14 days (20 for CVaR) -- not measured yet, which is neither good nor bad";

export function fmtSeries(kind: SeriesKind): (v: number) => string {
  return kind === "nav" ? (v) => pct(v) : fmtMoney;
}

/** Twelve tiles of a compounding NAV's tear sheet. `days` is the count every tile is over. */
export function NavTiles({ reading }: { reading: NavReading }) {
  const days = reading.days;
  const span = reading.drawdownSpan;
  return (
    <TileGrid count={12}>
      <Tile label="CAGR" value={pct(reading.cagr)} tone={tone(reading.cagr)} n={days} nUnit="days" afterFees />
      <Tile label="total return" value={pct(reading.totalReturn)} tone={tone(reading.totalReturn)} afterFees />
      <Tile label="max drawdown" value={pct(reading.maxDrawdown)} tone="neg" />
      <Tile label="MAR (CAGR ÷ max DD)" value={num(reading.mar)} tone={tone(reading.mar)} />
      <Tile label="Sortino" value={num(reading.sortino)} tone={tone(reading.sortino)} title={reading.sortino == null ? REFUSED : undefined} />
      <Tile label="Sharpe" value={num(reading.sharpe)} tone={tone(reading.sharpe)} title={reading.sharpe == null ? REFUSED : undefined} />
      <Tile label="ulcer index" value={num(reading.ulcerIndex)} tone="dim" title="depth and duration of drawdowns together: the RMS of the % below peak" />
      <Tile
        label="longest drawdown"
        value={span === undefined ? "—" : `${String(span.longest)}d`}
        tone="dim"
        title={span === undefined ? undefined : `${String(span.open)} sessions below the peak right now`}
      />
      <Tile label="worst day" value={pct(reading.worstDay, 2)} tone="neg" />
      <Tile label="CVaR (worst 10%)" value={pct(reading.cvar, 2)} tone="neg" title={reading.cvar == null ? REFUSED : "mean of the worst tenth of days"} />
      <Tile label="P(Sharpe > 0)" value={reading.psr == null ? "—" : `${(reading.psr * 100).toFixed(0)}%`} tone="dim" title={reading.psr == null ? REFUSED : "probabilistic Sharpe ratio: skew and fat tails included"} />
      <Tile
        label="track record needed"
        value={reading.minTrackRecordDays == null ? "—" : `${String(reading.minTrackRecordDays)}d`}
        tone="dim"
        title="minimum track record length: days this Sharpe needs before it beats zero at 95%. None when there is no edge to confirm."
      />
    </TileGrid>
  );
}

/** Six tiles of a dollar equity path's tear sheet (curve's marked equity -- no account, no %). */
export function EquityTiles({ reading }: { reading: EquityReading }) {
  const span = reading.drawdownSpan;
  return (
    <TileGrid count={6}>
      <Tile label="marked net" value={fmtMoney(reading.net ?? null)} tone={tone(reading.net)} n={reading.days} nUnit="days" afterFees />
      <Tile label="max drawdown" value={fmtMoney(reading.maxDrawdown ?? null)} tone="neg" title="the deepest marked fall below a high, open positions included" />
      <Tile label="worst day" value={fmtMoney(reading.worstDay ?? null)} tone="neg" />
      <Tile label="CVaR (worst 10%)" value={fmtMoney(reading.cvar ?? null)} tone="neg" title={reading.cvar == null ? REFUSED : "mean of the worst tenth of days"} />
      <Tile
        label="longest drawdown"
        value={span === undefined ? "—" : `${String(span.longest)}d`}
        tone="dim"
        title={span === undefined ? undefined : `${String(span.open)} sessions below the peak right now`}
      />
      <Tile label="P(Sharpe > 0)" value={reading.psr == null ? "—" : `${(reading.psr * 100).toFixed(0)}%`} tone="dim" title={reading.psr == null ? REFUSED : undefined} />
    </TileGrid>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Year × month grid, each cell shaded by its sign and size. The year column sums (equity) or
 *  compounds (nav) its months. */
export function MonthlyHeatmap({ monthly, kind }: { monthly: Record<string, number> | undefined; kind: SeriesKind }) {
  const entries = Object.entries(monthly ?? {});
  if (entries.length === 0) return <p className="muted">no completed month yet</p>;
  const years = [...new Set(entries.map(([m]) => m.slice(0, 4)))].sort();
  const maxAbs = Math.max(...entries.map(([, v]) => Math.abs(v)), 1e-9);
  const fmt = fmtSeries(kind);
  const shade = (v: number) =>
    v >= 0 ? `rgba(67, 181, 122, ${(0.15 + 0.6 * (v / maxAbs)).toFixed(2)})` : `rgba(217, 92, 74, ${(0.15 + 0.6 * (-v / maxAbs)).toFixed(2)})`;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>year</th>
            {MONTHS.map((m) => (
              <th key={m}>{m}</th>
            ))}
            <th>year</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const cells = MONTHS.map((_, i) => monthly?.[`${y}-${String(i + 1).padStart(2, "0")}`]);
            const present = cells.filter((v): v is number => v !== undefined);
            const total = kind === "nav" ? present.reduce((t, v) => t * (1 + v), 1) - 1 : present.reduce((t, v) => t + v, 0);
            return (
              <tr key={y}>
                <td>{y}</td>
                {cells.map((v, i) => (
                  <td key={i} style={v === undefined ? undefined : { background: shade(v) }}>
                    {v === undefined ? "" : fmt(v)}
                  </td>
                ))}
                <td className={total >= 0 ? "pnl-pos" : "pnl-neg"}>{fmt(total)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export interface NamedSeries {
  label: string;
  data: DatedValue[];
  color?: string;
}

/** Each series' depth below its own running peak, on one axis. */
export function UnderwaterChart({ series, kind, height = 160 }: { series: NamedSeries[]; kind: SeriesKind; height?: number }) {
  const lines: TimeLineSeries[] = series.map((s) => ({
    label: s.label,
    color: s.color,
    points: underwater(s.data, kind),
  }));
  if (lines.every((l) => l.points.length < 2)) return <p className="muted">not enough history yet</p>;
  return <TimeLineChart series={lines} height={height} yFormat={fmtSeries(kind)} />;
}

/** The trailing `window`-session change of each series: is the edge steady, or one good month? */
export function RollingChart({
  series,
  window,
  kind,
  height = 160,
}: {
  series: NamedSeries[];
  window: number;
  kind: SeriesKind;
  height?: number;
}) {
  const lines: TimeLineSeries[] = series.map((s) => ({ label: s.label, color: s.color, points: rolling(s.data, window, kind) }));
  if (lines.every((l) => l.points.length < 2)) {
    return <p className="muted">the rolling {window}-session view starts once {window + 1} sessions are on file</p>;
  }
  return <TimeLineChart series={lines} height={height} yFormat={fmtSeries(kind)} />;
}

/** Each series inside each stress window: its change and its deepest fall. "not held" is a series
 *  with no point in the window -- a different fact from a window it rode out flat. */
export function StressTable({ series, windows, kind }: { series: NamedSeries[]; windows: StressWindow[]; kind: SeriesKind }) {
  const fmt = fmtSeries(kind);
  if (windows.length === 0) return <p className="muted">no stress window covers the period on file</p>;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-2">
        <thead>
          <tr>
            <th>window</th>
            <th>dates</th>
            {series.map((s) => (
              <th key={s.label}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {windows.map((w) => (
            <tr key={`${w.label}-${w.from}`}>
              <td>
                {w.label}
                {w.source === "inversion" && <span className="chip chip-warn integrity-chip">live</span>}
              </td>
              <td className="muted">
                {w.from} → {w.to}
              </td>
              {series.map((s) => {
                const r = inWindow(s.data, w, kind);
                return (
                  <td key={s.label} title={r.points === 0 ? "no point on file inside this window" : `${String(r.points)} sessions`}>
                    {r.points === 0 ? (
                      <span className="muted">not held</span>
                    ) : (
                      <>
                        <span className={(r.change ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}>{r.change === null ? "—" : fmt(r.change)}</span>
                        <span className="muted"> · dd {r.drawdown === null ? "—" : fmt(r.drawdown)}</span>
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The VIX/VIX3M ratio over time with each declared threshold drawn as a flat line. */
export function RegimeRatioChart({
  rows,
  thresholds,
  height = 200,
}: {
  rows: Array<{ tradeDate: string; ratio: number | null }>;
  thresholds: Array<{ label: string; value: number }>;
  height?: number;
}) {
  const pts = rows.filter((r): r is { tradeDate: string; ratio: number } => r.ratio !== null);
  if (pts.length < 2) return <p className="muted">not enough regime history yet</p>;
  const series: TimeLineSeries[] = [
    { label: "VIX/VIX3M", points: pts.map((r) => ({ x: r.tradeDate, y: r.ratio })) },
    ...thresholds.map((t) => ({
      label: t.label,
      color: "#8a8f98",
      points: [
        { x: pts[0]!.tradeDate, y: t.value },
        { x: pts[pts.length - 1]!.tradeDate, y: t.value },
      ],
    })),
  ];
  return <TimeLineChart series={series} height={height} yFormat={(v) => v.toFixed(3)} />;
}
