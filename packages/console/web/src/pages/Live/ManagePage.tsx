import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ManageAccount, ManageLevel, ManagePayload, ManageTrade } from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";

/**
 * The daily position evaluation (packages/manage), read-only. Every trade in the live account judged
 * against the configured ruleset: what to do, which rule says so, at what confidence, and the levels
 * a held trade is waiting on. The verdicts are the package's -- this page only shows them -- and it
 * holds no path that touches an order: the closing order listed is what a person would enter.
 */
export function useManage(session: string | undefined) {
  return useQuery<ManagePayload>({
    queryKey: ["manage", session ?? "latest"],
    queryFn: async () => {
      const res = await fetch(`/api/manage${session ? `?session=${session}` : ""}`);
      if (!res.ok) throw new Error(`manage: HTTP ${res.status}`);
      return (await res.json()) as ManagePayload;
    },
    // One evaluation a day; polling hard would only re-read the same file.
    refetchInterval: 60_000,
  });
}

const RIGHT = { textAlign: "right" } as const;

/** A per-share option price: no cr/db, because the side is the order leg's, not the price's. */
function px(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

const ACTION_LABEL: Record<string, string> = {
  CLOSE: "close",
  TAKE_PROFIT: "take profit",
  ROLL_OR_CLOSE: "roll or close",
  CLOSE_LEGS: "close legs",
  REVIEW: "review",
  SETTLES: "settles",
  HOLD: "hold",
};

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined) return "muted";
  return v >= 0 ? "pnl-pos" : "pnl-neg";
}

function pct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
}

/** A net price as the suite writes it: `1.25 cr` / `0.40 db`. */
function net(price: number | null, side: "cr" | "db" | null): string {
  return price === null || side === null ? "—" : `${price.toFixed(2)} ${side}`;
}

function levelText(l: ManageLevel | null): string {
  if (!l) return "—";
  return `${net(l.netPrice, l.netSide)} (${fmtMoney(l.closeValue)})${l.basis === "max_loss" ? " · half max loss" : ""}`;
}

