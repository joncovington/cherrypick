import { Fragment, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ManageAccount, ManageLevel, ManagePayload, ManageTrade } from "@console/shared";
import { Card, DataCard, sortRows, useSort, type SortHeader } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtCash, fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { ActionChip, DetailCard, Kv, Money, tone } from "./manageParts";
import { PortfolioCards } from "./PortfolioCards";
import { ScenarioCards } from "./ScenarioCards";
import { WheelCards } from "./WheelCards";

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

// A stop set from the max loss rather than the premium. The share is the ruleset's, not this page's.
function levelText(l: ManageLevel | null): string {
  if (!l) return "—";
  return `${net(l.netPrice, l.netSide)} (${fmtMoney(l.closeValue)})${l.basis === "max_loss" ? " · from max loss" : ""}`;
}

/** ` (3d)`, or nothing where the evaluation did not count the days. */
function daysText(d: number | null): string {
  return d === null ? "" : ` (${d}d)`;
}

/** An earnings trade before its call: its take profit and stop wait, and it closes after the call. */
function heldForCall(t: ManageTrade): boolean {
  return t.earningsTrade !== null && !t.earningsTrade.passed;
}

// manage's own ranking (evaluate.PRIORITY), so the action column sorts by urgency, not alphabet.
const ACTION_PRIORITY: Record<string, number> = {
  ROLL_FOR_CREDIT: 0,
  CLOSE: 1,
  TAKE_PROFIT: 2,
  ROLL_OR_CLOSE: 3,
  CLOSE_LEGS: 4,
  REVIEW: 5,
  SETTLES: 6,
  HOLD: 7,
};

const HELD_TITLE = "an earnings trade: its take profit and stop wait for the call, and it closes after it";

/**
 * The expanded trade: the plain-English verdict as a lead, then the facts behind it as a row of
 * cards (opened, exit levels, the closing order, what the model says it can still do, the rules, and
 * an earnings trade's report where it is one), then the legs. Every figure is packages/manage's;
 * this only lays them out.
 */
