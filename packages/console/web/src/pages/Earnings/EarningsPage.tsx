import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useEarnings } from "../../lib/api";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { TabStrip, Pager, usePage } from "../../components/ScopeBar";
import { EarningsDetailCards } from "./EarningsDetail";
import { EarningsLiveCard, EarningsManagementLog } from "./EarningsLive";

// "open" leads: with positions managed rather than force-closed the next morning, what is
// carrying risk right now is the question the page is most often opened to answer.
const TABS = ["open", "overview", "strategy detail", "upcoming"] as const;

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

interface EarningsAnalytics {
  kpis: { totalNet: number; closedTrades: number; expectancy: number | null; strategiesActive: number };
  openPositions: Array<{
    strategy: string;
    symbol: string;
    quantity: number | null;
    credit: number | null;
    netOfCost: number | null;
    maxLoss: number | null;
    entryCost: number | null;
    expiration: string | null;
  }>;
  weekly: Array<{ week: string; net: number }>;
  strategies: Array<{
    strategy: string;
    trades: number;
    winRatePct: number | null;
    profitFactor: number | null;
    expectancy: number | null;
    net: number;
  }>;
}

function useEarningsAnalytics() {
  return useQuery<EarningsAnalytics>({
    queryKey: ["earnings-analytics"],
    queryFn: async () => {
      const res = await fetch("/api/earnings/analytics?mode=paper");
      if (!res.ok) throw new Error(`earnings analytics: HTTP ${res.status}`);
      return (await res.json()) as EarningsAnalytics;
    },
    refetchInterval: 60_000,
  });
}

