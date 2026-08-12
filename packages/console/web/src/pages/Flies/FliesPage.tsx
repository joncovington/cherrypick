import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useFlies, useFliesMeta, fliesQuery, type FliesFilter } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import type { TradingMode } from "@console/shared";
import { ForestCard } from "./ForestCard";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { OccupancyMap } from "../../components/OccupancyMap";
import { TimelineCard } from "./TimelineCard";
import { HistoryTab } from "./HistoryTab";
import { JournalCard } from "./JournalCard";
import { DivergenceCard } from "./DivergenceCard";
import { PerformanceTab } from "./PerformanceTab";

interface FliesAnalytics {
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
    maxPossibleLoss: number;
  };
  byArm: Array<{ arm: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  feeDrag: Array<{ arm: string; gross: number; fees: number; net: number; dragPct: number | null }>;
}

function useFliesAnalytics(mode: TradingMode, filter: FliesFilter) {
  return useQuery<FliesAnalytics>({
    queryKey: ["flies-analytics", mode, filter],
    queryFn: async () => {
      const res = await fetch(`/api/flies/analytics?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`flies analytics: HTTP ${res.status}`);
      return (await res.json()) as FliesAnalytics;
    },
    refetchInterval: 30_000,
  });
}

type FliesTab = "today" | "history" | "performance";

export function FliesPage() {
  const [mode, setMode] = useMode();
  const [arm, setArm] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  // null = the module's current era (SPX from 2026-08-01), matching what its own analytics
  // count as evidence. "ALL" reaches the XSP books too — a different trade at 1/5 the width and
  // 4x the fee drag — so widening is a stated choice, never the quiet default.
  const [era, setEra] = useState<string | null>(null);
  const [tab, setTab] = useState<FliesTab>("today");
  const filter: FliesFilter = { arm, date, era };
  const meta = useFliesMeta(mode, era);
  // Two tables on one payload, each with its own page — turning one leaves the
  // other where it was. Both reset when the filter changes underneath them.
  const booksPage = usePage([mode, arm, date, era]);
  const positionsPage = usePage([mode, arm, date, era]);
  const { data, isLoading, isError, isPlaceholderData } = useFlies(mode, filter, booksPage.page, positionsPage.page);
  const analytics = useFliesAnalytics(mode, filter);
  const a = analytics.data;

  // Narrowing the era can remove the arm or date currently selected (width-2/3/4 are XSP-only).
  // Clear a selection the new scope no longer offers, so the page never filters on a value the
  // dropdown cannot show — a filter you can't see is indistinguishable from a broken query.
  useEffect(() => {
    if (meta.data === undefined) return;
    if (arm !== null && !meta.data.arms.includes(arm)) setArm(null);
    if (date !== null && !meta.data.dates.includes(date)) setDate(null);
  }, [meta.data, arm, date]);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Flies</h1>
        <PaperLiveBadge mode={mode} />
        <div className="mode-toggle" style={{ marginLeft: 0 }}>
          {(["today", "history", "performance"] as FliesTab[]).map((t) => (
            <button key={t} type="button" className={tab === t ? "mode-btn active" : "mode-btn"} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </div>
        {tab === "today" && (
        <>
        <select
          className="text-input"
          value={arm ?? ""}
          onChange={(e) => setArm(e.target.value === "" ? null : e.target.value)}
          aria-label="arm filter"
        >
          <option value="">all arms</option>
          {meta.data?.arms.map((armName) => (
            <option key={armName} value={armName}>
              {armName}
            </option>
          ))}
        </select>
        <select
          className="text-input"
          value={era ?? ""}
          onChange={(e) => setEra(e.target.value === "" ? null : e.target.value)}
          aria-label="era scope"
          title="The XSP books (2026-07-29..07-31) are a different trade — 1-wide structures at 41% fee drag against the SPX book's 11%. Pooling them distorts every per-arm breakdown."
        >
          <option value="">SPX era (current)</option>
          <option value="ALL">all eras</option>
        </select>
        <select
          className="text-input"
          value={date ?? ""}
          onChange={(e) => setDate(e.target.value === "" ? null : e.target.value)}
          aria-label="date filter"
        >
          <option value="">latest day</option>
          {meta.data?.dates.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        </>
        )}
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      {tab === "history" && (
        <HistoryTab
          mode={mode}
          onReplayDay={(d) => {
            setDate(d);
            setTab("today");
          }}
        />
      )}
      {tab === "performance" && <PerformanceTab mode={mode} />}

      {tab === "today" && (
      <div className="cards cards-wide">
        <section className="card">
          <h2>{a?.today.tradeDate !== null && a !== undefined ? `latest session — ${a.today.tradeDate}` : "latest session"}</h2>
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">net P&L</span>
              <span className={`stat-value ${(a?.today.netPnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                {a !== undefined ? fmtMoney(a.today.netPnl) : "—"}
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">positions</span>
              <span className="stat-value">{a?.today.positions ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">open</span>
              <span className="stat-value">{a?.today.open ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">risk-free</span>
              <span className="stat-value pnl-pos">{a?.today.riskFree ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">completion</span>
              <span className="stat-value">{a?.today.completionPct != null ? `${a.today.completionPct.toFixed(0)}%` : "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">fees</span>
              <span className="stat-value pnl-neg">{a !== undefined ? fmtMoney(a.today.fees) : "—"}</span>
            </div>
            <div className="stat-tile" title="every open position own worst case, net of fees and the worst-case assignment fee — zero means nothing open can still lose">
              <span className="stat-label">max possible loss</span>
              <span className={`stat-value ${(a?.today.maxPossibleLoss ?? 0) < 0 ? "pnl-neg" : "muted"}`}>
                {a !== undefined ? fmtMoney(a.today.maxPossibleLoss) : "—"}
              </span>
            </div>
          </div>
        </section>

        <ArmRail module="flies" mode={mode} date={filter.date} />

        <AttemptTimeline module="flies" mode={mode} date={filter.date} />

        <OccupancyMap module="flies" mode={mode} date={filter.date} />

        <ForestCard mode={mode} filter={filter} />

        <TimelineCard mode={mode} filter={filter} arm={arm} />

        <JournalCard mode={mode} filter={filter} />

        <DivergenceCard mode={mode} filter={filter} />

        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(20rem, 1fr))" }}>
          <DataCard
            title="By arm"
            headers={["arm", "trades", "net", "win %", "avg", "PF"]}
          numFrom={1}
            loading={analytics.isLoading}
            rowCount={a?.byArm.length ?? 0}
          >
            {a?.byArm.map((r) => (
              <tr key={r.arm}>
                <td>{r.arm}</td>
                <td>{r.trades}</td>
                <td><PnlCell v={r.net} /></td>
                <td>{r.winPct != null ? `${r.winPct.toFixed(0)}%` : "—"}</td>
                <td>{r.avg != null ? fmtMoney(r.avg) : "—"}</td>
                <td>{r.profitFactor != null ? r.profitFactor.toFixed(2) : "—"}</td>
              </tr>
            ))}
          </DataCard>

          <DataCard
            title="Fee drag by arm"
            headers={["arm", "gross", "fees", "net", "drag %"]}
          numFrom={1}
            loading={analytics.isLoading}
            rowCount={a?.feeDrag.length ?? 0}
          >
            {a?.feeDrag.map((r) => (
              <tr key={r.arm}>
                <td>{r.arm}</td>
                <td>{fmtMoney(r.gross)}</td>
                <td className="pnl-neg">{fmtMoney(r.fees)}</td>
                <td><PnlCell v={r.net} /></td>
                <td className={r.dragPct != null && r.dragPct > 30 ? "pnl-neg" : "muted"}>
                  {r.dragPct != null ? `${r.dragPct.toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </DataCard>
        </div>

        <DataCard
          title={`Books — ${(data?.books.total ?? 0).toLocaleString()} matching`}
          headers={["date", "arm", "sym", "credit", "debits", "fees", "net cash", "floor", "band", "status", "P&L"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          busy={isPlaceholderData}
          rowCount={data?.books.rows.length ?? 0}
          footer={
            (data?.books.total ?? 0) > 0 && (
              <Pager
                offset={data?.books.offset ?? booksPage.page.offset}
                limit={data?.books.limit ?? booksPage.page.limit}
                total={data?.books.total ?? 0}
                onOffset={booksPage.setOffset}
                onLimit={booksPage.setLimit}
              />
            )
          }
        >
          {data?.books.rows.map((b) => (
            <tr key={b.bookId}>
              <td>{b.tradeDate}</td>
              <td className="muted">{b.arm ?? "—"}</td>
              <td>{b.symbol}</td>
              <td>{fmtMoney(b.creditCollected)}</td>
              <td>{fmtMoney(b.debitsPaid)}</td>
              <td>{fmtMoney(b.fees)}</td>
              <td>{fmtMoney(b.netCash)}</td>
              <td>{b.floorHolds === null ? "—" : b.floorHolds ? "holds" : "no"}</td>
              <td className="muted">
                {b.bandLow !== null && b.bandHigh !== null
                  ? `${fmtNum(b.bandLow, 0)}–${fmtNum(b.bandHigh, 0)}`
                  : "—"}
              </td>
              <td>{b.status}</td>
              <td><PnlCell v={b.pnl} /></td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title={`Positions — ${(data?.positions.total ?? 0).toLocaleString()} matching`}
          headers={["symbol", "arm", "mode", "kind", "centre", "net", "floor", "", "status"]}
          numFrom={1}
          loading={isLoading}
          isError={isError}
          busy={isPlaceholderData}
          rowCount={data?.positions.rows.length ?? 0}
          skeletonRows={10}
          empty={arm !== null || date !== null ? "no matching positions" : "no positions today"}
          footer={
            (data?.positions.total ?? 0) > 0 && (
              <Pager
                offset={data?.positions.offset ?? positionsPage.page.offset}
                limit={data?.positions.limit ?? positionsPage.page.limit}
                total={data?.positions.total ?? 0}
                onOffset={positionsPage.setOffset}
                onLimit={positionsPage.setLimit}
              />
            )
          }
        >
          {data?.positions.rows.map((p) => (
            <tr key={p.positionId}>
              <td>{p.symbol}</td>
              <td className="muted">{p.arm ?? "—"}</td>
              <td className="muted">{p.entryMode ?? "—"}</td>
              <td>{p.kind === "fly" ? "fly" : p.kind === "iron_fly" ? "iron fly" : p.kind === "bwb" ? `bwb ${p.side}` : `short ${p.side}`}</td>
              <td>{fmtNum(p.center, 0)}</td>
              <td>{fmtNum(p.net, 2)}</td>
              <td>{p.floorDollars !== null ? <PnlCell v={p.floorDollars} /> : "—"}</td>
              <td>
                {p.riskFree ? (
                  <span className="chain-badge chain-badge-long">risk-free</span>
                ) : p.kind === "fly" || p.kind === "iron_fly" ? (
                  <span className="chain-badge chain-badge-short">floor negative</span>
                ) : (
                  <span className="chain-badge">at risk</span>
                )}
              </td>
              <td>{p.status}</td>
            </tr>
          ))}
        </DataCard>
      </div>
      )}
    </div>
  );
}
