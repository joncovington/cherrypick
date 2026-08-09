import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useMeic } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { ScopeSelect, EraSelect, TabStrip, LoopPill } from "../../components/ScopeBar";
import { MeicDeepCards } from "./MeicDeepCards";
import { MeicPerformanceTab } from "./MeicPerformanceTab";

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

const TABS = ["today", "history", "performance"] as const;
const OUTCOMES = ["all", "wins", "losses", "open"] as const;

/** Status reads at a glance: stopped is the loss branch, expired is the win branch. */
function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  const cls = s.includes("stop") ? "chain-badge-short" : s.includes("expire") ? "chain-badge-long" : "";
  return <span className={`chain-badge ${cls}`}>{status}</span>;
}

export function MeicPage() {
  const [mode, setMode] = useMode();
  const [tab, setTab] = useState<(typeof TABS)[number]>("today");
  const [symbol, setSymbol] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  /** null = the module's current era, the default every read inherits. */
  const [era, setEra] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [reason, setReason] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const scope = useQuery<MeicScopeData>({
    queryKey: ["meic-scope", mode],
    queryFn: async () => (await fetch(`/api/meic/scope?mode=${mode}`)).json() as Promise<MeicScopeData>,
    staleTime: 300_000,
  });
  const loop = useQuery<LoopStatus>({
    queryKey: ["meic-loop", mode, symbol],
    queryFn: async () => (await fetch(`/api/meic/loop?${scopeQuery(mode, symbol, null, null)}`)).json() as Promise<LoopStatus>,
    refetchInterval: 30_000,
  });
  const { data, isLoading, isError, dataUpdatedAt } = useMeic(mode, symbol, profile, era);
  const analytics = useMeicAnalytics(mode, symbol, profile, era);
  const a = analytics.data;
  const totalExits = a?.exitReasons.reduce((s, r) => s + r.count, 0) ?? 0;

  const trades = (data?.trades ?? []).filter((t) => {
    if (outcome === "wins" && !(t.pnl !== null && t.pnl - (t.fees ?? 0) > 0)) return false;
    if (outcome === "losses" && !(t.pnl !== null && t.pnl - (t.fees ?? 0) <= 0)) return false;
    if (outcome === "open" && t.pnl !== null) return false;
    if (reason !== null && (t.exitReason ?? "open") !== reason) return false;
    if (search !== "") {
      const hay = `${t.tradeDate} ${t.symbol} ${t.status} ${t.exitReason ?? ""}`.toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  const l = loop.data;
  // Filtering to an era with nothing in it is a legitimate answer, not a
  // failure — say so, and offer the widening in one click.
  const eras = scope.data?.eras ?? [];
  const activeEra = era ?? scope.data?.currentEra;
  const activeEraCount = eras.find((e) => e.era === activeEra)?.trades ?? 0;
  const otherEraCount = eras.reduce((s, e) => s + e.trades, 0) - activeEraCount;
  const emptyEra = era !== "ALL" && eras.length > 0 && activeEraCount === 0 && otherEraCount > 0;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>MEIC</h1>
        <PaperLiveBadge mode={mode} />
        <TabStrip tabs={TABS} value={tab} onChange={setTab} />
        <ScopeSelect label="symbol" value={symbol} options={scope.data?.symbols} onChange={setSymbol} allLabel="all symbols" />
        <ScopeSelect label="profile" value={profile} options={scope.data?.profiles} onChange={setProfile} allLabel="all profiles" />
        <EraSelect value={era} eras={scope.data?.eras} currentEra={scope.data?.currentEra} onChange={setEra} />
        <LoopPill
          state={l?.state}
          ageSeconds={l?.ageSeconds}
          detail={l?.lastLoopAt !== null && l !== undefined ? `last loop ${l.lastLoopAt} · ${l.action ?? ""}` : undefined}
        />
        {l?.ivRank != null && <span className="chip">IV rank {(l.ivRank * 100).toFixed(0)}%</span>}
        {l?.underlyingPrice != null && <span className="chip">{l.underlyingPrice.toFixed(2)}</span>}
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      {emptyEra && (
        <p className="stale-note">
          No trades in era <strong>{activeEra}</strong> for this {mode} store — {otherEraCount} sit in earlier
          eras, which the module treats as shakedown data rather than evidence.{" "}
          <button type="button" className="link-button" onClick={() => setEra("ALL")}>
            show every era
          </button>
        </p>
      )}

      {tab === "performance" && <MeicPerformanceTab mode={mode} symbol={symbol} profile={profile} era={era} />}

      {tab === "history" && (
        <div className="cards cards-wide">
          <MeicDeepCards mode={mode} symbol={symbol} profile={profile} era={era} />
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
      )}

      {tab === "today" && (
        <div className="cards cards-wide">
          <Card title="Performance (net = gross P&L; win = P&L − fees > 0)" updatedAt={analytics.dataUpdatedAt}>
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

            <Card title="Fee drag (all-time)" updatedAt={analytics.dataUpdatedAt}>
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

          <DataCard
            title={`Trades (${trades.length} of ${data?.trades.length ?? 0})`}
            headers={["date", "entry", "sym", "put", "call", "wing", "credit", "qty", "IVR", "status", "P&L", "exit reason"]}
            numFrom={3}
            loading={isLoading}
            isError={isError}
            rowCount={trades.length}
            skeletonRows={10}
            updatedAt={dataUpdatedAt}
            controls={
              <>
                <div className="mode-toggle" style={{ marginLeft: 0 }}>
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
        </div>
      )}
    </div>
  );
}
