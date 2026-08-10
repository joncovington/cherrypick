import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useFliesTradeLog } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";

interface Summary {
  trades: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  wins: number;
  losses: number;
  winRatePct: number | null;
  avgPnl: number | null;
  feeDragPct: number | null;
  profitFactor: number | null;
}

interface History {
  byArm: Array<{ arm: string } & Summary>;
  byEntryMode: Array<{ entryMode: string } & Summary>;
  byEntryWindow: Array<{ window: string } & Summary>;
  feeDrag: Array<{ arm: string } & Summary>;
  dailyPnl: Array<{ date: string; trades: number; netPnl: number }>;
}

function useHistory(mode: TradingMode) {
  return useQuery<History>({
    queryKey: ["flies-history", mode],
    queryFn: async () => {
      const res = await fetch(`/api/flies/history?mode=${mode}`);
      if (!res.ok) throw new Error(`history: HTTP ${res.status}`);
      return (await res.json()) as History;
    },
    refetchInterval: 60_000,
  });
}

function SummaryRows<T extends Summary>({ rows, label }: { rows: T[] | undefined; label: keyof T }) {
  return (
    <>
      {rows?.map((r) => (
        <tr key={String(r[label])}>
          <td>{String(r[label])}</td>
          <td>{r.trades}</td>
          <td><PnlCell v={r.netPnl} /></td>
          <td>{r.winRatePct !== null ? `${r.winRatePct.toFixed(0)}%` : "—"}</td>
          <td>{r.avgPnl !== null ? fmtMoney(r.avgPnl) : "—"}</td>
          <td>{r.profitFactor !== null ? r.profitFactor.toFixed(2) : "—"}</td>
        </tr>
      ))}
    </>
  );
}

