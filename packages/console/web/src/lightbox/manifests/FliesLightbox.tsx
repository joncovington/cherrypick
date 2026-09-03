import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useFlies, useFliesMeta, fliesQuery, type FliesFilter } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { EraSelect, LoopPill, Pager, usePage } from "../../components/ScopeBar";
import { ModuleIntegrityStrip } from "../../components/ModuleIntegrityStrip";
import type { TradingMode } from "@console/shared";
import { ForestCard } from "../../pages/Flies/ForestCard";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { OccupancyMap } from "../../components/OccupancyMap";
import { TimelineCard } from "../../pages/Flies/TimelineCard";
import { HistoryTab } from "../../pages/Flies/HistoryTab";
import { JournalCard } from "../../pages/Flies/JournalCard";
import { DivergenceCard } from "../../pages/Flies/DivergenceCard";
import { PerformanceTab } from "../../pages/Flies/PerformanceTab";
import { ExperimentGuideView } from "../../components/ExperimentGuide";
import { structureLabel } from "../../pages/Flies/structure";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

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

function emptyReason(a: FliesAnalytics | undefined): string {
  const open = a?.today.positions ?? 0;
  if (open === 0) return "no positions on this session";
  return `${open} position${open === 1 ? "" : "s"} entered, none settled yet — per-arm results fill in as the book settles`;
}