function TradeDetail({ t }: { t: ManageTrade }) {
  const et = t.earningsTrade;
  const held = heldForCall(t);
  // Entry + close at mid = gross; gross − fees = net. Fees are a cost, shown positive and subtracted once.
  const fees = t.fees === null ? null : Math.abs(t.fees);
  const cashLabel = (v: number | null) => (v !== null && v < 0 ? "net paid in" : "net taken in");
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
                  [cashLabel(t.chain.cash), fmtCash(t.chain.cash)],
                  ["close at mid", fmtCash(t.value)],
                  ["P&L gross", <Money key="g" v={t.pnlGross} />],
                  ["fees paid", fmtMoney(fees)],
                  ["P&L net of fees", <Money key="n" v={t.pnlNet} />],
                ]}
              />
            </>
          ) : t.opening ? (
            <>
              <Kv
                rows={[
                  ["opened for", `${net(t.opening.netPrice, t.opening.netSide)} (${fmtCash(t.opening.net)})`],
                  ["close at mid", fmtCash(t.value)],
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
              ["take profit", levelText(t.takeProfit) + (held ? " · waits for the call" : "")],
              ["stop", levelText(t.stop) + (held ? " · waits for the call" : "")],
              ["time exit", t.timeExit ? `${t.timeExit}${et ? " · after the call" : ""}` : "—"],
              ...(t.earnings
                ? ([
                    ["earnings", `${t.earnings.date}${t.earnings.timeOfDay ? ` ${t.earnings.timeOfDay}` : ""}${daysText(t.earnings.days)}`],
                  ] as Array<[string, ReactNode]>)
                : []),
              ...(t.exDividend
                ? ([["ex-dividend", `${t.exDividend.date}${daysText(t.exDividend.days)}`]] as Array<[string, ReactNode]>)
                : []),
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
/** The ruleset's `risk.oversize_multiple`: how many budgets of modelled downside flag a trade. */
function oversizeMultiple(rules: Record<string, unknown> | null | undefined): number | null {
  const risk = rules?.["risk"];
  const m = risk && typeof risk === "object" ? (risk as Record<string, unknown>)["oversize_multiple"] : null;
  return typeof m === "number" && Number.isFinite(m) ? m : null;
}

function AccountTiles({ a, oversize }: { a: ManageAccount; oversize: number | null }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action)).length;
  const flagAt = a.riskBudget !== null && oversize !== null ? a.riskBudget * oversize : null;
  return (
    <TileGrid count={6}>
      <Tile label={`net liq · ${a.account ?? "?"}`} value={fmtMoney(a.netLiq)} />
      <Tile
        label="per-trade budget"
        value={fmtMoney(a.riskBudget)}
        title="risk.max_risk_pct of net liq: the unit a trade's modelled downside from here is measured in"
      />
      <Tile
        label={oversize === null ? "review over" : `review over (${oversize}×)`}
        value={fmtMoney(flagAt)}
        title="risk.oversize: a trade is flagged REVIEW when its modelled downside from today's marks exceeds this many budgets"
      />
      <Tile label="mark P&L (gross)" value={fmtMoney(a.pnlGross)} tone={a.pnlGross === null ? "dim" : a.pnlGross >= 0 ? "pos" : "neg"} />
      <Tile label="action needed" value={`${acting} of ${a.trades.length}`} />
      <Tile label="holding" value={String(a.trades.length - acting)} tone="dim" />
    </TileGrid>
  );
}

/** Positions: what to do with each trade today, the actions first. */
function PositionsAccount({ a, loading, oversize }: { a: ManageAccount; loading: boolean; oversize: number | null }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action));
  const holding = a.trades.filter((t) => PASSIVE.has(t.action));
  return (
    <>
      <AccountTiles a={a} oversize={oversize} />
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
function PortfolioAccount({ a, oversize }: { a: ManageAccount; oversize: number | null }) {
  return (
    <>
      <AccountTiles a={a} oversize={oversize} />
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
export function TradesCard({
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
    action: (t) => ACTION_PRIORITY[t.action] ?? 9,
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
            <td>{t.earnings ? `${t.earnings.date}${daysText(t.earnings.days)}` : "—"}</td>
            {variant === "act" ? (
              <td style={{ whiteSpace: "normal", minWidth: "18rem" }}>
                {t.reason}
                {t.conflicts.length > 0 ? <span className="muted"> · conflicts: {t.conflicts.join(", ")}</span> : null}
              </td>
            ) : (
              <>
                {heldForCall(t) ? (
                  <>
                    <td className="muted" style={RIGHT} title={HELD_TITLE}>
                      waits for call
                    </td>
                    <td className="muted" style={RIGHT} title={HELD_TITLE}>
                      waits for call
                    </td>
                  </>
                ) : (
                  <>
                    <td style={RIGHT}>{net(t.takeProfit?.netPrice ?? null, t.takeProfit?.netSide ?? null)}</td>
                    <td style={RIGHT}>{net(t.stop?.netPrice ?? null, t.stop?.netSide ?? null)}</td>
                  </>
                )}
                <td title={t.earningsTrade ? HELD_TITLE : undefined}>
                  {t.timeExit ?? "—"}
                  {t.earningsTrade ? <span className="muted"> · after call</span> : null}
                </td>
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

/** Live → wheel (2026-10-20): each wheel cycle on the account, from the same evaluation. */
export function WheelPage() {
  return <EvaluationPage view="wheel" />;
}

const VIEW_TITLE = { positions: "position evaluation", portfolio: "portfolio", wheel: "wheel" } as const;

/**
 * The three tabs read one evaluation file (2026-10-18: split from the single `manage` tab, whose dozen
 * account cards had buried the action list; 2026-10-20: the wheel). Each keeps the evaluation header,
 * because its caveats -- missing order chains, missing earnings dates, an unapplied rule -- qualify all.
 */
function EvaluationPage({ view }: { view: "positions" | "portfolio" | "wheel" }) {
  const title = VIEW_TITLE[view];
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
          <PositionsAccount key={a.account ?? "?"} a={a} loading={isLoading} oversize={oversizeMultiple(data?.rules)} />
        ) : view === "portfolio" ? (
          <PortfolioAccount key={a.account ?? "?"} a={a} oversize={oversizeMultiple(data?.rules)} />
        ) : (
          <WheelCards key={a.account ?? "?"} a={a} />
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