function TradeDetail({ t }: { t: ManageTrade }) {
  return (
    <div className="manage-detail" style={{ display: "grid", gap: "0.75rem", padding: "0.5rem 0" }}>
      {t.opening && (
        <div>
          <strong>opened for</strong> · net {net(t.opening.netPrice, t.opening.netSide)} ({fmtMoney(t.opening.net)}
          {t.opening.netSide === "cr" ? " received" : " paid"})
        </div>
      )}
      {t.order && (
        <div>
          <strong>closing order</strong> · net {net(t.order.netPrice, t.order.netSide)} ({fmtMoney(t.order.net)}) at mid
          <ul style={{ margin: "0.25rem 0 0 1rem" }}>
            {t.order.legs.map((l, i) => (
              <li key={i}>
                {l.side} {l.quantity} × {l.leg} @ {px(l.mid)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <strong>exit levels</strong> · take profit {levelText(t.takeProfit)} · stop {levelText(t.stop)} · time exit{" "}
        {t.timeExit ?? "—"}
        {t.earnings && (
          <>
            {" "}
            · earnings {t.earnings.date}
            {t.earnings.timeOfDay ? ` ${t.earnings.timeOfDay}` : ""} ({t.earnings.days}d)
          </>
        )}
        {t.exDividend && <> · ex-dividend {t.exDividend.date}</>}
      </div>
      {t.profile && (
        <div className="muted">
          modelled at {t.profile.horizon ?? "—"} (Black-Scholes at each leg's own IV, not a tradable price): max loss from
          open {t.profile.unboundedLow ? "unbounded" : fmtMoney(t.profile.maxLossFromOpen)} · left to win{" "}
          {fmtMoney(t.profile.upsideFromNow)} · left to lose {fmtMoney(t.profile.downsideFromNow)}
        </div>
      )}
      <div>
        <strong>rules fired</strong>
        {t.fired.length === 0 ? (
          <span className="muted"> · none</span>
        ) : (
          <ul style={{ margin: "0.25rem 0 0 1rem" }}>
            {t.fired.map((f, i) => (
              <li key={i}>
                <code>{f.rule}</code> → {ACTION_LABEL[f.action] ?? f.action}
                {f.confidence !== null ? ` (${f.confidence})` : ""}: {f.reason}
                {f.legs && f.legs.length > 0 ? <span className="muted"> [{f.legs.join(", ")}]</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <table className="data-table num-from-2">
        <thead>
          <tr>
            <th>leg</th>
            <th>dte</th>
            <th>open</th>
            <th>mid</th>
            <th>capture / return</th>
            <th>delta</th>
            <th>iv</th>
          </tr>
        </thead>
        <tbody>
          {t.legs.map((l, i) => (
            <tr key={i}>
              <td>{l.label}</td>
              <td>{l.dte ?? "—"}</td>
              <td>{px(l.openPrice)}</td>
              <td>{px(l.mid)}</td>
              <td className={tone(l.capture)}>{pct(l.capture)}</td>
              <td>{l.delta === null ? "—" : l.delta.toFixed(2)}</td>
              <td>{l.iv === null ? "—" : `${(l.iv * 100).toFixed(0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Actions that ask a person to do nothing today: a hold waits on its levels, a settling trade on the print. */
const PASSIVE = new Set(["HOLD", "SETTLES"]);

function AccountView({ a, loading }: { a: ManageAccount; loading: boolean }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action));
  const holding = a.trades.filter((t) => PASSIVE.has(t.action));
  return (
    <>
      <TileGrid count={6}>
        <Tile label={`net liq · ${a.account ?? "?"}`} value={fmtMoney(a.netLiq)} />
        <Tile label="per-trade budget" value={fmtMoney(a.riskBudget)} title="max-loss budget per trade (risk.max_risk_pct of net liq)" />
        <Tile label="mark P&L (gross)" value={fmtMoney(a.pnlGross)} tone={a.pnlGross === null ? "dim" : a.pnlGross >= 0 ? "pos" : "neg"} />
        <Tile label="trades" value={String(a.trades.length)} />
        <Tile label="action needed" value={String(acting.length)} />
        <Tile label="holding" value={String(holding.length)} tone="dim" />
      </TileGrid>
      <TradesCard title="action needed" trades={acting} loading={loading} empty="nothing to do today" variant="act" />
      <TradesCard title="holding" trades={holding} loading={loading} empty="no trades on hold" variant="hold" />
    </>
  );
}

/**
 * One table of trades. `act` rows carry the rule's reason; `hold` rows carry the levels the trade is
 * waiting on instead, since "no rule fired" is the same sentence on every one of them.
 */
function TradesCard({
  title,
  trades,
  loading,
  empty,
  variant,
}: {
  title: string;
  trades: ManageTrade[];
  loading: boolean;
  empty: string;
  variant: "act" | "hold";
}) {
  const [open, setOpen] = useState<string | null>(null);
  const tail = variant === "act" ? ["reason"] : ["take profit", "stop", "time exit"];
  return (
    <DataCard
      title={title}
      headers={["trade", "action", "conf.", "kind", "mark P&L (gross)", "capture / return", "short dte", "earnings", ...tail]}
      loading={loading}
      rowCount={trades.length}
      empty={empty}
    >
      {trades.map((t) => (
        <Fragment key={t.tradeId}>
          <tr>
            <td>
              <button
                type="button"
                className="btn btn-quiet"
                aria-expanded={open === t.tradeId}
                onClick={() => setOpen(open === t.tradeId ? null : t.tradeId)}
              >
                {open === t.tradeId ? "▾" : "▸"} {t.tradeId}
              </button>
              {t.intent ? <span className="muted"> · {t.intent}</span> : null}
            </td>
            <td>
              <span className="chip">{ACTION_LABEL[t.action] ?? t.action}</span>
            </td>
            <td style={RIGHT}>{t.confidence ?? "—"}</td>
            <td>{t.kind ?? "—"}</td>
            <td className={tone(t.pnlGross)} style={RIGHT}>{fmtMoney(t.pnlGross)}</td>
            <td className={tone(t.capture)} style={RIGHT}>{pct(t.capture)}</td>
            <td style={RIGHT}>{t.nearestShortDte ?? "—"}</td>
            <td>{t.earnings ? `${t.earnings.date} (${t.earnings.days}d)` : "—"}</td>
            {variant === "act" ? (
              <td style={{ whiteSpace: "normal", minWidth: "18rem" }}>
                {t.reason}
                {t.conflicts.length > 0 ? <span className="muted"> · conflicts: {t.conflicts.join(", ")}</span> : null}
              </td>
            ) : (
              <>
                <td style={RIGHT}>{net(t.takeProfit?.netPrice ?? null, t.takeProfit?.netSide ?? null)}</td>
                <td style={RIGHT}>{net(t.stop?.netPrice ?? null, t.stop?.netSide ?? null)}</td>
                <td>{t.timeExit ?? "—"}</td>
              </>
            )}
          </tr>
          {t.explanation && (
            <tr className="manage-explanation">
              <td colSpan={8 + tail.length} style={{ whiteSpace: "normal", paddingTop: 0, paddingLeft: "2rem" }}>
                <span className="muted">{t.explanation}</span>
              </td>
            </tr>
          )}
          {open === t.tradeId && (
            <tr>
              <td colSpan={8 + tail.length}>
                <TradeDetail t={t} />
              </td>
            </tr>
          )}
        </Fragment>
      ))}
    </DataCard>
  );
}

export function ManagePage() {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError, dataUpdatedAt } = useManage(session);
  const sessions = data?.sessions ?? [];
  const picker =
    sessions.length > 1 ? (
      <select value={data?.session ?? ""} onChange={(e) => setSession(e.target.value)} aria-label="session">
        {[...sessions].reverse().map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    ) : null;

  if (data && data.session === null) {
    return (
      <Card title="position evaluation" isError={isError}>
        <p className="muted">
          No evaluation has been written yet. It runs at 10:00 ET on trading days once <code>manage.enabled</code> is true in
          the orchestrator config (then re-run <code>install</code>), or by hand with <code>run.py manage</code>. The ruleset
          is <code>~/.cherrypick/config/manage.json</code>.
        </p>
      </Card>
    );
  }

  return (
    <div className="view-fade" style={{ display: "grid", gap: "1rem" }}>
      <Card title="position evaluation" updatedAt={dataUpdatedAt} isError={isError} controls={picker}>
        <p className="muted" style={{ margin: 0 }}>
          {data?.session ?? "…"} · snapshot {data?.snapshotGeneratedAt ?? "—"} · ruleset {data?.ruleset ?? "—"} (
          {data?.rulesSource ?? "shipped default"}) · advice only: marks are mids, P&L is gross of fees, and nothing here
          places an order
        </p>
        {data?.error && <p className="pnl-neg">evaluation error: {data.error}</p>}
        {data?.metricsError && (
          <p className="muted">earnings and dividend dates unavailable ({data.metricsError}); price rules still ran</p>
        )}
        {(data?.ruleWarnings ?? []).map((w) => (
          <p key={w} className="pnl-neg">
            rules: {w}
          </p>
        ))}
      </Card>
      {(data?.accounts ?? []).map((a) => (
        <AccountView key={a.account ?? "?"} a={a} loading={isLoading} />
      ))}
      <Card title="ruleset" collapseKey="manage-ruleset" defaultCollapsed>
        <pre style={{ margin: 0, fontSize: "0.8rem", whiteSpace: "pre-wrap" }}>
          {data?.rules ? JSON.stringify(data.rules, null, 2) : "—"}
        </pre>
      </Card>
    </div>
  );
}
