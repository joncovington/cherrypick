import { Fragment, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ManageAccount, ManageLevel, ManagePayload, ManageTrade } from "@console/shared";
import { Card, DataCard, sortRows, useSort, type SortHeader } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { PortfolioCards } from "./PortfolioCards";
import { ScenarioCards } from "./ScenarioCards";

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
  ROLL_FOR_CREDIT: "roll for credit",
  CLOSE_LEGS: "close legs",
  REVIEW: "review",
  SETTLES: "settles",
  HOLD: "hold",
};

// An action's chip colour: hold is blue, review amber, anything that exits or changes the trade red.
// SETTLES stays plain: nothing to do but let it expire.
const ACTION_CHIP: Record<string, string> = {
  HOLD: "chip-hold",
  REVIEW: "chip-warn",
  CLOSE: "chip-bad",
  TAKE_PROFIT: "chip-bad",
  ROLL_OR_CLOSE: "chip-bad",
  ROLL_FOR_CREDIT: "chip-bad",
  CLOSE_LEGS: "chip-bad",
};

function ActionChip({ action }: { action: string }) {
  return <span className={`chip ${ACTION_CHIP[action] ?? ""}`}>{ACTION_LABEL[action] ?? action}</span>;
}

/** An earnings trade's chip: amber while it holds for the call, red once the report is out and it closes. */
function EarningsChip({ et, label }: { et: NonNullable<ManageTrade["earningsTrade"]>; label: string }) {
  const report = `${et.report}${et.timeOfDay ? ` ${et.timeOfDay}` : ""}`;
  return (
    <span
      className={`chip ${et.passed ? "chip-bad" : "chip-warn"}`}
      title={
        et.passed
          ? `earnings trade: the ${report} report is out, close it`
          : `earnings trade: holding through the ${report} report, close after the call`
      }
    >
      {label}
    </span>
  );
}

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

/** A level's net price signed the suite's way (credit +, debit −), so a column of them sorts as cash. */
function signedLevel(l: ManageLevel | null): number | null {
  if (l === null || l.netPrice === null || l.netSide === null) return null;
  return l.netSide === "cr" ? l.netPrice : -l.netPrice;
}

function levelText(l: ManageLevel | null): string {
  if (!l) return "—";
  return `${net(l.netPrice, l.netSide)} (${fmtMoney(l.closeValue)})${l.basis === "max_loss" ? " · half max loss" : ""}`;
}

/** One card in an expanded trade's row: a small caps title, an optional chip, and its body. */
function DetailCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="md-card">
      <header>
        <h4>{title}</h4>
        {aside}
      </header>
      {children}
    </section>
  );
}