export function FliesLightbox({ slide }: { slide: string }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useMode();
  const [arm, setArm] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [era, setEra] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const meta = useFliesMeta(mode, era);
  const resolvedDate = date ?? meta.data?.dates[0] ?? null;
  const filter: FliesFilter = { arm, date: resolvedDate, symbol, era };
  const multiDayFilter: FliesFilter = { arm, date, symbol, era };
  const booksPage = usePage([mode, arm, date, symbol, era]);
  const positionsPage = usePage([mode, arm, date, symbol, era]);
  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useFlies(mode, filter, booksPage.page, positionsPage.page);
  const analytics = useFliesAnalytics(mode, filter);
  const a = analytics.data;
  const loop = useQuery<FliesLoopStatus>({
    queryKey: ["flies-loop", mode],
    queryFn: async () => (await fetch(`/api/flies/loop?mode=${mode}`)).json() as Promise<FliesLoopStatus>,
    refetchInterval: 30_000,
  });
  const l = loop.data;

  useEffect(() => {
    if (meta.data === undefined) return;
    if (arm !== null && !meta.data.arms.includes(arm)) setArm(null);
    if (date !== null && !meta.data.dates.includes(date)) setDate(null);
    if (symbol !== null && !meta.data.symbols.includes(symbol)) setSymbol(null);
  }, [meta.data, arm, date, symbol]);

  const replayDay = (d: string) => {
    setDate(d);
    const qs = params.toString();
    navigate(`/flies/now${qs ? `?${qs}` : ""}`);
  };

  const slides: SlideDef[] = [
    {
      id: "now",
      label: "now",
      render: () => (
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
              <div className="stat-tile" title="every open position's own worst case, net of fees and the worst-case assignment fee — zero means nothing open can still lose">
                <span className="stat-label">max possible loss</span>
                <span className={`stat-value ${(a?.today.maxPossibleLoss ?? 0) < 0 ? "pnl-neg" : "muted"}`}>
                  {a !== undefined ? fmtMoney(a.today.maxPossibleLoss) : "—"}
                </span>
              </div>
            </div>
          </section>
          <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(20rem, 1fr))" }}>
            <DataCard
              title="By arm"
              headers={["arm", "trades", "net", "win %", "avg", "PF"]}
              numFrom={1}
              loading={analytics.isLoading}
              rowCount={a?.byArm.length ?? 0}
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
        </div>
      ),
    },
    { id: "forest", label: "forest", render: () => <ForestCard mode={mode} filter={filter} /> },
    {
      id: "attempts",
      label: "attempts",
      render: () => (
        <div className="cards cards-wide">
          <ArmRail module="flies" mode={mode} date={filter.date} />
          <AttemptTimeline module="flies" mode={mode} date={filter.date} />
        </div>
      ),
    },
    { id: "occupancy", label: "occupancy", render: () => <OccupancyMap module="flies" mode={mode} date={filter.date} /> },
    { id: "timeline", label: "timeline", render: () => <TimelineCard mode={mode} filter={filter} arm={arm} /> },
    { id: "journal", label: "journal", render: () => <JournalCard mode={mode} filter={filter} /> },
    { id: "exits", label: "exits", render: () => <DivergenceCard mode={mode} filter={filter} /> },
    {
      id: "books",
      label: "books",
      render: () => (
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
              <td className="muted">{b.bandLow !== null && b.bandHigh !== null ? `${fmtNum(b.bandLow, 0)}–${fmtNum(b.bandHigh, 0)}` : "—"}</td>
              <td>{b.status}</td>
              <td><PnlCell v={b.pnl} /></td>
            </tr>
          ))}
        </DataCard>
      ),
    },
    {
      id: "trades",
      label: "positions",
      render: () => (
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
              <td>{structureLabel(p.kind, p.side)}</td>
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
      ),
    },
    { id: "performance", label: "performance", render: () => <PerformanceTab mode={mode} filter={multiDayFilter} /> },
    { id: "history", label: "history", render: () => <HistoryTab mode={mode} filter={multiDayFilter} onReplayDay={replayDay} /> },
    {
      id: "guide",
      label: "guide",
      render: () => (
        <ExperimentGuideView
          url="/api/flies/arms"
          mode={mode}
          intro="Every arm is an independent portfolio trading the same market with the same money, so the only thing separating them is which entries their rules allow. Each description below is the module's own — read from the deployed config — and 'what makes it different' is derived from the arm's settings: the values it does not share with the defaults or with most of its siblings."
        />
      ),
    },
  ];

  return (
    <LightboxFrame
      module="flies"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      session={null}
      loopPill={
        <LoopPill
          state={l?.state}
          ageSeconds={l?.ageSeconds}
          detail={l?.lastIterationAt != null ? `last iteration ${l.lastIterationAt}${l.arm !== null ? ` · ${l.arm}` : ""}` : undefined}
        />
      }
      headerControls={
        <>
          <select className="text-input" value={arm ?? ""} onChange={(e) => setArm(e.target.value === "" ? null : e.target.value)} aria-label="arm filter">
            <option value="">all arms</option>
            {meta.data?.arms.map((armName) => (
              <option key={armName} value={armName}>{armName}</option>
            ))}
          </select>
          {(meta.data?.symbols.length ?? 0) > 1 && (
            <select className="text-input" value={symbol ?? ""} onChange={(e) => setSymbol(e.target.value === "" ? null : e.target.value)} aria-label="symbol filter">
              <option value="">all symbols</option>
              {meta.data?.symbols.map((sym) => (
                <option key={sym} value={sym}>{sym}</option>
              ))}
            </select>
          )}
          <EraSelect
            value={era}
            eras={meta.data?.eras}
            currentEra={meta.data?.currentEra}
            onChange={setEra}
            pooledLabel="all eras — pooled"
            title="The XSP books (2026-07-29..07-31) are a different trade — 1-wide structures at 41% fee drag against the SPX book's 11%. Pooling them distorts every per-arm breakdown."
          />
          <select className="text-input" value={date ?? ""} onChange={(e) => setDate(e.target.value === "" ? null : e.target.value)} aria-label="date filter">
            <option value="">latest day</option>
            {meta.data?.dates.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          {l?.underlyingPrice != null && <span className="chip">{l.underlyingPrice.toFixed(2)}</span>}
          <ModeToggle mode={mode} onChange={setMode} />
        </>
      }
      persistentTop={
        <div className="lb-persistent">
          <ModuleIntegrityStrip integrity={data?.integrity} collapseKey="flies-integrity" updatedAt={dataUpdatedAt} />
        </div>
      }
    />
  );
}
