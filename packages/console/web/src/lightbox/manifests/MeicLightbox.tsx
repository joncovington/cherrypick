import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useMeic } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { ScopeSelect, EraSelect, LoopPill, Pager, usePage } from "../../components/ScopeBar";
import { ModuleIntegrityStrip } from "../../components/ModuleIntegrityStrip";
import { MeicDeepCards } from "../../pages/Meic/MeicDeepCards";
import { MeicDivergenceCard } from "../../pages/Meic/MeicDivergenceCard";
import { ExperimentGuideView } from "../../components/ExperimentGuide";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { OccupancyMap } from "../../components/OccupancyMap";
import { MeicForestCard } from "../../pages/Meic/MeicForestCard";
import { MeicPerformanceTab } from "../../pages/Meic/MeicPerformanceTab";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

interface MeicAnalytics {
  periods: Array<{ label: string; net: number; trades: number; wins: number; losses: number }>;
  byProfile: Array<{ profile: string; trades: number; net: number; winPct: number | null; avg: number | null; profitFactor: number | null }>;
  profileFeeDrag: Array<{ profile: string; gross: number; fees: number; net: number; dragPct: number | null }>;
  exitReasons: Array<{ reason: string; count: number }>;
  feeDrag: { grossCredit: number; fees: number; netPnl: number; dragPct: number | null };
}

interface MeicScopeData {
  symbols: string[];
  profiles: string[];
  eras: Array<{ era: string; trades: number }>;
  currentEra: string;
}

interface LoopStatus {
  state: "live" | "idle" | "no-data";
  lastLoopAt: string | null;
  ageSeconds: number | null;
  action: string | null;
  ivRank: number | null;
  underlyingPrice: number | null;
  sessionQuality: string | null;
}

function scopeQuery(mode: TradingMode, symbol: string | null, profile: string | null, era: string | null): string {
  const p = new URLSearchParams({ mode });
  if (symbol !== null) p.set("symbol", symbol);
  if (profile !== null) p.set("profile", profile);
  if (era !== null) p.set("era", era);
  return p.toString();
}

function useMeicAnalytics(mode: TradingMode, symbol: string | null, profile: string | null, era: string | null) {
  return useQuery<MeicAnalytics>({
    queryKey: ["meic-analytics", mode, symbol, profile, era],
    queryFn: async () => {
      const res = await fetch(`/api/meic/analytics?${scopeQuery(mode, symbol, profile, era)}`);
      if (!res.ok) throw new Error(`meic analytics: HTTP ${res.status}`);
      return (await res.json()) as MeicAnalytics;
    },
    refetchInterval: 30_000,
  });
}

const OUTCOMES = ["all", "wins", "losses", "open"] as const;

function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  const cls = s.includes("stop") ? "chain-badge-short" : s.includes("expire") ? "chain-badge-long" : "";
  return <span className={`chain-badge ${cls}`}>{status}</span>;
}

