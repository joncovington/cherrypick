import { useEarnings } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";

export function EarningsPage() {
  const { data, isLoading, isError } = useEarnings();

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Earnings</h1>
        <span className="chip">both books</span>
      </div>

      <div className="cards cards-wide">
        <DataCard
          title="Trades"
          headers={["", "opened", "sym", "strategy", "exp", "credit", "qty", "closed", "P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.trades.length ?? 0}
          skeletonRows={8}
        >
          {data?.trades.map((t) => (
            <tr key={`${t.mode}-${t.orderId}`}>
              <td><PaperLiveBadge mode={t.mode} /></td>
              <td>{t.openedAt?.slice(0, 10) ?? "—"}</td>
              <td>{t.symbol}</td>
              <td>{t.strategy}</td>
              <td className="muted">{t.expiration ?? "—"}</td>
              <td>{fmtMoney(t.entryCredit)}</td>
              <td>{fmtNum(t.quantity, 0)}</td>
              <td className="muted">{t.closedAt?.slice(0, 10) ?? "open"}</td>
              <td><PnlCell v={t.pnl} /></td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Entry reviews (screened symbols)"
          headers={["", "scan", "sym", "timing", "winrate", "IV/RV", "exp move", "tier", "selected", "reason"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.reviews.length ?? 0}
          skeletonRows={10}
        >
          {data?.reviews.map((r, i) => (
            <tr key={`${r.mode}-${r.scanDate}-${r.symbol}-${i}`} className={r.selected ? "row-selected" : ""}>
              <td><PaperLiveBadge mode={r.mode} /></td>
              <td>{r.scanDate}</td>
              <td>{r.symbol}</td>
              <td className="muted">{r.timing ?? "—"}</td>
              <td>{fmtNum(r.winrate, 1)}</td>
              <td>{fmtNum(r.ivRvRatio, 2)}</td>
              <td>{fmtNum(r.expectedMove, 2)}</td>
              <td>{r.bestTier ?? "—"}</td>
              <td>{r.selected ? "✓" : ""}</td>
              <td className="muted">{r.reason ?? "—"}</td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
