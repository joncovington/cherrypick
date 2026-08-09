import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useFlies, useFliesMeta, fliesQuery, type FliesFilter } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import type { TradingMode } from "@console/shared";
import { ForestCard } from "./ForestCard";

interface FliesAnalytics {
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
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

export function FliesPage() {
  const [mode, setMode] = useMode();
  const [arm, setArm] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const filter: FliesFilter = { arm, date };
  const meta = useFliesMeta(mode);
  const { data, isLoading, isError } = useFlies(mode, filter);
  const analytics = useFliesAnalytics(mode, filter);
  const a = analytics.data;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Flies</h1>
        <PaperLiveBadge mode={mode} />
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
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

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
          </div>
        </section>

        <ForestCard mode={mode} filter={filter} />

        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(20rem, 1fr))" }}>
          <DataCard
            title="By arm"
            headers={["arm", "trades", "net", "win %", "avg", "PF"]}
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
          title="Books"
          headers={["date", "arm", "sym", "credit", "debits", "fees", "net cash", "floor", "band", "status", "P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.books.length ?? 0}
        >
          {data?.books.map((b) => (
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
          title="Positions"
          headers={["date", "entry", "sym", "kind", "side", "center", "wing", "qty", "net", "status", "P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.positions.length ?? 0}
          skeletonRows={10}
        >
          {data?.positions.map((p) => (
            <tr key={p.positionId}>
              <td>{p.tradeDate}</td>
              <td className="muted">{p.entryTime?.slice(11, 16) ?? "—"}</td>
              <td>{p.symbol}</td>
              <td>{p.kind ?? "—"}</td>
              <td>{p.side ?? "—"}</td>
              <td>{fmtNum(p.center, 0)}</td>
              <td>{fmtNum(p.wingWidth, 0)}</td>
              <td>{fmtNum(p.quantity, 0)}</td>
              <td>{fmtMoney(p.net)}</td>
              <td>{p.status}</td>
              <td><PnlCell v={p.pnl} /></td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
