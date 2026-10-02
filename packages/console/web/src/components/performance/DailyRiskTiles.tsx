import { fmtMoney } from "../../lib/format";
import { Tile } from "./Tile";
import { TileGrid } from "./TileGrid";

/** `analytics/riskMetrics.ts`'s RiskSummary, as the flies and MEIC performance endpoints serve it. */
export interface DailyRisk {
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  recoveryFactor: number | null;
  sampleSize: number;
  undersampledFlag: boolean;
  sharpeOverfitFlag: boolean;
}

function ratio(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

function tone(v: number | null): "pos" | "neg" | "dim" {
  if (v === null) return "dim";
  return v >= 0 ? "pos" : "neg";
}

/**
 * The daily-series risk row both 0DTE performance tabs carry, from the one `riskSummary` — so the
 * flies and MEIC pages show the same six tiles in the same order and say the same thing about them.
 * They had drifted: flies showed sessions and max drawdown but no recovery factor, MEIC the reverse,
 * and only MEIC said when the sample was too thin to read.
 *
 * `maxDrawdown` is the page's own curve figure (peak-to-trough of cumulative DAILY net), passed in
 * rather than recomputed so the tile and the underwater chart beside it cannot disagree.
 */
export function DailyRiskTiles({ risk, maxDrawdown }: { risk: DailyRisk | undefined; maxDrawdown: number | null }) {
  const n = risk?.sampleSize ?? 0;
  return (
    <>
      <TileGrid count={6}>
        <Tile label="sharpe (daily, ann.)" value={ratio(risk?.sharpe ?? null)} tone={tone(risk?.sharpe ?? null)} n={n} nUnit="sessions" />
        <Tile label="sortino (daily, ann.)" value={ratio(risk?.sortino ?? null)} tone={tone(risk?.sortino ?? null)} n={n} nUnit="sessions" />
        <Tile label="calmar" value={ratio(risk?.calmar ?? null)} tone={tone(risk?.calmar ?? null)} />
        <Tile
          label="recovery factor"
          value={ratio(risk?.recoveryFactor ?? null)}
          tone={tone(risk?.recoveryFactor ?? null)}
          title="net P&L over the largest daily drawdown"
        />
        <Tile
          label="max drawdown (daily)"
          value={fmtMoney(maxDrawdown === null ? null : -maxDrawdown)}
          tone={maxDrawdown === null || maxDrawdown === 0 ? "dim" : "neg"}
          afterFees
        />
        <Tile label="sessions" value={risk === undefined ? "—" : String(n)} tone="dim" />
      </TileGrid>
      <p className="muted lbl" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
        Annualised on 252 sessions from {n} of them, against a $100k drawing base the books do not trade.
        {risk?.undersampledFlag === true && " Too few sessions for these ratios to mean anything yet; they describe this stretch, not the strategy."}
        {risk?.sharpeOverfitFlag === true && " Sharpe above 3 on a sample this small is a warning about the sample, not a stronger pass."}
      </p>
    </>
  );
}
