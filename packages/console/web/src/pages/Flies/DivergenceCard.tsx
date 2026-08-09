import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { DataCard, fmtPct } from "../../components/DataTable";
import { fliesQuery, type FliesFilter } from "../../lib/api";

interface Divergence {
  date: string | null;
  iterations: number;
  allAgreeRatePct: number | null;
  pairs: Array<{ arms: string; iterations: number; agreementRatePct: number | null }>;
}

/**
 * Arm divergence: how often the arms actually picked different centres. The
 * experiment can only separate two arms to the extent they disagree — a pair
 * agreeing above 80% is flagged, because that comparison cannot answer the
 * question as framed no matter how long it runs.
 */
export function DivergenceCard({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const { data, isLoading, dataUpdatedAt } = useQuery<Divergence>({
    queryKey: ["flies-divergence", mode, filter.date],
    queryFn: async () => {
      const res = await fetch(`/api/flies/divergence?${fliesQuery(mode, { arm: null, date: filter.date })}`);
      if (!res.ok) throw new Error(`divergence: HTTP ${res.status}`);
      return (await res.json()) as Divergence;
    },
    refetchInterval: 60_000,
  });

  const allAgree = data?.allAgreeRatePct ?? null;
  return (
    <DataCard
      title={`Arm divergence${data?.date != null ? ` (${data.date})` : ""}`}
      headers={["pair", "iterations", "agreed %", ""]}
      numFrom={1}
      loading={isLoading}
      rowCount={data?.pairs.length ?? 0}
      updatedAt={dataUpdatedAt}
      empty="no iterations with two or more arms quoting"
      controls={
        allAgree !== null ? (
          <span className={`chip ${allAgree > 80 ? "chip-warn" : ""}`}>
            all arms agreed on {allAgree.toFixed(0)}% of {data?.iterations ?? 0} iterations
          </span>
        ) : undefined
      }
    >
      {data?.pairs.map((p) => (
        <tr key={p.arms}>
          <td>{p.arms}</td>
          <td className="muted">{p.iterations}</td>
          <td className={p.agreementRatePct !== null && p.agreementRatePct > 80 ? "pnl-neg" : ""}>
            {fmtPct(p.agreementRatePct)}
          </td>
          <td>
            {p.agreementRatePct !== null && p.agreementRatePct > 80 && (
              <span className="chain-badge chain-badge-short">hard to tell apart</span>
            )}
          </td>
        </tr>
      ))}
    </DataCard>
  );
}
