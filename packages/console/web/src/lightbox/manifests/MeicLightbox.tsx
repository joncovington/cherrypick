import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useMeic } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { ScopeSelect, EraSelect, LoopPill } from "../../components/ScopeBar";
import { ModuleIntegrityStrip } from "../../components/ModuleIntegrityStrip";
import { MeicDeepCards } from "../../pages/Meic/MeicDeepCards";
import { MeicDivergenceCard } from "../../pages/Meic/MeicDivergenceCard";
import { ExperimentGuideView } from "../../components/ExperimentGuide";
import { ArmRail, AttemptTimeline } from "../../components/Attempts";
import { OccupancyMap } from "../../components/OccupancyMap";
import { MeicForestCard } from "../../pages/Meic/MeicForestCard";
import { MeicPerformanceTab } from "../../pages/Meic/MeicPerformanceTab";
import { MeicSession } from "../../pages/Meic/MeicSession";
import { MeicHistoryTable, MeicPositionsTable, type MeicScope } from "../../pages/Meic/MeicTables";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { AdvisorSlide } from "../../components/advisor/AdvisorSlide";
import { RegimeCutsTab } from "../../components/RegimeCutsTab";
import { ModuleFrame } from "../ModuleFrame";
import { MEIC_SLIDES, type MeicSlideId } from "../navGroups";
import type { SlideDef } from "../types";
import { fmtIvr } from "../../lib/format";

interface MeicAnalytics {
  periods: Array<{ label: string; net: number; trades: number; wins: number; losses: number }>;
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

const MEIC_LABEL = Object.fromEntries(MEIC_SLIDES.map((s) => [s.id, s.label])) as Record<MeicSlideId, string>;

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

/**
 * MEIC on the module frame (2026-09-25): a left rail of pages rather than a carousel of slides, and
 * nothing on the surface opens an overlay — every card links to a page the rail also reaches. The
 * tab names and their groups are declared in `navGroups.ts` (`MEIC_SLIDES`), where the reasons for
 * the three renames are written down.
 */
export function MeicLightbox({ slide }: { slide: string }) {
  const [mode, setMode] = useMode();
  const [symbol, setSymbol] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [era, setEra] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);

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

  // The frame's own read: the session list for the day select and the integrity strip. One row of
  // the positions view -- the same query key the session tab's position count uses.
  const { data, isLoading, isError, dataUpdatedAt } = useMeic(mode, {
    day,
    symbol,
    profile,
    era: resolvedEra,
    view: "positions",
    outcome: "all",
    reason: null,
    search: "",
    limit: 1,
    offset: 0,
  });
  const analytics = useMeicAnalytics(mode, symbol, profile, era);
  const a = analytics.data;
  const totalExits = a?.exitReasons.reduce((s, r) => s + r.count, 0) ?? 0;
  const l = loop.data;
  const eras = scope.data?.eras ?? [];
  const activeEraCount = eras.find((e) => e.era === activeEra)?.trades ?? 0;
  const otherEraCount = eras.reduce((s, e) => s + e.trades, 0) - activeEraCount;
  const emptyEra = era !== "ALL" && eras.length > 0 && activeEraCount === 0 && otherEraCount > 0;

  const scopeArgs: MeicScope = { day, symbol, profile, era: resolvedEra };
  const reasons = (a?.exitReasons ?? []).map((r) => r.reason);

  const slides: Array<SlideDef & { id: MeicSlideId }> = [
    { id: "session", label: MEIC_LABEL.session, render: () => <MeicSession mode={mode} scope={scopeArgs} /> },
    { id: "forest", label: MEIC_LABEL.forest, render: () => <MeicForestCard mode={mode} date={day} /> },
    {
      // Attempts and occupancy merged (2026-09): both are bounded snapshots regardless of session
      // activity -- AttemptTimeline's SVG height depends only on arm count, and OccupancyMap shows
      // only CURRENTLY open legs -- so combining them cannot outgrow the page on a busy session.
      id: "attempts",
      label: MEIC_LABEL.attempts,
      render: () => (
        <div className="cards cards-wide">
          <ArmRail module="meic" mode={mode} date={day} />
          <AttemptTimeline module="meic" mode={mode} date={day} />
          <OccupancyMap module="meic" mode={mode} date={day} />
        </div>
      ),
    },
    {
      id: "exits",
      label: MEIC_LABEL.exits,
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
                <span className="stat-label">premium collected</span>
                <span className="stat-value">{a !== undefined ? fmtMoney(a.feeDrag.grossCredit) : "—"}</span>
              </div>
              <div className="stat-tile">
                <span className="stat-label">fees and settlement</span>
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
    { id: "regime", label: MEIC_LABEL.regime, render: () => <RegimeCutsTab module="meic" /> },
    {
      id: "calibration",
      label: MEIC_LABEL.calibration,
      render: () => <MeicPerformanceTab mode={mode} symbol={symbol} profile={profile} era={resolvedEra} />,
    },
    { id: "performance", label: MEIC_LABEL.performance, render: () => <PerformanceSlide module="meic" mode={mode} /> },
    { id: "advisor", label: MEIC_LABEL.advisor, render: () => <AdvisorSlide module="meic" /> },
    { id: "positions", label: MEIC_LABEL.positions, render: () => <MeicPositionsTable mode={mode} scope={scopeArgs} /> },
    {
      id: "history",
      label: MEIC_LABEL.history,
      render: () => <MeicHistoryTable mode={mode} scope={scopeArgs} reasons={reasons} />,
    },
    {
      id: "sessions",
      label: MEIC_LABEL.sessions,
      render: () => (
        <div className="cards cards-wide">
          <Card title="Calendar periods (net of fees)" updatedAt={analytics.dataUpdatedAt}>
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
      id: "guide",
      label: MEIC_LABEL.guide,
      render: () => (
        <ExperimentGuideView
          url="/api/meic/profiles"
          mode={mode}
          intro="Every ENABLED arm is evaluated on every tick — they are parallel arms of one experiment, not a ladder you pick a rung from, and active_profile no longer selects between them. Each description below is the module's own, read from config.risk.json, and 'what makes it different' is derived from the arm's settings: the values it does not share with the module's base config or with most of its siblings."
        />
      ),
    },
  ];

  return (
    <ModuleFrame
      module="meic"
      slide={slide}
      slides={slides}
      badge={<PaperLiveBadge mode={mode} />}
      session={data?.session ?? null}
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
              title="Governs every page with a session in it -- session, forest, attempts, positions and history -- so no two can describe different days side by side."
            >
              <option value="">
                latest session{day === null && data?.session != null ? ` (${data.session})` : ""}
              </option>
              {data?.summaries.map((sm) => (
                <option key={sm.summaryDate} value={sm.summaryDate}>{sm.summaryDate}</option>
              ))}
            </select>
          )}
          <ScopeSelect label="symbol" value={symbol} options={scope.data?.symbols} onChange={setSymbol} allLabel="all symbols" />
          <ScopeSelect label="arm" value={profile} options={scope.data?.profiles} onChange={setProfile} allLabel="all arms" />
          <EraSelect value={era} eras={scope.data?.eras} currentEra={scope.data?.currentEra} onChange={setEra} />
          {l?.ivRank != null && <span className="chip">IV rank {fmtIvr(l.ivRank)}</span>}
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
