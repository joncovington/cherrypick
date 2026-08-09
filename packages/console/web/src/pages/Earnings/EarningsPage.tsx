import { useQuery } from "@tanstack/react-query";
import { useEarnings } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";

interface UpcomingRow {
  symbol: string;
  earningsDate: string;
  timing: string | null;
  price: number | null;
  expectedMovePct: number | null;
  ivRvRatio: number | null;
  termStructure: number | null;
  winrate: number | null;
  ivRank: number | null;
  tier: string;
  tierReasons: string[];
}

interface UpcomingPayload {
  passCompletedAt: number | null;
  done: number;
  total: number;
  rows: UpcomingRow[];
}

function useUpcoming() {
  return useQuery<UpcomingPayload>({
    queryKey: ["earnings-upcoming"],
    queryFn: async () => {
      const res = await fetch("/api/earnings/upcoming");
      if (!res.ok) throw new Error(`upcoming: HTTP ${res.status}`);
      return (await res.json()) as UpcomingPayload;
    },
    refetchInterval: 60_000,
  });
}

function tierClass(tier: string): string {
  if (tier === "recommended") return "chip-ok";
  if (tier === "near_miss") return "chip-warn";
  return "";
}

export function EarningsPage() {
  const { data, isLoading, isError } = useEarnings();
  const upcoming = useUpcoming();

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Earnings</h1>
        <span className="chip">both books</span>
      </div>

      <div className="cards cards-wide">
        <DataCard
          title={`Upcoming earnings (forward scan${upcoming.data && upcoming.data.total > 0 ? ` — ${upcoming.data.done}/${upcoming.data.total}` : ""})`}
          headers={["date", "sym", "timing", "price", "exp move", "IV/RV", "term", "winrate", "IVR", "tier"]}
          loading={upcoming.isLoading}
          isError={upcoming.isError}
          rowCount={upcoming.data?.rows.length ?? 0}
          skeletonRows={6}
          empty="no forward scan yet — the earnings module's scheduled symbol_watch refresh writes this"
        >
          {upcoming.data?.rows.map((r) => (
            <tr key={`${r.earningsDate}-${r.symbol}`}>
              <td>{r.earningsDate}</td>
              <td>{r.symbol}</td>
              <td className="muted">{r.timing ?? "—"}</td>
              <td>{fmtNum(r.price, 2)}</td>
              <td>{r.expectedMovePct !== null ? `${(r.expectedMovePct * 100).toFixed(1)}%` : "—"}</td>
              <td>{fmtNum(r.ivRvRatio, 2)}</td>
              <td>{fmtNum(r.termStructure, 2)}</td>
              <td>{r.winrate !== null ? `${(r.winrate * 100).toFixed(0)}%` : "—"}</td>
              <td>{r.ivRank !== null ? (r.ivRank * 100).toFixed(0) : "—"}</td>
              <td>
                <span className={`chip ${tierClass(r.tier)}`} title={r.tierReasons.join("; ")}>
                  {r.tier.replace("_", " ")}
                </span>
              </td>
            </tr>
          ))}
        </DataCard>

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
