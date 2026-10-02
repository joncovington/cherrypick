import type { OnPeakRisk } from "@console/shared";
import { fmtMoney, fmtNum, fmtPct } from "../../lib/format";
import { Tile } from "./Tile";
import { TileGrid } from "./TileGrid";

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

/** `worst_session`'s `{session, net}` (core.metrics), or null when absent or misshapen. */
function worstSession(reading: Record<string, unknown>): { session: string; net: number } | null {
  const raw = reading["worst_session"];
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  return typeof obj["session"] === "string" && typeof obj["net"] === "number" ? { session: obj["session"], net: obj["net"] } : null;
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

/**
 * `peakRisk` is flies' stand-in for return on capital, which its ledger cannot carry (a legged
 * book's risk depends on completion): Σ net over Σ session peak risk. Passed, it takes that tile's
 * place; every other module passes nothing and keeps return on capital.
 */
export function MetricTiles({ reading, peakRisk }: { reading: Record<string, unknown>; peakRisk?: OnPeakRisk }) {
  const sample = count(reading, "sample");
  const netPnl = num(reading, "net_pnl");
  const winRate = num(reading, "win_rate");
  const expectancy = num(reading, "expectancy");
  const profitFactor = num(reading, "profit_factor");
  const sharpe = num(reading, "sharpe");
  const sessionSharpe = num(reading, "session_sharpe");
  const psr = num(reading, "psr");
  const sessions = count(reading, "sessions");
  const worst = worstSession(reading);
  const maxDrawdown = num(reading, "max_drawdown");
  const returnOnCapital = num(reading, "return_on_capital");
  const captureRate = nested(reading, "capture_rate", "value");

  // Twelve tiles, so TileGrid's 2/3/4/6 columns always come out even. The first six are edge, the
  // second six shape and risk; the per-session pair sits beside the per-trade Sharpe it corrects.
  return (
    <TileGrid count={12}>
      <Tile label="sample" value={sample === null ? "—" : String(sample)} tone="dim" />
      <Tile label="net P&L" value={fmtMoney(netPnl)} tone={tone(netPnl)} afterFees n={sample} />
      <Tile label="win rate" value={fmtPct(pctFraction(winRate), 1)} tone={tone(winRate === null ? null : winRate - 0.5)} n={sample} />
      <Tile label="expectancy" value={fmtMoney(expectancy)} tone={tone(expectancy)} afterFees n={sample} />
      <Tile label="profit factor" value={fmtNum(profitFactor)} tone={tone(profitFactor === null ? null : profitFactor - 1)} n={sample} />
      {peakRisk !== undefined ? (
        <Tile
          label="return on peak risk"
          value={fmtPct(pctFraction(peakRisk.ratio), 1)}
          tone={tone(peakRisk.ratio)}
          n={peakRisk.sessions}
          nUnit="sessions"
          afterFees
          title={
            `settled net ${fmtMoney(peakRisk.net)} over the sum of each session's peak worst-case exposure ` +
            `${fmtMoney(peakRisk.peakRisk)}; ${String(peakRisk.sessions)} of ${String(peakRisk.of)} sessions ` +
            `(a session counts once finished and once it carried risk)` +
            (peakRisk.replayed > 0 ? `; ${String(peakRisk.replayed)} replayed from positions, the rest recorded live` : "; every peak recorded live")
          }
        />
      ) : (
        <Tile label="return on capital" value={fmtPct(pctFraction(returnOnCapital), 1)} tone={tone(returnOnCapital)} n={sample} />
      )}
      <Tile label="capture rate" value={fmtPct(pctFraction(captureRate.v), 1)} tone={tone(captureRate.v)} n={captureRate.n} />
      <Tile
        label="max drawdown (per trade)"
        value={fmtMoney(maxDrawdown === null ? null : -maxDrawdown)}
        tone={maxDrawdown === null || maxDrawdown === 0 ? "dim" : "neg"}
        afterFees
        title="peak-to-trough of the running net, trade by trade in session order"
      />
      <Tile
        label="worst session"
        value={fmtMoney(worst?.net ?? null)}
        tone={tone(worst?.net ?? null)}
        n={sessions}
        nUnit="sessions"
        afterFees
        title={worst !== null ? `the single worst day's net, ${worst.session}` : undefined}
      />
      <Tile
        label="sharpe / trade"
        value={fmtNum(sharpe)}
        tone={tone(sharpe)}
        n={sample}
        title="mean over stdev of per-trade net. Entries on one day are not independent, so this reads steadier than the book; the per-session figure is the risk the arm ran"
      />
      <Tile
        label="sharpe / session"
        value={fmtNum(sessionSharpe)}
        tone={tone(sessionSharpe)}
        n={sessions}
        nUnit="sessions"
        title="mean over stdev of per-session net, not annualised (×√252 for a daily book, ×√52 for a weekly one). Refused below the suite's minimum effective sample of sessions"
      />
      <Tile
        label="P(sharpe > 0)"
        value={fmtPct(pctFraction(psr), 1)}
        tone={psr === null ? "dim" : tone(sessionSharpe)}
        n={sessions}
        nUnit="sessions"
        title="probabilistic Sharpe ratio: the chance the true per-session Sharpe is above zero, given how many sessions there are and how skewed and fat-tailed they were. Short premium's rare large loss lowers it below what the Sharpe alone suggests"
      />
    </TileGrid>
  );
}