/** Daily P&L calendar, Monday at the top of each week column; click a day to replay it. */
export function FliesCalendar({
  days,
  onPick,
}: {
  days: Array<{ date: string; trades: number; netPnl: number }>;
  onPick?: (date: string) => void;
}) {
  if (days.length === 0) return <p className="muted">no settled days yet</p>;
  const maxAbs = Math.max(...days.map((d) => Math.abs(d.netPnl)), 1);
  const weeks = new Map<string, Array<{ date: string; trades: number; netPnl: number; weekday: number }>>();
  for (const d of days) {
    const dt = new Date(d.date + "T00:00:00Z");
    const weekday = (dt.getUTCDay() + 6) % 7;
    const monday = new Date(dt);
    monday.setUTCDate(dt.getUTCDate() - weekday);
    const key = monday.toISOString().slice(0, 10);
    let col = weeks.get(key);
    if (col === undefined) {
      col = [];
      weeks.set(key, col);
    }
    col.push({ ...d, weekday });
  }
  return (
    <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 4 }}>
      {[...weeks.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([week, cells]) => (
        <div key={week} style={{ display: "grid", gridTemplateRows: "repeat(5, 18px)", gap: 4 }}>
          {[0, 1, 2, 3, 4].map((wd) => {
            const cell = cells.find((c) => c.weekday === wd);
            if (cell === undefined)
              return <div key={wd} style={{ width: 18, height: 18, background: "var(--row-line)", borderRadius: 3 }} />;
            const alpha = 0.2 + 0.8 * (Math.abs(cell.netPnl) / maxAbs);
            const color = cell.netPnl >= 0 ? `rgba(67, 181, 122, ${alpha})` : `rgba(217, 92, 74, ${alpha})`;
            return (
              <div
                key={wd}
                role={onPick !== undefined ? "button" : undefined}
                title={`${cell.date}: ${fmtMoney(cell.netPnl)} (${cell.trades} positions)${onPick !== undefined ? " — click to replay" : ""}`}
                onClick={() => onPick?.(cell.date)}
                style={{ width: 18, height: 18, background: color, borderRadius: 3, cursor: onPick !== undefined ? "pointer" : "default" }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

const OUTCOMES = ["all", "wins", "losses", "pinned", "risk-free"] as const;

export function HistoryTab({ mode, onReplayDay }: { mode: TradingMode; onReplayDay: (date: string) => void }) {
  const { data, isLoading } = useHistory(mode);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [search, setSearch] = useState("");

  // Search reaches the DB now, so let typing settle first.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  const { page, setOffset, setLimit } = usePage([mode, outcome, debouncedSearch]);
  const logQuery = useFliesTradeLog(mode, outcome, debouncedSearch, page);
  const log = logQuery.data?.rows ?? [];
  const logTotal = logQuery.data?.total ?? 0;

  const headers = ["", "trades", "net", "win %", "avg", "PF"];

  return (
    <div className="cards cards-wide">
      <section className="card">
        <h2>Daily P&L calendar (settled days — click a day to replay it)</h2>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <FliesCalendar days={data?.dailyPnl ?? []} onPick={onReplayDay} />}
      </section>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(20rem, 1fr))" }}>
        <DataCard title="By arm (legged only, settled)" headers={headers} loading={isLoading} rowCount={data?.byArm.length ?? 0}>
          <SummaryRows rows={data?.byArm} label="arm" />
        </DataCard>
        <DataCard title="By entry mode" headers={headers} loading={isLoading} rowCount={data?.byEntryMode.length ?? 0}>
          <SummaryRows rows={data?.byEntryMode} label="entryMode" />
        </DataCard>
        <DataCard title="By entry window (deliberately unranked)" headers={headers} loading={isLoading} rowCount={data?.byEntryWindow.length ?? 0}>
          <SummaryRows rows={data?.byEntryWindow} label="window" />
        </DataCard>
        <DataCard title="Fee drag by arm" headers={["arm", "gross", "fees", "net", "drag %"]} loading={isLoading} rowCount={data?.feeDrag.length ?? 0}>
          {data?.feeDrag.map((r) => (
            <tr key={r.arm}>
              <td>{r.arm}</td>
              <td>{fmtMoney(r.grossPnl)}</td>
              <td className="pnl-neg">{fmtMoney(r.fees)}</td>
              <td><PnlCell v={r.netPnl} /></td>
              <td className={r.feeDragPct !== null && r.feeDragPct > 30 ? "pnl-neg" : "muted"}>
                {r.feeDragPct !== null ? `${r.feeDragPct.toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </DataCard>
      </div>

      <section className="card">
        <div className="panel-head-row">
          <h2>Trade log — {logTotal.toLocaleString()} matching</h2>
          <div className="mode-toggle">
            {OUTCOMES.map((o) => (
              <button key={o} type="button" className={outcome === o ? "mode-btn active" : "mode-btn"} onClick={() => setOutcome(o)}>
                {o}
              </button>
            ))}
          </div>
          <input className="text-input" placeholder="search…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ textTransform: "none" }} />
        </div>
        <div className={`table-scroll ${logQuery.isPlaceholderData ? "table-busy" : ""}`}>
          <table className="data-table">
            <thead>
              <tr>
                <th>date</th><th>sym</th><th>arm</th><th>mode</th><th>kind</th><th>centre</th><th>window</th>
                <th>net</th><th>fees</th><th>P&L</th><th>latency</th><th></th>
              </tr>
            </thead>
            <tbody>
              {log.map((r, i) => (
                <tr key={i}>
                  <td>{r.tradeDate}</td>
                  <td>{r.symbol}</td>
                  <td className="muted">{r.arm ?? "—"}</td>
                  <td className="muted">{r.entryMode ?? "—"}</td>
                  <td>{r.kind === "fly" ? "fly" : r.kind === "iron_fly" ? "iron fly" : `short ${r.side}`}</td>
                  <td>{fmtNum(r.center, 0)}</td>
                  <td className="muted">{r.window ?? "—"}</td>
                  <td>{fmtNum(r.net, 2)}</td>
                  <td className="muted">{r.fees !== null ? fmtMoney(r.fees) : "—"}</td>
                  <td><PnlCell v={r.pnl} /></td>
                  <td className="muted">{r.latencyMin !== null ? `${r.latencyMin.toFixed(0)}m` : "—"}</td>
                  <td>{r.pinned && <span className="chain-badge chain-badge-short">pinned</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {logTotal > 0 && (
          <div className="card-footer">
            <Pager
              offset={logQuery.data?.offset ?? page.offset}
              limit={logQuery.data?.limit ?? page.limit}
              total={logTotal}
              onOffset={setOffset}
              onLimit={setLimit}
            />
          </div>
        )}
      </section>
    </div>
  );
}
