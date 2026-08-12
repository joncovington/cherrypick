/**
 * The strategy card's human half: what this ticket is, what the greeks mean in dollars, the
 * pass/warn/fail checklist, and any event landing inside the chosen expiration.
 *
 * Two things this deliberately does NOT do:
 *
 * - It never presents a defined-risk Score and an undefined-risk one as the same number. The
 *   defined-risk form is externally validated against eleven observed cards; the undefined-risk
 *   form is the console's own estimate over a computed 2 SD risk, and it is labelled as such.
 * - It never turns an absent input into a green light. Every unknown grades **warn**, which is why
 *   a ticket built without a two-sided quote shows an ungraded liquidity row rather than a pass.
 */
import { useQuery } from "@tanstack/react-query";

interface CheckItem {
  name: string;
  status: "pass" | "warn" | "fail";
}

export interface StrategyReadoutData {
  direction: "bullish" | "bearish" | "neutral" | null;
  annualizedReturn: number | null;
  probWorthless: number | null;
  probableRisk2sd: number | null;
  score: number | null;
  scoreIsEstimated: boolean;
  comboSpreadPct: number | null;
  hasWeeklyCadence: boolean | null;
  explanation: string | null;
  greeksText: string | null;
  checklist: CheckItem[];
  checklistDirectional: CheckItem[] | null;
}

const STATUS_CLASS: Record<CheckItem["status"], string> = {
  pass: "chip-ok",
  warn: "chip-warn",
  fail: "chip-missing",
};

function useWarnings(symbol: string, expiration: string | null) {
  return useQuery<{ warnings: string[] }>({
    queryKey: ["symbol-warnings", symbol, expiration],
    queryFn: async () => {
      const res = await fetch(`/api/symbol/${symbol}/warnings?expiration=${expiration}`);
      if (!res.ok) throw new Error(`warnings: HTTP ${res.status}`);
      return (await res.json()) as { warnings: string[] };
    },
    enabled: expiration !== null,
    staleTime: 300_000,
  });
}

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function StrategyReadout({
  data,
  symbol,
  expiration,
}: {
  data: StrategyReadoutData;
  symbol: string;
  expiration: string | null;
}) {
  const warnings = useWarnings(symbol, expiration);
  // The directional checklist is the right one for a spread with a clear side; the premium-seller
  // checklist for everything else. Showing both would grade the same ticket twice.
  const items =
    data.direction === "neutral" || data.checklistDirectional === null
      ? data.checklist
      : data.checklistDirectional;

  return (
    <section className="card">
      <h2>Strategy</h2>

      {data.explanation && <p style={{ marginTop: 0 }}>{data.explanation}</p>}

      <div className="stat-row" style={{ marginBottom: 12 }}>
        {data.annualizedReturn !== null && (
          <span className="chip">
            annualized {pct(data.annualizedReturn)}
            <span className="muted">*</span>
          </span>
        )}
        {data.probWorthless !== null && <span className="chip">POW {pct(data.probWorthless)}</span>}
        {data.score !== null && (
          <span className="chip">
            score {data.score.toFixed(0)}
            {data.scoreIsEstimated ? <span className="muted"> est</span> : null}
          </span>
        )}
        {data.comboSpreadPct !== null && (
          <span className="chip">spread {pct(data.comboSpreadPct, 1)} of mid</span>
        )}
        {data.probableRisk2sd !== null && data.probableRisk2sd > 0 && (
          <span className="chip">2σ risk ${data.probableRisk2sd.toFixed(0)}</span>
        )}
      </div>

      {items.length > 0 && (
        <>
          <div className="fine-label">Checklist</div>
          <div className="stat-row" style={{ margin: "4px 0 12px" }}>
            {items.map((i) => (
              <span key={i.name} className={`chip ${STATUS_CLASS[i.status]}`}>
                {i.name}
              </span>
            ))}
          </div>
        </>
      )}

      {data.greeksText && (
        <p className="muted" style={{ fontSize: 12 }}>
          {data.greeksText}
        </p>
      )}

      {warnings.data && warnings.data.warnings.length > 0 && (
        <>
          <div className="fine-label">Events inside this expiration</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {warnings.data.warnings.map((w) => (
              <li key={w} className="pnl-neg" style={{ marginBottom: 4 }}>
                {w}
              </li>
            ))}
          </ul>
        </>
      )}

      {data.annualizedReturn !== null && (
        <p className="muted" style={{ fontSize: 11, marginBottom: 0 }}>
          * Annualized assumes the same trade repeats back-to-back all year at the same return — a
          comparison metric, not a forecast.
          {data.hasWeeklyCadence === false
            ? " This chain has no weekly cadence, so the liquidity row caps at warn."
            : ""}
        </p>
      )}
    </section>
  );
}
