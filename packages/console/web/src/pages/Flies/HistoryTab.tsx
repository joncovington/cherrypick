import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useFliesTradeLog, fliesQuery, type FliesFilter } from "../../lib/api";
import { DataCard, PnlCell, SkeletonRows, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { clockTime, structureLabel, wingWidth } from "./structure";
import { fmtCash, fmtPrice } from "../../lib/format";

interface Summary {
  trades: number;
  sessions: number;
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

interface CompletionSummary {
  trades: number;
  completed: number;
  completionPct: number | null;
  medianLatencyMin: number | null;
  pinned: number;
  pinnedPct: number | null;
}

interface History {
  /** The regime-cuts writer's thin floor; null when it has published none (see readers/flies.ts). */
  thinBelowSessions: number | null;
  byArm: Array<{ arm: string } & Summary>;
  byEntryMode: Array<{ entryMode: string } & Summary>;
  byEntryHour: Array<{ hour: string } & Summary>;
  byArmHour: Array<{ arm: string; hour: string } & CompletionSummary>;
  feeDrag: Array<{ arm: string } & Summary>;
  dailyPnl: Array<{ date: string; trades: number; netPnl: number }>;
}

/** "10:00-11:00" -> "10-11", so the hour matrix stays narrow enough to need no horizontal scroll. */
function shortHour(hour: string): string {
  const m = /^(\d{2}):00-(\d{2}):00$/.exec(hour);
  return m ? `${m[1]}–${m[2]}` : hour;
}

function useHistory(mode: TradingMode, filter: FliesFilter) {
  return useQuery<History>({
    queryKey: ["flies-history", mode, filter.arm, filter.symbol, filter.era],
    queryFn: async () => {
      const res = await fetch(`/api/flies/history?${fliesQuery(mode, { ...filter, date: null })}`);
      if (!res.ok) throw new Error(`history: HTTP ${res.status}`);
      return (await res.json()) as History;
    },
    refetchInterval: 60_000,
  });
}

function SummaryRows<T extends Summary>({
  rows,
  label,
  thinBelow,
}: {
  rows: T[] | undefined;
  label: keyof T;
  /** The regime-cuts writer's declared floor; null when it has published none. */
  thinBelow: number | null;
}) {
  return (
    <>
      {rows?.map((r) => {
        // Sessions are the unit of independence, so they decide whether the rest of the row means
        // anything. Below the writer's floor the net, win rate and profit factor are a day or two of
        // weather; dimmed rather than hidden, because the row is still the honest record of what
        // happened. The floor is the one core.regimecuts declares, read from its artifact -- not a 3
        // kept here, which is how two surfaces start disagreeing about the same row.
        const thin = thinBelow !== null && r.sessions < thinBelow;
        return (
          <tr key={String(r[label])} style={thin ? { opacity: 0.55 } : undefined}>
            <td>
              {String(r[label])}
              {thin && (
                <span className="muted" style={{ fontSize: 10, marginLeft: 4 }} title={`Fewer than ${thinBelow} sessions — same-day trades share a regime, so this is a handful of independent observations however many trades it holds.`}>
                  thin
                </span>
              )}
            </td>
            <td>{r.trades}</td>
            <td className={thin ? "muted" : undefined}>{r.sessions}</td>
            <td><PnlCell v={r.netPnl} /></td>
            <td>{r.winRatePct !== null ? `${r.winRatePct.toFixed(0)}%` : "—"}</td>
            <td>{r.avgPnl !== null ? fmtMoney(r.avgPnl) : "—"}</td>
            <td>{r.profitFactor !== null ? r.profitFactor.toFixed(2) : "—"}</td>
          </tr>
        );
      })}
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

export function HistoryTab({
  mode,
  filter,
  onReplayDay,
}: {
  mode: TradingMode;
  filter: FliesFilter;
  onReplayDay: (date: string) => void;
}) {
  // `date` is dropped on purpose: this tab answers questions ACROSS sessions, so pinning the day
  // selected on the Today tab would empty it.
  const { data, isLoading } = useHistory(mode, filter);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [search, setSearch] = useState("");

  // Search reaches the DB now, so let typing settle first.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Explicit date bounds, either side independently empty. The search box could already match a
  // date as text, which answers "2026-08" but not "the week either side of the cadence change" —
  // and every measurement break in this module is a date, so a log filterable only by prefix cannot
  // be pointed at one side of a break.
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const range = { from: from === "" ? null : from, to: to === "" ? null : to };

  const { page, setOffset, setLimit } = usePage([
    mode, outcome, debouncedSearch, from, to, filter.arm, filter.era,
  ]);
  // `filter.arm` and `filter.era` are the PAGE-level scope, the same one every other card on this
  // tab already honours. The log took era and ignored arm, so narrowing to one arm left it
  // answering for all of them beside tables that did not.
  const logQuery = useFliesTradeLog(
    mode, outcome, debouncedSearch, page, filter.era, range, filter.arm,
  );
  const log = logQuery.data?.rows ?? [];
  const logTotal = logQuery.data?.total ?? 0;
  const totals = logQuery.data?.totals;

  // Sessions beside trades, everywhere. Same-day trades share a regime and are not independent
  // observations — this module's own experiment docs put the effective N at the day count, so a
  // per-arm net over 40 trades from 3 sessions is a 3-sample reading wearing a 40-sample coat.
  const headers = ["", "trades", "sessions", "net", "win %", "avg", "PF"];

  // Arm x hour completion matrix: rows by arm, columns by the hours that actually appear, sorted
  // chronologically. A Map per arm keeps a missing (arm, hour) combination a real "no trades" dash
  // rather than a zero, matching this module's own "None never means zero" rule.
  const hourColumns = [...new Set((data?.byArmHour ?? []).map((r) => r.hour))].sort((a, b) => a.localeCompare(b));
  const armRows: Array<[string, Map<string, CompletionSummary>]> = [];
  {
    const byArmMap = new Map<string, Map<string, CompletionSummary>>();
    for (const r of data?.byArmHour ?? []) {
      let hourMap = byArmMap.get(r.arm);
      if (hourMap === undefined) {
        hourMap = new Map<string, CompletionSummary>();
        byArmMap.set(r.arm, hourMap);
      }
      hourMap.set(r.hour, r);
    }
    for (const [arm, hourMap] of byArmMap) armRows.push([arm, hourMap]);
  }

  return (
    <div className="cards cards-wide">
      <section className="card">
        <h2>Daily P&L calendar (settled days — click a day to replay it)</h2>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <FliesCalendar days={data?.dailyPnl ?? []} onPick={onReplayDay} />}
      </section>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(34rem, 1fr))" }}>
        <DataCard title="By arm (legged only, settled)" headers={headers} loading={isLoading} rowCount={data?.byArm.length ?? 0}>
          <SummaryRows rows={data?.byArm} label="arm" thinBelow={data?.thinBelowSessions ?? null} />
        </DataCard>
        <DataCard title="By entry mode" headers={headers} loading={isLoading} rowCount={data?.byEntryMode.length ?? 0}>
          <SummaryRows rows={data?.byEntryMode} label="entryMode" thinBelow={data?.thinBelowSessions ?? null} />
        </DataCard>
        <DataCard title="By entry hour (deliberately unranked)" headers={headers} loading={isLoading} rowCount={data?.byEntryHour.length ?? 0}>
          <SummaryRows rows={data?.byEntryHour} label="hour" thinBelow={data?.thinBelowSessions ?? null} />
        </DataCard>
        <DataCard title="Fee drag by arm" headers={["arm", "gross", "fees", "net", "drag %"]} loading={isLoading} rowCount={data?.feeDrag.length ?? 0}>
          {data?.feeDrag.map((r) => (
            <tr key={r.arm}>
              <td>{r.arm}</td>
              <td>{fmtMoney(r.grossPnl)}</td>
              <td className="pnl-neg">{fmtMoney(r.fees)}</td>
              <td><PnlCell v={r.netPnl} /></td>
              <td className="muted">
                {r.feeDragPct !== null ? `${r.feeDragPct.toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </DataCard>
      </div>

      <section className="card">
        <h2>Completion &amp; pin rate — by arm &times; entry hour</h2>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th></th>
                {hourColumns.map((h) => (
                  <th key={h}>{shortHour(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRows n={3} cols={hourColumns.length + 1} />
              ) : armRows.length === 0 ? (
                <tr>
                  <td colSpan={hourColumns.length + 1} className="muted">no rows</td>
                </tr>
              ) : (
                armRows.map(([arm, byHour]) => (
                  <tr key={arm}>
                    <td>{arm}</td>
                    {hourColumns.map((h) => {
                      const c = byHour.get(h);
                      if (c === undefined || c.trades === 0) return <td key={h} className="muted">—</td>;
                      const cls = c.completionPct === null ? undefined : c.completionPct >= 70 ? "pnl-pos" : c.completionPct < 55 ? "pnl-neg" : undefined;
                      return (
                        <td key={h} className={cls} title={`${c.trades} trades, ${c.completed} completed`}>
                          {c.completionPct !== null ? `${c.completionPct.toFixed(0)}%` : "—"}
                          {" · "}
                          {c.medianLatencyMin !== null ? `${c.medianLatencyMin.toFixed(0)}m` : "—"}
                          {" · "}
                          {c.pinnedPct !== null ? `${c.pinnedPct.toFixed(0)}%` : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
          Each cell reads completion % · median minutes to complete · pinned %. Completion is whether
          the second leg ever filled; pinned is a settlement inside the short strike's band. A dash
          means no trades entered that arm in that hour.
        </p>
      </section>

      <section className="card">
        <div className="panel-head-row">
          <h2>Trade log — {logTotal.toLocaleString()} matching</h2>
          {totals !== undefined && totals.trades > 0 && (
            // Over every matching row, not the page. Sessions ride beside the net because same-day
            // trades share a regime and are not independent observations — this module's own
            // experiment docs put the effective N at the session count, so a net over 40 trades
            // from 3 sessions is a 3-sample reading wearing a 40-sample coat.
            <span
              className="chip"
              title={
                "Over every row matching these filters — not just this page. Gross − fees − settlement = net. " +
                "Slippage is what the modelled fills conceded against mid: already inside gross, never subtracted again" +
                (totals.slippageTrades < totals.trades
                  ? ` — recorded on ${totals.slippageTrades.toLocaleString()} of these ${totals.trades.toLocaleString()} trades.`
                  : ".")
              }
            >
              net <PnlCell v={totals.netPnl} /> · {totals.trades.toLocaleString()} trades ·{" "}
              {totals.sessions.toLocaleString()} session{totals.sessions === 1 ? "" : "s"} · gross{" "}
              {fmtMoney(totals.grossPnl)} · fees {fmtMoney(totals.fees)} · settlement {fmtMoney(totals.settlementFees)}
              {" "}
              · slippage{" "}
              {totals.slippageTrades === totals.trades
                ? fmtMoney(totals.slippage)
                : `${fmtMoney(totals.slippage)} (recorded on ${totals.slippageTrades.toLocaleString()} of ${totals.trades.toLocaleString()})`}
            </span>
          )}
          {/* A filter, not a tab strip: role=group rather than tablist, which would promise
              tab semantics for something that narrows one table. */}
          <div className="mode-toggle" role="group" aria-label="outcome filter">
            {OUTCOMES.map((o) => (
              <button key={o} type="button" className={outcome === o ? "mode-btn active" : "mode-btn"} onClick={() => setOutcome(o)}>
                {o}
              </button>
            ))}
          </div>
          <input className="text-input" placeholder="search…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ textTransform: "none" }} />
          <label className="muted" style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}>
            from
            <input className="text-input" type="date" value={from} max={to === "" ? undefined : to} onChange={(e) => setFrom(e.target.value)} aria-label="from date" />
          </label>
          <label className="muted" style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}>
            to
            <input className="text-input" type="date" value={to} min={from === "" ? undefined : from} onChange={(e) => setTo(e.target.value)} aria-label="to date" />
          </label>
          {(from !== "" || to !== "") && (
            <button type="button" className="mode-btn" onClick={() => { setFrom(""); setTo(""); }}>
              clear dates
            </button>
          )}
        </div>
        <div className={`table-scroll ${logQuery.isPlaceholderData ? "table-busy" : ""}`}>
          <table className="data-table">
            <thead>
              <tr>
                <th>date</th><th>time</th><th>sym</th><th>arm</th><th>mode</th><th>kind</th>
                <th>centre</th><th title="wing width in points; near/far when the wing is broken">wing</th>
                <th>window</th><th>qty</th>
                <th title="net entry price per share — cr received, db paid">price</th>
                <th title="entry cash flow for the whole position: + received, − paid">entry</th>
                <th title="exit cash flow: the close, or the settlement payoff at the bell">exit</th>
                <th>how</th>
                <th title="entry + exit, before any cost">gross</th>
                <th title="trading fees: commissions and exchange fees">fees</th>
                <th title="exercise / assignment fees at settlement">settle</th>
                <th title="what the modelled fills gave up against mid — already inside gross, never subtracted again">slip</th>
                <th title="gross − fees − settlement">net</th>
                <th>latency</th><th></th>
              </tr>
            </thead>
            <tbody>
              {log.map((r, i) => (
                <tr key={i}>
                  <td>{r.tradeDate}</td>
                  <td className="muted">{clockTime(r.entryTime)}</td>
                  <td>{r.symbol}</td>
                  <td className="muted">{r.arm ?? "—"}</td>
                  <td className="muted">{r.entryMode ?? "—"}</td>
                  <td>{structureLabel(r.kind, r.side)}</td>
                  <td>{fmtNum(r.center, 0)}</td>
                  <td>{wingWidth(r.wingWidth, r.farWidth)}</td>
                  <td className="muted">{r.window ?? "—"}</td>
                  <td>{r.quantity ?? "—"}</td>
                  <td>{fmtPrice(r.price)}</td>
                  <td>{fmtCash(r.entryCash)}</td>
                  <td>{fmtCash(r.exitCash)}</td>
                  <td className="muted">{r.exitKind ?? "—"}</td>
                  <td>{fmtMoney(r.gross)}</td>
                  <td className="muted" title={r.settlementFees === null ? "the fee total: its settlement share was not recorded" : undefined}>
                    {fmtMoney(r.fees)}
                  </td>
                  <td className="muted">{r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)}</td>
                  <td className="muted" title={r.slippage === null ? "not recorded: flies records the slippage its modelled fills concede from 2026-09-25 on, and a live fill is the broker's own price, with none modelled" : "conceded against mid — inside gross, never subtracted"}>
                    {r.slippage === null ? "n/r" : fmtMoney(r.slippage)}
                  </td>
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
