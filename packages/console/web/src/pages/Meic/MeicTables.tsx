import { useEffect, useState } from "react";
import type { MeicTradeRow, TradingMode } from "@console/shared";
import { useMeic } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, usePage } from "../../components/ScopeBar";
import { fmtCash, fmtPrice } from "../../lib/format";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";

/**
 * MEIC's two trade tables in the suite's standard layout (root CLAUDE.md, "Trade histories and
 * reports"): `positions` is every iron condor the session held, entry side only; `history` is the
 * closed ones, with entry and exit as signed cash, how it ended, gross, fees, settlement, slippage
 * and net. Both are pages of one server query (`view`), so a row cannot mean two things.
 *
 * MEIC's slippage is conceded inside the modelled fill prices, so it is already in gross: the
 * column is a measure and nothing subtracts it.
 */

export interface MeicScope {
  day: string | null;
  symbol: string | null;
  profile: string | null;
  era: string | null;
}

const OUTCOMES = ["all", "wins", "losses"] as const;

function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  const cls = s.includes("stop") ? "chain-badge-short" : s.includes("expire") ? "chain-badge-long" : "";
  return <span className={`chain-badge ${cls}`}>{status}</span>;
}

function sessionLabel(day: string | null): string {
  return day !== null ? ` on ${day}` : " on the latest session";
}

export function MeicPositionsTable({ mode, scope }: { mode: TradingMode; scope: MeicScope }) {
  const { page, setOffset, setLimit } = usePage([mode, scope.day, scope.symbol, scope.profile, scope.era]);
  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useMeic(mode, {
    ...scope,
    view: "positions",
    outcome: "all",
    reason: null,
    search: "",
    ...page,
  });
  const rows = data?.trades.rows ?? [];
  const total = data?.trades.total ?? 0;
  return (
    <DataCard
      title={`Positions — ${total.toLocaleString()}${sessionLabel(scope.day)}`}
      headers={["time", "sym", "put", "call", "wing", "qty", "price", "entry", "IVR", "status"]}
      numFrom={2}
      loading={isLoading}
      isError={isError}
      rowCount={rows.length}
      skeletonRows={10}
      busy={isPlaceholderData}
      empty="no positions on this session"
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
    >
      {rows.map((t) => (
        <tr key={`${t.mode}-${t.id}`}>
          <td className="muted">{t.entryTime?.slice(11, 16) ?? "—"}</td>
          <td>{t.symbol}</td>
          <td>{fmtNum(t.putStrike, 0)}</td>
          <td>{fmtNum(t.callStrike, 0)}</td>
          <td>{fmtNum(t.wingWidth, 0)}</td>
          <td>{fmtNum(t.quantity, 0)}</td>
          <td>{fmtPrice(t.netCredit)}</td>
          <td>{fmtCash(t.entryCash)}</td>
          <td className="muted">{t.ivRankAtEntry !== null ? `${(t.ivRankAtEntry * 100).toFixed(0)}%` : "—"}</td>
          <td><StatusBadge status={t.status} /></td>
        </tr>
      ))}
    </DataCard>
  );
}

/**
 * The history's columns (components/table/columns.ts). MEIC's slippage is conceded inside the
 * modelled fill prices, so `slip` is a measure beside the costs and nothing subtracts it.
 */
