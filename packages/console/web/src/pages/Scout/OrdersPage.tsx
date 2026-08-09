import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { mutateJson } from "../../lib/api";
import { SkeletonRows, fmtMoney } from "../../components/DataTable";

interface TicketLeg {
  symbol: string;
  quantity: number;
  price: number;
}

interface StagedTicket {
  id: string;
  createdAt: string;
  symbol: string;
  strategy: string | null;
  legs: TicketLeg[];
  credit: number | null;
  maxRisk: number | null;
  dryRun: { ok: boolean; error?: string; account?: string } | null;
  note: string | null;
  status: string;
}

async function getStaged(): Promise<{ tickets: StagedTicket[] }> {
  const res = await fetch("/api/orders/staged");
  if (!res.ok) throw new Error(`staged: HTTP ${res.status}`);
  return (await res.json()) as { tickets: StagedTicket[] };
}

export function OrdersPage() {
  const { data, isLoading } = useQuery({ queryKey: ["staged"], queryFn: getStaged });
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => mutateJson(`/api/orders/staged/${id}`, "DELETE"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["staged"] }),
  });

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Staged tickets</h1>
        <span className="chip">dry-run only — execute manually in the platform</span>
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>created</th>
                <th>sym</th>
                <th>legs</th>
                <th>credit</th>
                <th>max risk</th>
                <th>dry-run</th>
                <th>account</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRows n={4} cols={8} />
              ) : data?.tickets.length === 0 ? (
                <tr>
                  <td colSpan={8} className="muted">
                    no staged tickets — build a position from the chain and stage it
                  </td>
                </tr>
              ) : (
                data?.tickets.map((t) => (
                  <tr key={t.id}>
                    <td className="muted">{t.createdAt.slice(0, 16).replace("T", " ")}</td>
                    <td>{t.symbol}</td>
                    <td>
                      {t.legs.map((l, i) => (
                        <div key={i}>
                          <span className={`chain-badge ${l.quantity < 0 ? "chain-badge-short" : "chain-badge-long"}`}>
                            {l.quantity < 0 ? "STO" : "BTO"} {Math.abs(l.quantity)}
                          </span>{" "}
                          {l.symbol.trim()} @ {l.price.toFixed(2)}
                        </div>
                      ))}
                    </td>
                    <td>{fmtMoney(t.credit)}</td>
                    <td>{fmtMoney(t.maxRisk)}</td>
                    <td>
                      {t.dryRun === null ? (
                        <span className="muted">—</span>
                      ) : t.dryRun.ok ? (
                        <span className="pnl-pos">validated</span>
                      ) : (
                        <span className="pnl-neg" title={t.dryRun.error}>
                          failed
                        </span>
                      )}
                    </td>
                    <td className="muted">{t.dryRun?.account ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-quiet"
                        onClick={() => remove.mutate(t.id)}
                        aria-label="delete ticket"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
