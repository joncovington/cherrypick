import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useEarnings } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { IntegrityStrip } from "../../pages/Earnings/IntegrityStrip";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { fmtCash, fmtIvr, fmtPrice } from "../../lib/format";
import { EarningsDetailCards } from "../../pages/Earnings/EarningsDetail";
import { EarningsHistory } from "../../pages/Earnings/EarningsHistory";
import { useUrlDateRange } from "../../components/table/DateRange";
import { EarningsLiveCard, EarningsManagementLog } from "../../pages/Earnings/EarningsLive";
import { EarningsSession, type EarningsAnalytics } from "../../pages/Earnings/EarningsSession";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { ModuleFrame } from "../ModuleFrame";
import { EARNINGS_SLIDES, type EarningsSlideId } from "../navGroups";
import type { SlideDef } from "../types";

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

const EARNINGS_LABEL = Object.fromEntries(EARNINGS_SLIDES.map((s) => [s.id, s.label])) as Record<EarningsSlideId, string>;

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

function useEarningsAnalytics(mode: TradingMode, era: string | null) {
  return useQuery<EarningsAnalytics>({
    queryKey: ["earnings-analytics", mode, era],
    queryFn: async () => {
      const res = await fetch(`/api/earnings/analytics?mode=${mode}${era !== null ? `&era=${era}` : ""}`);
      if (!res.ok) throw new Error(`earnings analytics: HTTP ${res.status}`);
      return (await res.json()) as EarningsAnalytics;
    },
    refetchInterval: 60_000,
  });
}

function EraScope({ era, onChange }: { era: string | null; onChange: (v: string | null) => void }) {
  return (
    <select
      className={`text-input ${era === null ? "" : "scope-select-off-default"}`}
      value={era ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      aria-label="era"
      title="The advisor era began 2026-08-21, when every hand-designed variant retired and management params came under advisor experiments. Earlier trades ran under different rules — pooling them reads as one experiment when it is really two."
    >
      <option value="">this era (default)</option>
      <option value="ALL">all history</option>
    </select>
  );
}

/**
 * earnings on the module frame (2026-09-25): a left rail of pages, and nothing on the surface opens
 * an overlay. The tab names and the reasons for them are declared in `navGroups.ts`.
 */
export function EarningsLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  const [era, setEra] = useState<string | null>(null);
  // The history's date range, in the page address; it bounds the history table and its totals only.
  const range = useUrlDateRange();
  const tradesPage = usePage([era, range.from, range.to]);
  const reviewsPage = usePage();
  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useEarnings(tradesPage.page, reviewsPage.page, era, {
    from: range.from,
    to: range.to,
  });
  const upcoming = useUpcoming();
  const analytics = useEarningsAnalytics(mode, era);
  const a = analytics.data;
  const t = data?.totals;

  const slides: Array<SlideDef & { id: EarningsSlideId }> = [
    {
      id: "session",
      label: EARNINGS_LABEL.session,
      render: () => (
        <EarningsSession
          analytics={a}
          loading={analytics.isLoading}
          recommended={upcoming.data === undefined ? null : upcoming.data.rows.filter((r) => r.tier === "recommended").length}
        />
      ),
    },
    {
      id: "decisions",
      label: EARNINGS_LABEL.decisions,
      render: () => (
        <div className="cards cards-wide">
          <EarningsManagementLog />
        </div>
      ),
    },
    {
      id: "strategies",
      label: EARNINGS_LABEL.strategies,
      render: () => (
        <div className="cards cards-wide">
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
          <EarningsDetailCards mode={mode} era={era} />
        </div>
      ),
    },
    {
      id: "screening",
      label: EARNINGS_LABEL.screening,
      render: () => (
        <DataCard
          title={`Entry reviews (screened symbols) — ${(data?.reviews.total ?? 0).toLocaleString()} across both books`}
          headers={["", "scan", "sym", "timing", "winrate", "IV/RV", "exp move", "selected", "reason"]}
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
              <td>{r.selected ? "✓" : ""}</td>
              <td className="muted">{r.reason ?? "—"}</td>
            </tr>
          ))}
        </DataCard>
      ),
    },
    {
      id: "upcoming",
      label: EARNINGS_LABEL.upcoming,
      render: () => (
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
              <td>{fmtIvr(r.ivRank)}</td>
              <td>
                <span className={`chip ${tierClass(r.tier)}`} title={r.tierReasons.join("; ")}>
                  {r.tier.replace("_", " ")}
                </span>
              </td>
            </tr>
          ))}
        </DataCard>
      ),
    },
    // Always reads the paper ledger regardless of the page's own mode toggle -- calibrate's own
    // "paper only" rule for promotion evidence, same as every other module's performance slide.
    { id: "performance", label: EARNINGS_LABEL.performance, render: () => <PerformanceSlide module="earnings" /> },
    { id: "advisor", label: EARNINGS_LABEL.advisor, render: () => <AdvisorSlide module="earnings" /> },
    {
      id: "positions",
      label: EARNINGS_LABEL.positions,
      render: () => (
        <div className="cards cards-wide">
          <EarningsLiveCard />
          <DataCard
            title={`Open positions — ${mode} book, entry side`}
            headers={["strategy", "sym", "exp", "qty", "price", "entry", "entry cost", "max loss"]}
            numFrom={3}
            loading={analytics.isLoading}
            rowCount={a?.openPositions.length ?? 0}
            empty="no open positions"
          >
            {a?.openPositions.map((p, i) => (
              <tr key={`${p.symbol}-${i}`}>
                <td>{p.strategy}</td>
                <td>{p.symbol}</td>
                <td className="muted">{p.expiration ?? "—"}</td>
                <td>{fmtNum(p.quantity, 0)}</td>
                <td>{fmtPrice(p.price)}</td>
                <td>{fmtCash(p.credit)}</td>
                <td className="muted" title="entry fees and slippage charged so far">{fmtMoney(p.entryCost)}</td>
                <td>{p.maxLoss != null ? fmtMoney(-Math.abs(p.maxLoss)) : "—"}</td>
              </tr>
            ))}
          </DataCard>
        </div>
      ),
    },
    {
      id: "history",
      label: EARNINGS_LABEL.history,
      render: () => (
        <EarningsHistory
          data={data}
          loading={isLoading}
          isError={isError}
          busy={isPlaceholderData}
          ranged={range.from !== null || range.to !== null}
          page={{ ...tradesPage.page, setOffset: tradesPage.setOffset, setLimit: tradesPage.setLimit }}
        />
      ),
    },
  ];

  return (
    <ModuleFrame
      module="earnings"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      session={null}
      headerControls={
        <>
          <ModeToggle mode={mode} onChange={setMode} />
          <EraScope era={era} onChange={setEra} />
        </>
      }
      integrity={<IntegrityStrip data={data} updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity.measurementBreaks.length ?? 0) > 0 || (data?.integrity.schemaDrift.length ?? 0) > 0}
    />
  );
}
