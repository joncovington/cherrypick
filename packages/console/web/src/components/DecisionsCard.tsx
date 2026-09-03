import { DataCard } from "./DataTable";
import { useDecisions, type DecisionsModule } from "../lib/api";

/**
 * The collapsed decision journal, one card shared by curve/pmcc/bwb (`readers/decisions.ts`'s own
 * docstring has the fuller "why this exists" -- the three modules all write
 * `core.ledgerstore.record_decision` from their loop but had no console reader before this). Every
 * row is a distinct (book, symbol, reason) this session, `occurrences` counting a gate that
 * refused the identical way tick after tick as one row rather than a flood of duplicates. Entered
 * rows (accepted) surface as a plain chip; every refusal keeps its own reason text so "why didn't
 * this book trade today" never comes down to a generic "no".
 */
export function DecisionsCard({ module }: { module: DecisionsModule }) {
  const { data, isLoading, isError, dataUpdatedAt } = useDecisions(module);
  return (
    <DataCard
      title={`Entry decisions${data?.tradeDate !== null && data?.tradeDate !== undefined ? ` (${data.tradeDate})` : ""}`}
      headers={["book", "symbol", "reason", "n", "detail"]}
      numFrom={3}
      loading={isLoading}
      isError={isError}
      rowCount={data?.rows.length ?? 0}
      empty="no decisions recorded this session"
      updatedAt={dataUpdatedAt}
    >
      {data?.rows.map((r, i) => (
        <tr key={`${r.book}-${r.symbol}-${r.reason}-${String(i)}`}>
          <td>{r.book}</td>
          <td>{r.symbol}</td>
          <td>
            {r.reason}
            {r.accepted && <span className="chip chip-ok" style={{ marginLeft: "0.4rem" }}>entered</span>}
          </td>
          <td>{r.occurrences}</td>
          <td className="muted" style={{ textAlign: "left" }}>{r.detail ?? "—"}</td>
        </tr>
      ))}
    </DataCard>
  );
}