export function EarningsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("overview");
  const tradesPage = usePage();
  const reviewsPage = usePage();
  const { data, isLoading, isError, isPlaceholderData } = useEarnings(tradesPage.page, reviewsPage.page);
  const upcoming = useUpcoming();
  const analytics = useEarningsAnalytics();
  const a = analytics.data;
  const maxWeek = Math.max(...(a?.weekly.map((w) => Math.abs(w.net)) ?? [0]), 1);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Earnings</h1>
        <TabStrip tabs={TABS} value={tab} onChange={setTab} />
        <span className="chip">both books</span>
      </div>

      <div className="cards cards-wide">
        {tab === "open" && (
          <>
            <EarningsLiveCard />
            <EarningsManagementLog />
          </>
        )}
        {tab === "strategy detail" && <EarningsDetailCards mode="paper" />}
        {tab === "overview" && (
        <>
        <section className="card">
          <h2>Strategy test — paper</h2>
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">net expectancy / trade</span>
              <span className={`stat-value ${(a?.kpis.expectancy ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                {a?.kpis.expectancy != null ? fmtMoney(a.kpis.expectancy) : "—"}
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">total net P&L</span>
              <span className={`stat-value ${(a?.kpis.totalNet ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                {a !== undefined ? fmtMoney(a.kpis.totalNet) : "—"}
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">closed trades</span>
              <span className="stat-value">{a?.kpis.closedTrades ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">strategies active</span>
              <span className="stat-value">{a?.kpis.strategiesActive ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">capital at risk (open)</span>
              <span className="stat-value">
                {a !== undefined ? fmtMoney(a.openPositions.reduce((s, p) => s + Math.abs(p.maxLoss ?? 0), 0)) : "—"}
              </span>
            </div>
          </div>
          {a !== undefined && a.weekly.length > 0 && (
            <div style={{ display: "flex", gap: 4, alignItems: "flex-end", marginTop: "0.8rem", height: "3.6rem" }}>
              {a.weekly.slice(-16).map((w) => (
                <div key={w.week} title={`${w.week}: ${fmtMoney(w.net)}`} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}>
                  <div
                    style={{
                      height: `${Math.max(6, (Math.abs(w.net) / maxWeek) * 100)}%`,
                      background: w.net >= 0 ? "var(--ok)" : "var(--err)",
                      borderRadius: 2,
                      opacity: 0.75,
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </section>

        <DataCard
          title="Cross-strategy comparison (net of costs)"
          headers={["strategy", "trades", "win rate", "profit factor", "expectancy", "net"]}
          numFrom={1}
          loading={analytics.isLoading}
          rowCount={a?.strategies.length ?? 0}
        >
          {a?.strategies.map((s) => (
            <tr key={s.strategy}>
              <td>{s.strategy}</td>
              <td>{s.trades}</td>
              <td>{s.winRatePct !== null ? `${s.winRatePct.toFixed(0)}%` : "—"}</td>
              <td>{s.profitFactor !== null ? s.profitFactor.toFixed(2) : "—"}</td>
              <td>{s.expectancy !== null ? <PnlCell v={s.expectancy} /> : "—"}</td>
              <td><PnlCell v={s.net} /></td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Open positions"
          headers={["strategy", "sym", "qty", "credit/(debit)", "net of cost", "max loss", "entry cost", "exp"]}
          numFrom={1}
          loading={analytics.isLoading}
          rowCount={a?.openPositions.length ?? 0}
          empty="no open positions"
        >
          {a?.openPositions.map((p, i) => (
            <tr key={`${p.symbol}-${i}`}>
              <td>{p.strategy}</td>
              <td>{p.symbol}</td>
              <td>{fmtNum(p.quantity, 0)}</td>
              <td>{p.credit != null ? fmtMoney(p.credit) : "—"}</td>
              <td>{p.netOfCost != null ? <PnlCell v={p.netOfCost} /> : "—"}</td>
              <td>{p.maxLoss != null ? fmtMoney(-Math.abs(p.maxLoss)) : "—"}</td>
              <td className="muted">{p.entryCost != null ? fmtMoney(p.entryCost) : "—"}</td>
              <td className="muted">{p.expiration ?? "—"}</td>
            </tr>
          ))}
          {a !== undefined && a.openPositions.length > 0 && (
            <tr>
              <td colSpan={2} className="muted">{a.openPositions.length} open</td>
              <td />
              <td>{fmtMoney(a.openPositions.reduce((s, p) => s + (p.credit ?? 0), 0))}</td>
              <td><PnlCell v={a.openPositions.reduce((s, p) => s + (p.netOfCost ?? 0), 0)} /></td>
              <td className="pnl-neg">{fmtMoney(-a.openPositions.reduce((s, p) => s + Math.abs(p.maxLoss ?? 0), 0))}</td>
              <td className="muted">{fmtMoney(a.openPositions.reduce((s, p) => s + (p.entryCost ?? 0), 0))}</td>
              <td />
            </tr>
          )}
        </DataCard>

        </>
        )}

        {tab === "upcoming" && (
        <DataCard
          title={`Upcoming earnings (forward scan${upcoming.data && upcoming.data.total > 0 ? ` — ${upcoming.data.done}/${upcoming.data.total}` : ""})`}
          headers={["date", "sym", "timing", "price", "exp move", "IV/RV", "term", "winrate", "IVR", "tier"]}
          numFrom={1}
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

        )}

        {tab === "overview" && (
        <>
        <DataCard
          title={`Trades — ${(data?.trades.total ?? 0).toLocaleString()} across both books`}
          headers={["", "opened", "sym", "strategy", "exp", "credit", "qty", "closed", "P&L"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          busy={isPlaceholderData}
          rowCount={data?.trades.rows.length ?? 0}
          skeletonRows={8}
          footer={
            (data?.trades.total ?? 0) > 0 && (
              <Pager
                offset={data?.trades.offset ?? tradesPage.page.offset}
                limit={data?.trades.limit ?? tradesPage.page.limit}
                total={data?.trades.total ?? 0}
                onOffset={tradesPage.setOffset}
                onLimit={tradesPage.setLimit}
              />
            )
          }
        >
          {data?.trades.rows.map((t) => (
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
          title={`Entry reviews (screened symbols) — ${(data?.reviews.total ?? 0).toLocaleString()} across both books`}
          headers={["", "scan", "sym", "timing", "winrate", "IV/RV", "exp move", "tier", "selected", "reason"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          busy={isPlaceholderData}
          rowCount={data?.reviews.rows.length ?? 0}
          skeletonRows={10}
          footer={
            (data?.reviews.total ?? 0) > 0 && (
              <Pager
                offset={data?.reviews.offset ?? reviewsPage.page.offset}
                limit={data?.reviews.limit ?? reviewsPage.page.limit}
                total={data?.reviews.total ?? 0}
                onOffset={reviewsPage.setOffset}
                onLimit={reviewsPage.setLimit}
              />
            )
          }
        >
          {data?.reviews.rows.map((r, i) => (
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
        </>
        )}
      </div>
    </div>
  );
}