const HISTORY_COLUMNS: ColumnDef<MeicTradeRow>[] = [
  { id: "date", header: "date", kind: "describe", render: (r) => r.tradeDate },
  { id: "time", header: "time", kind: "describe", className: "muted", render: (r) => r.entryTime?.slice(11, 16) ?? "—" },
  { id: "sym", header: "sym", kind: "describe", render: (r) => r.symbol },
  { id: "put", header: "put", kind: "describe", numeric: true, render: (r) => fmtNum(r.putStrike, 0) },
  { id: "call", header: "call", kind: "describe", numeric: true, render: (r) => fmtNum(r.callStrike, 0) },
  { id: "wing", header: "wing", kind: "describe", numeric: true, render: (r) => fmtNum(r.wingWidth, 0) },
  { id: "qty", header: "qty", kind: "describe", numeric: true, render: (r) => fmtNum(r.quantity, 0) },
  {
    id: "price",
    header: "price",
    title: "net entry credit per share",
    kind: "describe",
    numeric: true,
    render: (r) => fmtPrice(r.netCredit),
  },
  { id: "how", header: "how", kind: "describe", className: "muted", render: (r) => r.exitKind ?? "—" },
  { id: "reason", header: "exit reason", kind: "describe", className: "muted", render: (r) => r.exitReason ?? "—" },
  {
    id: "entry",
    header: "entry",
    title: "entry cash flow for the whole position: + received, − paid",
    kind: "money",
    render: (r) => fmtCash(r.entryCash),
  },
  {
    id: "exit",
    header: "exit",
    title: "the close, the stop, or the settlement at the bell",
    kind: "money",
    render: (r) => fmtCash(r.exitCash),
  },
  { id: "gross", header: "gross", title: "entry + exit, before any cost", kind: "money", render: (r) => fmtMoney(r.gross) },
  {
    id: "fees",
    header: "fees",
    title: "trading fees: commissions and exchange fees",
    kind: "money",
    className: "muted",
    render: (r) => (
      <span title={r.settlementFees === null ? "the fee total: its settlement share was not recorded" : undefined}>
        {fmtMoney(r.fees)}
      </span>
    ),
  },
  {
    id: "settle",
    header: "settle",
    title: "exercise / assignment fees at settlement",
    kind: "money",
    className: "muted",
    render: (r) => (r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)),
  },
  {
    id: "slip",
    header: "slip",
    title: "what the modelled fills gave up against mid — already inside gross, never subtracted again",
    kind: "money",
    className: "muted",
    render: (r) => fmtMoney(r.slippage),
  },
  { id: "net", header: "net", title: "gross − fees − settlement", kind: "money", pinned: true, render: (r) => <PnlCell v={r.net} /> },
];

export function MeicHistoryTable({
  mode,
  scope,
  reasons,
}: {
  mode: TradingMode;
  scope: MeicScope;
  reasons: string[];
}) {
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("all");
  const [reason, setReason] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);
  // A date range REPLACES the header's session (the server drops the day when either bound is set):
  // "across these days" is a question the one-session scope cannot answer.
  const { from, to } = useUrlDateRange();
  const ranged = from !== null || to !== null;
  const { page, setOffset, setLimit } = usePage([
    mode, scope.day, scope.symbol, scope.profile, scope.era, outcome, reason, debounced, from, to,
  ]);
  const { data, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useMeic(mode, {
    ...scope,
    view: "history",
    outcome,
    reason,
    search: debounced,
    from,
    to,
    ...page,
  });
  const total = data?.trades.total ?? 0;
  const t = data?.totals;
  const where = ranged ? ` from ${from ?? "the start"} to ${to ?? "today"}` : sessionLabel(scope.day);
  return (
    <HistoryTable
      table="meic-history"
      title={`History — ${total.toLocaleString()} closed${where}`}
      defs={HISTORY_COLUMNS}
      rows={data?.trades.rows ?? []}
      rowKey={(r) => `${r.mode}-${r.id}`}
      loading={isLoading}
      isError={isError}
      busy={isPlaceholderData}
      empty={
        ranged ? "nothing closed in this date range matches these filters" : "nothing closed on this session matches these filters"
      }
      updatedAt={dataUpdatedAt}
      allLabel="session"
      allTitle="the session picked in the header, as every other page on this frame shows"
      filters={
        <>
          <div className="mode-toggle" role="group" aria-label="outcome filter">
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
            {reasons.filter((r) => r !== "open").map((r) => (
              <option key={r} value={r}>{r}</option>
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
      footer={
        total > 0 && (
          <>
            {t !== undefined && t.trades > 0 && (
              <span
                className="chip"
                title="Over every row matching these filters — not just this page. Gross − fees − settlement = net. Slippage is inside the fill prices, so inside gross; it is shown, never subtracted."
              >
                net <PnlCell v={t.net} /> · {t.trades.toLocaleString()} trades · {t.sessions.toLocaleString()} session
                {t.sessions === 1 ? "" : "s"} · gross {fmtMoney(t.gross)} · fees {fmtMoney(t.fees)} · settlement{" "}
                {fmtMoney(t.settlementFees)} · slippage {fmtMoney(t.slippage)}
              </span>
            )}
            <Pager
              offset={data?.trades.offset ?? page.offset}
              limit={data?.trades.limit ?? page.limit}
              total={total}
              onOffset={setOffset}
              onLimit={setLimit}
            />
          </>
        )
      }
    />
  );
}
