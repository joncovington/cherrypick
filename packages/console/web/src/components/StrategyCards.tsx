import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fmtMoney } from "./DataTable";

export interface ApiLeg {
  kind: "call" | "put" | "stock";
  strike: number | null;
  quantity: number;
  price: number;
  delta: number | null;
  expiration: string | null;
  occSymbol: string | null;
}

interface SuggestionCard {
  name: string;
  label: string;
  legs: ApiLeg[];
  credit: number;
  maxProfit: { value: number | null; unbounded: boolean };
  maxRisk: { value: number | null; unbounded: boolean };
  breakevens: number[];
  pop: number | null;
}

interface SuggestionsPayload {
  expiration?: string;
  dte?: number;
  tradeDate?: string;
  cards?: SuggestionCard[];
  error?: string;
}

interface IncomeCell {
  strike: number;
  delta: number;
  mid: number | null;
  expiration: string;
  dte: number;
  pow: number | null;
  annualizedReturn: number | null;
  occSymbol: string;
}

interface IncomeGridPayload {
  tradeDate?: string;
  buckets?: Array<{ name: string; expiration: string; dte: number; tiers: Record<string, IncomeCell> }>;
  error?: string;
}

const SENTIMENTS = [
  ["bullish", "Bullish"],
  ["bearish", "Bearish"],
  ["high_iv", "High IV"],
] as const;

const TIERS = ["conservative", "optimal", "aggressive"] as const;
const BUCKET_LABELS: Record<string, string> = { short: "20–39d", medium: "40–70d", long: "71–180d" };

function ext(e: { value: number | null; unbounded: boolean }): string {
  return e.unbounded ? "unbounded" : fmtMoney(e.value);
}

/** Sentiment suggestion cards + the short-put income grid, both built from the
 *  EOD chain snapshot server-side. Clicking a card loads its legs into the editor. */
export function StrategyCards({
  symbol,
  onApply,
}: {
  symbol: string;
  onApply: (legs: ApiLeg[], expiration: string) => void;
}) {
  const [sentiment, setSentiment] = useState<string>("bullish");
  const valid = /^[A-Z][A-Z0-9./]{0,9}$/.test(symbol);

  const sugg = useQuery<SuggestionsPayload>({
    queryKey: ["builder-suggestions", symbol, sentiment],
    queryFn: async () =>
      (await fetch(`/api/builder/suggestions/${encodeURIComponent(symbol)}?sentiment=${sentiment}`)).json() as Promise<SuggestionsPayload>,
    enabled: valid,
    staleTime: 300_000,
    placeholderData: (prev) => prev,
  });

  const grid = useQuery<IncomeGridPayload>({
    queryKey: ["builder-income-grid", symbol],
    queryFn: async () =>
      (await fetch(`/api/builder/income-grid/${encodeURIComponent(symbol)}?kind=put`)).json() as Promise<IncomeGridPayload>,
    enabled: valid,
    staleTime: 300_000,
    placeholderData: (prev) => prev,
  });

  return (
    <div className="cards cards-wide">
      <section className="card">
        <div className="page-title-row">
          <h2 style={{ margin: 0 }}>Suggestions</h2>
          {SENTIMENTS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={sentiment === key ? "btn" : "btn btn-quiet"}
              onClick={() => setSentiment(key)}
            >
              {label}
            </button>
          ))}
          {sugg.data?.expiration !== undefined && (
            <span className="chip">
              {sugg.data.expiration} ({sugg.data.dte}d) · EOD {sugg.data.tradeDate}
            </span>
          )}
        </div>
        {sugg.data?.error !== undefined ? (
          <p className="muted">{sugg.data.error}</p>
        ) : (sugg.data?.cards ?? []).length === 0 ? (
          <p className="muted">no viable structures for this expiration</p>
        ) : (
          <div className="stats-grid">
            {sugg.data!.cards!.map((c) => (
              <button
                key={c.name}
                type="button"
                className="stat-tile"
                style={{ cursor: "pointer", textAlign: "left", background: "none" }}
                onClick={() => onApply(c.legs, c.legs[0]?.expiration ?? "")}
              >
                <span className="stat-label">{c.label}</span>
                <span className="stat-value">
                  {c.credit >= 0 ? `${fmtMoney(c.credit)} cr` : `${fmtMoney(-c.credit)} db`}
                </span>
                <span className="muted" style={{ display: "block", fontSize: 12 }}>
                  max risk {ext(c.maxRisk)} · POP {c.pop !== null ? `${(c.pop * 100).toFixed(0)}%` : "—"}
                </span>
                <span className="muted" style={{ display: "block", fontSize: 12 }}>
                  {c.legs
                    .map((l) => `${l.quantity > 0 ? "+" : ""}${l.quantity} ${l.strike ?? ""}${l.kind === "call" ? "C" : "P"}`)
                    .join(" / ")}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <div className="page-title-row">
          <h2 style={{ margin: 0 }}>Short put grid</h2>
          <span className="muted">risk tolerance × time · click to load</span>
          {grid.data?.tradeDate !== undefined && <span className="chip">EOD {grid.data.tradeDate}</span>}
        </div>
        {grid.data?.error !== undefined ? (
          <p className="muted">{grid.data.error}</p>
        ) : (grid.data?.buckets ?? []).length === 0 ? (
          <p className="muted">no expirations inside the 20–180 day windows in the snapshot</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>tier</th>
                  {grid.data!.buckets!.map((b) => (
                    <th key={b.name}>
                      {BUCKET_LABELS[b.name] ?? b.name} <span className="muted">({b.dte}d)</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {TIERS.map((tier) => (
                  <tr key={tier}>
                    <td className="muted">{tier}</td>
                    {grid.data!.buckets!.map((b) => {
                      const cell = b.tiers[tier];
                      if (cell === undefined) return <td key={b.name}>—</td>;
                      return (
                        <td key={b.name}>
                          <button
                            type="button"
                            className="btn btn-quiet"
                            onClick={() =>
                              onApply(
                                [
                                  {
                                    kind: "put",
                                    strike: cell.strike,
                                    quantity: -1,
                                    price: cell.mid ?? 0,
                                    delta: -cell.delta,
                                    expiration: cell.expiration,
                                    occSymbol: cell.occSymbol,
                                  },
                                ],
                                cell.expiration,
                              )
                            }
                            title={`Δ ${cell.delta}${cell.annualizedReturn !== null ? ` · ${(cell.annualizedReturn * 100).toFixed(0)}%/yr` : ""}`}
                          >
                            {cell.strike}p {cell.mid !== null ? `@ ${cell.mid.toFixed(2)}` : ""}
                            {cell.pow !== null ? ` · ${(cell.pow * 100).toFixed(0)}%` : ""}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
