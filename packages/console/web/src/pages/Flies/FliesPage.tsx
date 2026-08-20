import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useFlies, useFliesMeta, fliesQuery, type FliesFilter } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage, LoopPill } from "../../components/ScopeBar";
import type { TradingMode } from "@console/shared";
import { ForestCard } from "./ForestCard";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { OccupancyMap } from "../../components/OccupancyMap";
import { TimelineCard } from "./TimelineCard";
import { HistoryTab } from "./HistoryTab";
import { JournalCard } from "./JournalCard";
import { DivergenceCard } from "./DivergenceCard";
import { PerformanceTab } from "./PerformanceTab";
import { ExperimentGuideView } from "../../components/ExperimentGuide";

interface FliesLoopStatus {
  state: "live" | "idle" | "no-data";
  lastIterationAt: string | null;
  ageSeconds: number | null;
  symbol: string | null;
  arm: string | null;
  underlyingPrice: number | null;
}

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

type FliesTab = "today" | "history" | "performance" | "help";

/**
 * Why a per-arm table is empty. Both tables count settled positions, so an open session shows
 * nothing — which is a different fact from an arm that never traded, and the page has to say which.
 */
function emptyReason(a: FliesAnalytics | undefined): string {
  const open = a?.today.positions ?? 0;
  if (open === 0) return "no positions on this session";
  return `${open} position${open === 1 ? "" : "s"} entered, none settled yet — per-arm results fill in as the book settles`;
}

export function FliesPage() {
  const [mode, setMode] = useMode();
  const [arm, setArm] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  // null = the module's current era (SPX from 2026-08-01), matching what its own analytics
  // count as evidence. "ALL" reaches the XSP books too — a different trade at 1/5 the width and
  // 4x the fee drag — so widening is a stated choice, never the quiet default.
  const [era, setEra] = useState<string | null>(null);
  // Only meaningful with era ALL — the current era is SPX alone, so the select hides itself
  // rather than offering a one-option filter.
  const [symbol, setSymbol] = useState<string | null>(null);
  const [tab, setTab] = useState<FliesTab>("today");
  const meta = useFliesMeta(mode, era);
  // ONE day governs every Today card, the way it already does on the MEIC page.
  //
  // Left null, each card resolved its own: the attempts views default to the latest day with
  // ATTEMPTS while the forest, occupancy and session tiles default to the latest day with
  // POSITIONS. Before anything fills those are different days, so the tab could show yesterday's
  // book beside today's refusals — each correctly labelled, and contradictory side by side.
  const resolvedDate = date ?? meta.data?.dates[0] ?? null;
  const filter: FliesFilter = { arm, date: resolvedDate, symbol, era };
  // History and Performance span sessions by definition, so they keep the RAW date — resolving a
  // "latest day" for them would collapse both to one session and empty every trend on them.
  const multiDayFilter: FliesFilter = { arm, date, symbol, era };
  // Two tables on one payload, each with its own page — turning one leaves the
  // other where it was. Both reset when the filter changes underneath them.
  const booksPage = usePage([mode, arm, date, symbol, era]);
  const positionsPage = usePage([mode, arm, date, symbol, era]);
  const { data, isLoading, isError, isPlaceholderData } = useFlies(mode, filter, booksPage.page, positionsPage.page);
  const analytics = useFliesAnalytics(mode, filter);
  const a = analytics.data;
  // Whether the loop is alive is a property of the module, not of the arm/era being viewed, so
  // this is deliberately unscoped by the filter above.
  const loop = useQuery<FliesLoopStatus>({
    queryKey: ["flies-loop", mode],
    queryFn: async () => (await fetch(`/api/flies/loop?mode=${mode}`)).json() as Promise<FliesLoopStatus>,
    refetchInterval: 30_000,
  });
  const l = loop.data;

  // Narrowing the era can remove the arm or date currently selected (width-2/3/4 are XSP-only).
  // Clear a selection the new scope no longer offers, so the page never filters on a value the
  // dropdown cannot show — a filter you can't see is indistinguishable from a broken query.
  useEffect(() => {
    if (meta.data === undefined) return;
    if (arm !== null && !meta.data.arms.includes(arm)) setArm(null);
    if (date !== null && !meta.data.dates.includes(date)) setDate(null);
    if (symbol !== null && !meta.data.symbols.includes(symbol)) setSymbol(null);
  }, [meta.data, arm, date, symbol]);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Flies</h1>
        <PaperLiveBadge mode={mode} />
        <div className="mode-toggle" style={{ marginLeft: 0 }}>
          {(["today", "history", "performance", "help"] as FliesTab[]).map((t) => (
            <button key={t} type="button" className={tab === t ? "mode-btn active" : "mode-btn"} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </div>
        {/* Arm, symbol and era scope EVERY tab — a per-arm ranking on History or an equity curve on
            Performance is exactly where a silently-pooled era does the most damage. Only the date
            select stays Today-only: the multi-day views drop it, since pinning one session would
            empty them. */}
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
        {(meta.data?.symbols.length ?? 0) > 1 && (
          <select
            className="text-input"
            value={symbol ?? ""}
            onChange={(e) => setSymbol(e.target.value === "" ? null : e.target.value)}
            aria-label="symbol filter"
          >
            <option value="">all symbols</option>
            {meta.data?.symbols.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
        )}
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
        {tab === "today" && (
        <>
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
        <LoopPill
          state={l?.state}
          ageSeconds={l?.ageSeconds}
          detail={
            l?.lastIterationAt != null
              ? `last iteration ${l.lastIterationAt}${l.arm !== null ? ` · ${l.arm}` : ""}`
              : undefined
          }
        />
        {l?.underlyingPrice != null && <span className="chip">{l.underlyingPrice.toFixed(2)}</span>}
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      {tab === "history" && (
        <HistoryTab
          mode={mode}
          filter={multiDayFilter}
          onReplayDay={(d) => {
            setDate(d);
            setTab("today");
          }}
        />
      )}
      {tab === "performance" && <PerformanceTab mode={mode} filter={multiDayFilter} />}
      {tab === "help" && (
        <ExperimentGuideView
          url="/api/flies/arms"
          mode={mode}
          intro="Every arm is an independent portfolio trading the same market with the same money, so the only thing separating them is which entries their rules allow. Each description below is the module's own — read from the deployed config — and 'what makes it different' is derived from the arm's settings: the values it does not share with the defaults or with most of its siblings."
        />
      )}

      {tab === "today" && (
      <div className="cards cards-wide">
        <ArmRail module="flies" mode={mode} date={filter.date} />

        <AttemptTimeline module="flies" mode={mode} date={filter.date} />

        <OccupancyMap module="flies" mode={mode} date={filter.date} />

        {/* The aggregate sits BELOW the per-arm views, and that ordering is the point. Every arm is
            an independent portfolio on unbounded capital, so a net summed across six deliberately
            different strategies cannot move for any reason worth acting on. It is context, not the
            headline. */}
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
            // These count SETTLED positions, so they are legitimately empty for most of a live
            // session. Say which kind of empty it is: "no rows" beside a card reporting 34 open
            // positions reads as an arm that did nothing.
            empty={emptyReason(a)}
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
            empty={emptyReason(a)}
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
