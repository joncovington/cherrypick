import { fmtMoney, fmtNum, fmtPct } from "../../lib/format";
import { Tile } from "./Tile";

/**
 * The core calibration-reading tile row -- one `reading` object (a group's `calibration_reading`
 * dict, snake_case field names verbatim per `lib/api.ts::ModulePerformanceResult`'s own
 * convention). This is the suite-wide half every module's performance slide shares; a module's
 * own bespoke extras (MEIC's profile table, flies' completion/roll cards, ...) stay in that
 * module's own tab and are not reproduced here.
 *
 * Every accessor tolerates a missing/mistyped key rather than throwing -- an older reading (from a
 * console build that predates a metric) or a differently-shaped one must degrade to an em-dash,
 * never crash the slide.
 */

function num(reading: Record<string, unknown>, key: string): number | null {
  const v = reading[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function count(reading: Record<string, unknown>, key: string): number | null {
  const v = reading[key];
  return typeof v === "number" && Number.isInteger(v) ? v : null;
}

/** A nested `{value, n}` or `{median, n}` reading -- `capture_rate`, `max_profit_pct`,
 * `max_loss_pct`'s own shape (core.metrics.py). */
function nested(reading: Record<string, unknown>, key: string, valueKey: "value" | "median"): { v: number | null; n: number | null } {
  const raw = reading[key];
  if (typeof raw !== "object" || raw === null) return { v: null, n: null };
  const obj = raw as Record<string, unknown>;
  const v = typeof obj[valueKey] === "number" ? (obj[valueKey] as number) : null;
  const n = typeof obj["n"] === "number" ? (obj["n"] as number) : null;
  return { v, n };
}

function tone(v: number | null): "pos" | "neg" | "dim" | undefined {
  if (v === null) return "dim";
  return v >= 0 ? "pos" : "neg";
}

/** calibration_reading's win_rate/return_on_capital/capture_rate are FRACTIONS (0.0567, not 5.67)
 * -- fmtPct expects an already-scaled percent, so every fraction-shaped reading is scaled here
 * rather than at the call site, where it would be one easy digit to drop. */
function pctFraction(v: number | null): number | null {
  return v === null ? null : v * 100;
}

export function MetricTiles({ reading }: { reading: Record<string, unknown> }) {
  const sample = count(reading, "sample");
  const netPnl = num(reading, "net_pnl");
  const winRate = num(reading, "win_rate");
  const expectancy = num(reading, "expectancy");
  const profitFactor = num(reading, "profit_factor");
  const sharpe = num(reading, "sharpe");
  const maxDrawdown = num(reading, "max_drawdown");
  const returnOnCapital = num(reading, "return_on_capital");
  const captureRate = nested(reading, "capture_rate", "value");

  return (
    <div className="stats-grid">
      <Tile label="sample" value={sample === null ? "—" : String(sample)} tone="dim" />
      <Tile label="net P&L" value={fmtMoney(netPnl)} tone={tone(netPnl)} afterFees n={sample} />
      <Tile label="win rate" value={fmtPct(pctFraction(winRate), 1)} tone={tone(winRate === null ? null : winRate - 0.5)} n={sample} />
      <Tile label="expectancy" value={fmtMoney(expectancy)} tone={tone(expectancy)} afterFees n={sample} />
      <Tile label="profit factor" value={fmtNum(profitFactor)} tone={tone(profitFactor === null ? null : profitFactor - 1)} n={sample} />
      <Tile label="sharpe" value={fmtNum(sharpe)} tone={tone(sharpe)} n={sample} />
      <Tile label="max drawdown" value={fmtMoney(maxDrawdown === null ? null : -maxDrawdown)} tone={maxDrawdown === null || maxDrawdown === 0 ? "dim" : "neg"} afterFees />
      <Tile label="return on capital" value={fmtPct(pctFraction(returnOnCapital), 1)} tone={tone(returnOnCapital)} n={sample} />
      <Tile label="capture rate" value={fmtPct(pctFraction(captureRate.v), 1)} tone={tone(captureRate.v)} n={captureRate.n} />
    </div>
  );
}