/** Label / value rows. A value is a node so a figure can carry its tone. */
function Kv({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="md-kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Money({ v }: { v: number | null }) {
  return <span className={tone(v)}>{fmtMoney(v)}</span>;
}

/**
 * The expanded trade: the plain-English verdict as a lead, then the facts behind it as a row of
 * cards (opened, exit levels, the closing order, what the model says it can still do, the rules, and
 * an earnings trade's report where it is one), then the legs. Every figure is packages/manage's;
 * this only lays them out.
 */
function TradeDetail({ t }: { t: ManageTrade }) {
  const et = t.earningsTrade;
  const credit = t.kind === "credit";
  return (
    <div className="md-detail">
      {t.explanation && <p className="md-lead">{t.explanation}</p>}
      <div className="md-cards">
        <DetailCard
          title="opened"
          aside={t.chain && t.chain.rolls > 0 ? <span className="chip">{t.chain.rolls} roll{t.chain.rolls === 1 ? "" : "s"}</span> : null}
        >
          {t.chain && t.chain.orders.length > 0 ? (
            <>
              <ul className="md-orders">
                {t.chain.orders.map((o, i) => (
                  <li key={i}>
                    <span className="md-order-head">
                      {o.date} · {i === 0 ? "open" : o.role}
                      <span className="md-price">{net(o.netPrice, o.netSide)}</span>
                    </span>
                    <span className="md-sub">{o.legs.join(" · ")}</span>
                  </li>
                ))}
              </ul>
              <Kv
                rows={[
                  [credit ? "net taken in" : "net paid in", fmtMoney(t.chain.cash === null ? null : Math.abs(t.chain.cash))],
                  ["fees", fmtMoney(t.fees)],
                  ["P&L gross", <Money key="g" v={t.pnlGross} />],
                  ["P&L net of fees", <Money key="n" v={t.pnlNet} />],
                ]}
              />
            </>
          ) : t.opening ? (
            <>
              <Kv
                rows={[
                  ["opened for", `${net(t.opening.netPrice, t.opening.netSide)} (${fmtMoney(t.opening.net)})`],
                  ["P&L gross", <Money key="g" v={t.pnlGross} />],
                ]}
              />
              {t.basis === "open_legs" && (
                <p className="md-note">from the open legs' average prices: no order chain was reconciled, so earlier rolls are not counted</p>
              )}
            </>
          ) : (
            <p className="md-note">no opening record</p>
          )}
        </DetailCard>

        {et && (
          <DetailCard
            title="earnings trade"
            aside={<EarningsChip et={et} label={et.passed ? "report out: close" : "holding for the call"} />}
          >
            <Kv
              rows={[
                ["report", `${et.report}${et.timeOfDay ? ` ${et.timeOfDay}` : ""}`],
                ["put on", et.leadDays === null ? "—" : `${et.leadDays} day${et.leadDays === 1 ? "" : "s"} before`],
              ]}
            />
            {t.heldForEarnings.length > 0 && (
              <>
                <p className="md-subhead">waiting for the call</p>
                <ul className="md-rules">
                  {t.heldForEarnings.map((f, i) => (
                    <li key={i}>
                      <ActionChip action={f.action} /> <code>{f.rule}</code>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </DetailCard>
        )}

        <DetailCard title="exit levels">
          <Kv
            rows={[
              ["take profit", levelText(t.takeProfit)],
              ["stop", levelText(t.stop)],
              ["time exit", t.timeExit ?? "—"],
              ...(t.earnings
                ? ([["earnings", `${t.earnings.date}${t.earnings.timeOfDay ? ` ${t.earnings.timeOfDay}` : ""} (${t.earnings.days}d)`]] as Array<
                    [string, ReactNode]
                  >)
                : []),
              ...(t.exDividend ? ([["ex-dividend", `${t.exDividend.date} (${t.exDividend.days}d)`]] as Array<[string, ReactNode]>) : []),
            ]}
          />
        </DetailCard>

        {t.order && (
          <DetailCard title="closing order" aside={<span className="md-price">{net(t.order.netPrice, t.order.netSide)}</span>}>
            <ul className="md-orders">
              {t.order.legs.map((l, i) => (
                <li key={i}>
                  <span className="md-order-head">
                    {l.side} {l.quantity} × {l.leg}
                    <span className="md-price">{px(l.mid)}</span>
                  </span>
                </li>
              ))}
            </ul>
            <Kv rows={[["at mid", <Money key="c" v={t.order.net} />]]} />
            <p className="md-note">a mid is not a fill</p>
          </DetailCard>
        )}

        {t.roll && (
          <DetailCard title="proposed roll" aside={<span className="md-price">{t.roll.credit === null ? "—" : `${t.roll.credit.toFixed(2)} cr`}</span>}>
            <ul className="md-orders">
              {t.roll.legs.map((l, i) => (
                <li key={i}>
                  <span className="md-order-head">{l}</span>
                </li>
              ))}
            </ul>
            <Kv rows={[["modelled", <Money key="r" v={t.roll.net} />]]} />
            <p className="md-note">modelled from today's IV; send only for a net credit</p>
          </DetailCard>
        )}

        {t.profile && (
          <DetailCard title="what it can still do">
            <Kv
              rows={[
                ["max loss from open", t.profile.unboundedLow ? "unbounded" : <Money key="m" v={t.profile.maxLossFromOpen} />],
                ["left to win", <Money key="w" v={t.profile.upsideFromNow} />],
                ["left to lose", <Money key="l" v={t.profile.downsideFromNow} />],
              ]}
            />
            <p className="md-note">modelled at {t.profile.horizon ?? "—"}: Black-Scholes at each leg's own IV, not a tradable price</p>
          </DetailCard>
        )}

        <DetailCard title="rules" aside={<span className="muted">{t.fired.length} fired</span>}>
          {t.fired.length === 0 ? (
            <p className="md-note">
              {t.heldForEarnings.length > 0
                ? `none acting today: ${t.heldForEarnings.length} waiting for the earnings call`
                : "none fired"}
            </p>
          ) : (
            <ul className="md-rules">
              {t.fired.map((f, i) => (
                <li key={i}>
                  <span className="md-rule-head">
                    <ActionChip action={f.action} /> <code>{f.rule}</code>
                    {f.confidence !== null ? <span className="muted"> {f.confidence}</span> : null}
                  </span>
                  <span className="md-sub">
                    {f.reason}
                    {f.legs && f.legs.length > 0 ? ` [${f.legs.join(", ")}]` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </DetailCard>
      </div>
      <div className="table-scroll">
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
    </div>
  );
}

/** Actions that ask a person to do nothing today: a hold waits on its levels, a settling trade on the print. */
const PASSIVE = new Set(["HOLD", "SETTLES"]);

/** The account strip both tabs open with: the book's size and what today asks of it. */
function AccountTiles({ a }: { a: ManageAccount }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action)).length;
  return (
    <TileGrid count={6}>
      <Tile label={`net liq · ${a.account ?? "?"}`} value={fmtMoney(a.netLiq)} />
      <Tile label="per-trade budget" value={fmtMoney(a.riskBudget)} title="max-loss budget per trade (risk.max_risk_pct of net liq)" />
      <Tile label="mark P&L (gross)" value={fmtMoney(a.pnlGross)} tone={a.pnlGross === null ? "dim" : a.pnlGross >= 0 ? "pos" : "neg"} />
      <Tile label="trades" value={String(a.trades.length)} />
      <Tile label="action needed" value={String(acting)} />
      <Tile label="holding" value={String(a.trades.length - acting)} tone="dim" />
    </TileGrid>
  );
}

/** Positions: what to do with each trade today, the actions first. */
function PositionsAccount({ a, loading }: { a: ManageAccount; loading: boolean }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action));
  const holding = a.trades.filter((t) => PASSIVE.has(t.action));
  return (
    <>
      <AccountTiles a={a} />
      <TradesCard title="action needed" trades={acting} loading={loading} empty="nothing to do today" variant="act" />
      <TradesCard title="holding" trades={holding} loading={loading} empty="no trades on hold" variant="hold" />
      {a.excluded.length > 0 && (
        <p className="muted" style={{ margin: 0 }}>
          not evaluated: {a.excluded.map((e) => `${e.underlying} (${e.legs} leg${e.legs === 1 ? "" : "s"}, ${e.reason})`).join("; ")}
        </p>
      )}
    </>
  );
}

/** Portfolio: the account read as one book -- its size, its greeks against their bands, how it
 *  behaves if the market moves, and the trades sized to fit it. */
function PortfolioAccount({ a }: { a: ManageAccount }) {
  return (
    <>
      <AccountTiles a={a} />
      {a.portfolio ? (
        <PortfolioCards p={a.portfolio} scenarios={<ScenarioCards p={a.portfolio} />} />
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          no portfolio reading for this account in this evaluation
        </p>
      )}
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
  const sort = useSort();
  const tail: Array<string | SortHeader> =
    variant === "act" ? ["reason"] : [{ label: "take profit", k: "tp" }, { label: "stop", k: "stop" }, "time exit"];
  // Each trade's detail row is rendered inside its own Fragment, so it follows its trade through a sort.
  const rows = sortRows(trades, sort, {
    trade: (t) => t.tradeId,
    action: (t) => ACTION_LABEL[t.action] ?? t.action,
    conf: (t) => t.confidence,
    kind: (t) => t.kind,
    pnl: (t) => t.pnlGross,
    capture: (t) => t.capture,
    dte: (t) => t.nearestShortDte,
    earnings: (t) => t.earnings?.days ?? null,
    tp: (t) => signedLevel(t.takeProfit),
    stop: (t) => signedLevel(t.stop),
  });
  return (
    <DataCard
      title={title}
      headers={[
        { label: "trade", k: "trade" },
        { label: "action", k: "action" },
        { label: "conf.", k: "conf" },
        { label: "kind", k: "kind" },
        { label: "mark P&L (gross)", k: "pnl" },
        { label: "capture / return", k: "capture" },
        { label: "short dte", k: "dte" },
        { label: "earnings", k: "earnings", title: "sorts by days to the announcement" },
        ...tail,
      ]}
      sort={sort}
      loading={loading}
      rowCount={trades.length}
      empty={empty}
    >
      {rows.map((t) => (
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
              {t.shape ? <span className="muted"> · {t.shape}</span> : null}
              {t.intent ? <span className="muted"> · {t.intent}</span> : null}
              {t.earningsTrade ? (
                <>
                  {" "}
                  <EarningsChip et={t.earningsTrade} label="earnings" />
                </>
              ) : null}
            </td>
            <td>
              <ActionChip action={t.action} />
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

/** Live → positions: each trade's action, rule and levels, actions first. */
export function PositionsPage() {
  return <EvaluationPage view="positions" />;
}

/** Live → portfolio: the account as one book, from the same evaluation. */
export function PortfolioPage() {
  return <EvaluationPage view="portfolio" />;
}

/**
 * Both tabs read one evaluation file (2026-10-18: split from the single `manage` tab, whose dozen
 * account cards had buried the action list). Each keeps the evaluation header, because its caveats
 * -- missing order chains, missing earnings dates, an unapplied rule -- qualify both.
 */
function EvaluationPage({ view }: { view: "positions" | "portfolio" }) {
  const title = view === "positions" ? "position evaluation" : "portfolio";
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError, dataUpdatedAt } = useManage(session);
  const sessions = data?.sessions ?? [];
  const picker =
    sessions.length > 1 ? (
      <select
        className="chip review-session-select"
        value={data?.session ?? ""}
        onChange={(e) => setSession(e.target.value)}
        aria-label="session"
      >
        {[...sessions].reverse().map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    ) : null;

  if (data && data.session === null) {
    return (
      <Card title={title} isError={isError}>
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
      <Card title={title} updatedAt={dataUpdatedAt} isError={isError} controls={picker}>
        <p className="muted" style={{ margin: 0 }}>
          {data?.session ?? "…"} · snapshot {data?.snapshotGeneratedAt ?? "—"} · ruleset {data?.ruleset ?? "—"} (
          {data?.rulesSource ?? "shipped default"}) · advice only: marks are mids, table P&L is gross (net of fees is in each trade's chain), and nothing here
          places an order
        </p>
        {data?.error && <p className="pnl-neg">evaluation error: {data.error}</p>}
        {data?.metricsError && (
          <p className="muted">earnings and dividend dates unavailable ({data.metricsError}); price rules still ran</p>
        )}
        {data?.transactionsError && (
          <p className="muted">
            order chains unavailable ({data.transactionsError}): every trade is priced from its open legs, so a rolled
            trade's earlier credits are not in its basis
          </p>
        )}
        {(data?.ruleWarnings ?? []).map((w) => (
          <p key={w} className="pnl-neg">
            rules: {w}
          </p>
        ))}
      </Card>
      {(data?.accounts ?? []).map((a) =>
        view === "positions" ? (
          <PositionsAccount key={a.account ?? "?"} a={a} loading={isLoading} />
        ) : (
          <PortfolioAccount key={a.account ?? "?"} a={a} />
        ),
      )}
      {view === "positions" && (
        <Card title="ruleset" collapseKey="manage-ruleset" defaultCollapsed>
          <pre style={{ margin: 0, fontSize: "0.8rem", whiteSpace: "pre-wrap" }}>
            {data?.rules ? JSON.stringify(data.rules, null, 2) : "—"}
          </pre>
        </Card>
      )}
    </div>
  );
}