export function MeicLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  const [symbol, setSymbol] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [era, setEra] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [reason, setReason] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  const scope = useQuery<MeicScopeData>({
    queryKey: ["meic-scope", mode, era],
    queryFn: async () =>
      (await fetch(`/api/meic/scope?mode=${mode}${era !== null ? `&era=${era}` : ""}`)).json() as Promise<MeicScopeData>,
    staleTime: 300_000,
  });
  useEffect(() => {
    const data = scope.data;
    if (data === undefined) return;
    if (symbol !== null && !data.symbols.includes(symbol)) setSymbol(null);
    if (profile !== null && !data.profiles.includes(profile)) setProfile(null);
  }, [scope.data, symbol, profile]);

  const loop = useQuery<LoopStatus>({
    queryKey: ["meic-loop", mode, symbol],
    queryFn: async () => (await fetch(`/api/meic/loop?${scopeQuery(mode, symbol, null, null)}`)).json() as Promise<LoopStatus>,
    refetchInterval: 30_000,
  });

  const erasPresent = scope.data?.eras ?? [];
  const defaultEra =
    scope.data === undefined
      ? undefined
      : erasPresent.some((e) => e.era === scope.data?.currentEra)
        ? scope.data.currentEra
        : (erasPresent[erasPresent.length - 1]?.era ?? scope.data.currentEra);
  const activeEra = era ?? defaultEra;
  const resolvedEra = era ?? defaultEra ?? null;

  const { page, setOffset, setLimit } = usePage([mode, day, symbol, profile, resolvedEra, outcome, reason, debouncedSearch]);

  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useMeic(mode, {
    day,
    symbol,
    profile,
    era: resolvedEra,
    outcome,
    reason,
    search: debouncedSearch,
    ...page,
  });
  const analytics = useMeicAnalytics(mode, symbol, profile, era);
  const a = analytics.data;
  const totalExits = a?.exitReasons.reduce((s, r) => s + r.count, 0) ?? 0;
  const trades = data?.trades.rows ?? [];
  const total = data?.trades.total ?? 0;
  const l = loop.data;
  const eras = scope.data?.eras ?? [];
  const activeEraCount = eras.find((e) => e.era === activeEra)?.trades ?? 0;
  const otherEraCount = eras.reduce((s, e) => s + e.trades, 0) - activeEraCount;
  const emptyEra = era !== "ALL" && eras.length > 0 && activeEraCount === 0 && otherEraCount > 0;

  const slides: SlideDef[] = [
    {
      id: "now",
      label: "now",
      render: () => (
        <div className="cards cards-wide">
          <Card title="Performance (net of fees)" updatedAt={analytics.dataUpdatedAt}>
            <div className="stats-grid">
              {(a?.periods ?? []).map((p) => (
                <div key={p.label} className="stat-tile">
                  <span className="stat-label">{p.label}</span>
                  <span className={`stat-value ${p.net >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(p.net)}</span>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {p.trades} trades · {p.wins}W/{p.losses}L
                    {p.wins + p.losses > 0 ? ` · ${((p.wins / (p.wins + p.losses)) * 100).toFixed(0)}%` : ""}
                  </span>
                </div>
              ))}
            </div>
          </Card>
          <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(18rem, 1fr))" }}>
            <DataCard
              title="By profile — today"
              headers={["profile", "trades", "net", "win %", "avg", "PF"]}
              numFrom={1}
              loading={analytics.isLoading}
              rowCount={a?.byProfile.length ?? 0}
              updatedAt={analytics.dataUpdatedAt}
              empty="nothing settled yet today — 0DTE positions resolve at the close"
            >
              {a?.byProfile.map((r) => (
                <tr key={r.profile}>
                  <td>{r.profile}</td>
                  <td>{r.trades}</td>
                  <td><PnlCell v={r.net} /></td>
                  <td>{r.winPct != null ? `${r.winPct.toFixed(0)}%` : "—"}</td>
                  <td>{r.avg != null ? fmtMoney(r.avg) : "—"}</td>
                  <td>{r.profitFactor != null ? r.profitFactor.toFixed(2) : "—"}</td>
                </tr>
              ))}
            </DataCard>
            <DataCard
              title="Fee drag by profile — today (drag is fees against premium collected)"
              headers={["profile", "credit", "fees", "net", "drag %"]}
              numFrom={1}
              loading={analytics.isLoading}
              rowCount={a?.profileFeeDrag.length ?? 0}
              updatedAt={analytics.dataUpdatedAt}
              empty="nothing settled yet today — 0DTE positions resolve at the close"
            >
              {a?.profileFeeDrag.map((r) => (
                <tr key={r.profile}>
                  <td>{r.profile}</td>
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
    { id: "forest", label: "forest", render: () => <MeicForestCard mode={mode} date={day} /> },
    {
      id: "attempts",
      label: "attempts",
      render: () => (
        <div className="cards cards-wide">
          <ArmRail module="meic" mode={mode} date={day} />
          <AttemptTimeline module="meic" mode={mode} date={day} />
        </div>
      ),
    },
    { id: "occupancy", label: "occupancy", render: () => <OccupancyMap module="meic" mode={mode} date={day} /> },
    {
      id: "exits",
      label: "exits",
      render: () => (
        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(18rem, 1fr))" }}>
          <DataCard
            title="Exit reasons"
            headers={["reason", "count", "%"]}
            numFrom={1}
            tableClass="data-table-labelled"
            loading={analytics.isLoading}
            rowCount={a?.exitReasons.length ?? 0}
            updatedAt={analytics.dataUpdatedAt}
          >
            {a?.exitReasons.map((r) => (
              <tr key={r.reason}>
                <td>{r.reason}</td>
                <td>{r.count}</td>
                <td className="muted">{totalExits > 0 ? `${((r.count / totalExits) * 100).toFixed(1)}%` : "—"}</td>
              </tr>
            ))}
          </DataCard>
          <MeicDivergenceCard mode={mode} date={null} />
          <Card title="Fee drag (this era)" updatedAt={analytics.dataUpdatedAt}>
            <div className="stats-grid">
              <div className="stat-tile">
                <span className="stat-label">gross credit</span>
                <span className="stat-value">{a !== undefined ? fmtMoney(a.feeDrag.grossCredit) : "—"}</span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">total fees</span>
                <span className="stat-value pnl-neg">{a !== undefined ? fmtMoney(a.feeDrag.fees) : "—"}</span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">net P&L</span>
                <span className={`stat-value ${(a?.feeDrag.netPnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                  {a !== undefined ? fmtMoney(a.feeDrag.netPnl) : "—"}
                </span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">fee drag</span>
                <span className="stat-value">{fmtPct(a?.feeDrag.dragPct ?? null, 1)}</span>
              </div>
            </div>
          </Card>
        </div>
      ),
    },
    {
      id: "calibration",
      label: "calibration",
      render: () => <MeicPerformanceTab mode={mode} symbol={symbol} profile={profile} era={resolvedEra} />,
    },
    {
      id: "performance",
      label: "performance",
      render: () => <PerformanceSlide module="meic" />,
    },
    {
      id: "history",
      label: "history",
      render: () => (
        <div className="cards cards-wide">
          <MeicDeepCards mode={mode} symbol={symbol} profile={profile} era={resolvedEra} />
          <DataCard
            title="Daily summaries"
            headers={["date", "sym", "entries", "filled", "stopped", "win %", "net P&L"]}
            numFrom={2}
            loading={isLoading}
            isError={isError}
            rowCount={data?.summaries.length ?? 0}
            updatedAt={dataUpdatedAt}
          >
            {data?.summaries.map((s) => (
              <tr key={`${s.summaryDate}-${s.symbol}`}>
                <td>{s.summaryDate}</td>
                <td>{s.symbol ?? "—"}</td>
                <td>{fmtNum(s.totalEntries, 0)}</td>
                <td>{fmtNum(s.entriesFilled, 0)}</td>
                <td>{fmtNum(s.entriesStopped, 0)}</td>
                <td>{fmtNum(s.winRatePct, 1)}</td>
                <td><PnlCell v={s.netPnl} /></td>
              </tr>
            ))}
          </DataCard>
        </div>
      ),
    },
    {
      id: "trades",
      label: "trades",
      render: () => (
        <DataCard
          title={`Trades — ${total.toLocaleString()} matching${day !== null ? ` on ${day}` : " on the latest session"}`}
          headers={["date", "entry", "sym", "put", "call", "wing", "credit", "qty", "IVR", "status", "P&L", "exit reason"]}
          numFrom={3}
          loading={isLoading}
          isError={isError}
          rowCount={trades.length}
          skeletonRows={10}
          busy={isPlaceholderData}
          empty="no trades match these filters"
          updatedAt={dataUpdatedAt}
          footer={
            total > 0 && (
              <Pager
                offset={data?.trades.offset ?? page.offset}
                limit={data?.trades.limit ?? page.limit}
                total={total}
                onOffset={setOffset}
                onLimit={setLimit}
              />
            )
          }
          controls={
            <>
              <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="outcome filter">
                {OUTCOMES.map((o) => (
                  <button key={o} type="button" className={outcome === o ? "mode-btn active" : "mode-btn"} onClick={() => setOutcome(o)}>
                    {o}
                  </button>
                ))}
              </div>
              <select
                className="text-input"
                value={reason ?? ""}
                onChange={(e) => setReason(e.target.value === "" ? null : e.target.value)}
                aria-label="exit reason"
              >
                <option value="">all reasons</option>
                {a?.exitReasons.map((r) => (
                  <option key={r.reason} value={r.reason}>{r.reason}</option>
                ))}
              </select>
              <input
                className="text-input"
                placeholder="search…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{ textTransform: "none", width: "8rem" }}
              />
            </>
          }
        >
          {trades.map((t) => (
            <tr key={`${t.mode}-${t.id}`}>
              <td>{t.tradeDate}</td>
              <td className="muted">{t.entryTime?.slice(11, 16) ?? "—"}</td>
              <td>{t.symbol}</td>
              <td>{fmtNum(t.putStrike, 0)}</td>
              <td>{fmtNum(t.callStrike, 0)}</td>
              <td>{fmtNum(t.wingWidth, 0)}</td>
              <td>{fmtMoney(t.netCredit)}</td>
              <td>{fmtNum(t.quantity, 0)}</td>
              <td className="muted">{t.ivRankAtEntry !== null ? `${(t.ivRankAtEntry * 100).toFixed(0)}%` : "—"}</td>
              <td><StatusBadge status={t.status} /></td>
              <td><PnlCell v={t.pnl} /></td>
              <td className="muted" style={{ textAlign: "left" }}>{t.exitReason ?? "—"}</td>
            </tr>
          ))}
        </DataCard>
      ),
    },
    {
      id: "guide",
      label: "guide",
      render: () => (
        <ExperimentGuideView
          url="/api/meic/profiles"
          mode={mode}
          intro="Every ENABLED risk profile is evaluated on every tick — they are parallel arms of one experiment, not a ladder you pick a rung from, and active_profile no longer selects between them. Each description below is the module's own, read from config.risk.json, and 'what makes it different' is derived from the profile's settings: the values it does not share with the module's base config or with most of its siblings."
        />
      ),
    },
  ];

  return (
    <LightboxFrame
      module="meic"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      session={null}
      loopPill={
        <LoopPill
          state={l?.state}
          ageSeconds={l?.ageSeconds}
          detail={l?.lastLoopAt !== null && l !== undefined ? `last loop ${l.lastLoopAt} · ${l.action ?? ""}` : undefined}
        />
      }
      headerControls={
        <>
          {(data?.summaries.length ?? 0) > 0 && (
            <select
              className="text-input"
              value={day ?? ""}
              onChange={(e) => setDay(e.target.value === "" ? null : e.target.value)}
              aria-label="session"
              title="Governs the arm rail, attempt timeline, occupancy map and forest together, so they can never describe different days side by side."
            >
              <option value="">latest session</option>
              {data?.summaries.map((sm) => (
                <option key={sm.summaryDate} value={sm.summaryDate}>{sm.summaryDate}</option>
              ))}
            </select>
          )}
          <ScopeSelect label="symbol" value={symbol} options={scope.data?.symbols} onChange={setSymbol} allLabel="all symbols" />
          <ScopeSelect label="profile" value={profile} options={scope.data?.profiles} onChange={setProfile} allLabel="all profiles" />
          <EraSelect value={era} eras={scope.data?.eras} currentEra={scope.data?.currentEra} onChange={setEra} />
          {l?.ivRank != null && <span className="chip">IV rank {(l.ivRank * 100).toFixed(0)}%</span>}
          {l?.underlyingPrice != null && <span className="chip">{l.underlyingPrice.toFixed(2)}</span>}
          <ModeToggle mode={mode} onChange={setMode} />
        </>
      }
      persistentTop={
        emptyEra ? (
          <div className="lb-persistent">
            <p className="stale-note">
              No trades in era <strong>{activeEra}</strong> for this {mode} store — {otherEraCount} sit in
              earlier eras, which the module treats as shakedown data rather than evidence.{" "}
              <button type="button" className="link-button" onClick={() => setEra("ALL")}>
                show every era
              </button>
            </p>
          </div>
        ) : undefined
      }
      integrity={<ModuleIntegrityStrip integrity={data?.integrity} collapseKey="meic-integrity" updatedAt={dataUpdatedAt} />}
      integrityAttention={(data?.integrity?.measurementBreaks.length ?? 0) > 0 || (data?.integrity?.schemaDrift.length ?? 0) > 0}
    />
  );
}
