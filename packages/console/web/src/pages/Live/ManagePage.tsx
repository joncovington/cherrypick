import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ManageAccount, ManageLevel, ManagePayload, ManageTrade } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Cells, HeadRow, useColumns, type Column } from "../../components/columns";
import { TileGrid, tileCount } from "../../components/performance/TileGrid";
import { useOverview } from "../../lib/api";
import { parseSuiteTs } from "../../lib/etTime";
import { fmtCash, fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { ActionChip, DetailCard, Kv, Money, Upside, VerdictsHeld, ageText, etStamp, tone } from "./manageParts";
import { BandsHistoryCard } from "./BandsHistory";
import { LimitsCard } from "./LimitsCard";
import { PortfolioCards } from "./PortfolioCards";
import { PremiumPaceCard } from "./PremiumPace";
import { ScenarioCards } from "./ScenarioCards";
import { PmccCards } from "./PmccCards";
import { WheelCards } from "./WheelCards";
import { PositionCards } from "./PositionCards";
import {
  LiveGreeksCard,
  LiveMarksContext,
  LiveStatus,
  liveMarks,
  useManageLive,
} from "./LiveMarks";

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
    // Manage re-evaluates every 30 minutes, 10:00-16:35 ET; a minute's poll sees each run and keeps
    // the snapshot's age current, and polling harder would only re-read the same file.
    refetchInterval: 60_000,
  });
}

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
export const ACTION_PRIORITY: Record<string, number> = {
  ROLL_FOR_CREDIT: 0,
  CLOSE: 1,
  TAKE_PROFIT: 2,
  ROLL_OR_CLOSE: 3,
  CLOSE_LEGS: 4,
  REVIEW: 5,
  SETTLES: 6,
  HOLD: 7,
};

/**
 * The expanded trade: the plain-English verdict as a lead, then the facts behind it as a row of
 * cards (opened, exit levels, the closing order, what the model says it can still do, the rules, and
 * an earnings trade's report where it is one), then the legs. Every figure is packages/manage's;
 * this only lays them out.
 */
export function TradeDetail({ t }: { t: ManageTrade }) {
  const et = t.earningsTrade;
  const held = heldForCall(t);
  // Entry + close at mid = gross; gross − fees = net. Fees are a cost, shown positive and subtracted once.
  const fees = t.fees === null ? null : Math.abs(t.fees);
  const cashLabel = (v: number | null) => (v !== null && v < 0 ? "net paid in" : "net taken in");
  const legCols = useColumns(LEG_COLUMNS);
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
                ["left to win", <Upside key="w" v={t.profile.upsideFromNow} />],
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
          <HeadRow cols={legCols} />
        </thead>
        <tbody>
          {t.legs.map((l, i) => (
            <tr key={i}>
              <Cells cols={legCols} row={l} />
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

type Leg = ManageTrade["legs"][number];

/** An expanded trade's legs: strike, time, mid and delta are what a roll or close turns on; the
 *  open price, each leg's capture and its IV are the model's working, in the detail view. */
const LEG_COLUMNS: Column<Leg>[] = [
  { key: "leg", label: "leg", td: (l) => <td>{l.label}</td> },
  { key: "dte", label: "dte", td: (l) => <td>{l.dte ?? "—"}</td> },
  { key: "open", label: "open", detailOnly: true, td: (l) => <td>{px(l.openPrice)}</td> },
  { key: "mid", label: "mid", td: (l) => <td>{px(l.mid)}</td> },
  { key: "capture", label: "capture / return", detailOnly: true, td: (l) => <td className={tone(l.capture)}>{pct(l.capture)}</td> },
  { key: "delta", label: "delta", td: (l) => <td>{l.delta === null ? "—" : l.delta.toFixed(2)}</td> },
  {
    key: "iv",
    label: "iv",
    detailOnly: true,
    td: (l) => (
      <td>
        <IvCell l={l} />
      </td>
    ),
  },
];

const IV_TITLE = {
  broker:
    "the broker's IV: this leg's quote is wide, so an IV solved from its mid would be the midpoint of a market nobody quoted seriously; the broker's is used because it reprices the leg inside its quote",
  wide: "a wide quote, solved from its own mid anyway: the broker had no IV for it, or none that reprices it inside the quote",
  unpriceable:
    "no volatility prices this mid under the model, which carries no dividend (the mid sits under the model's least possible value), so the leg is left unmodelled: no delta, theta or stress figure is taken from it",
} as const;

/** A leg's IV with where it came from (packages/manage `_legs`): a broker IV beside a mid IV is
 *  marked, and a leg no IV prices says so rather than showing a blank. */
export function IvCell({ l }: { l: ManageTrade["legs"][number] }) {
  if (l.ivSource === "unpriceable") {
    return (
      <span className="muted" title={IV_TITLE.unpriceable}>
        — unpriceable
      </span>
    );
  }
  if (l.iv === null) return <>—</>;
  const v = `${(l.iv * 100).toFixed(0)}%`;
  if (l.ivSource === "broker") {
    return (
      <span title={IV_TITLE.broker}>
        {v} <span className="muted">broker</span>
      </span>
    );
  }
  if (l.wide === true) {
    return (
      <span title={IV_TITLE.wide}>
        {v} <span className="muted">wide</span>
      </span>
    );
  }
  return <>{v}</>;
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

export function AccountTiles({ a, oversize }: { a: ManageAccount; oversize: number | null }) {
  const acting = a.trades.filter((t) => !PASSIVE.has(t.action)).length;
  const flagAt = a.riskBudget !== null && oversize !== null ? a.riskBudget * oversize : null;
  // The budget and its review line are the ruleset's working, in the detail view. No "holding" count:
  // it is the holding table's row count. The P&L stays gross because the evaluation writes no
  // account net (the tables' P&L is each trade's net); the label and hover say so.
  const tiles = useColumns([
    { key: "liq", node: <Tile key="liq" label={`net liq · ${a.account ?? "?"}`} value={fmtMoney(a.netLiq)} /> },
    {
      key: "budget",
      detailOnly: true,
      node: (
        <Tile
          key="budget"
          label="per-trade budget"
          value={fmtMoney(a.riskBudget)}
          title="risk.max_risk_pct of net liq: the unit a trade's modelled downside from here is measured in"
        />
      ),
    },
    {
      key: "review",
      detailOnly: true,
      node: (
        <Tile
          key="review"
          label={oversize === null ? "review over" : `review over (${oversize}×)`}
          value={fmtMoney(flagAt)}
          title="risk.oversize: a trade is flagged REVIEW when its modelled downside from today's marks exceeds this many budgets"
        />
      ),
    },
    {
      key: "pnl",
      node: (
        <Tile
          key="pnl"
          label="mark P&L (gross, before fees)"
          value={fmtMoney(a.pnlGross)}
          tone={a.pnlGross === null ? "dim" : a.pnlGross >= 0 ? "pos" : "neg"}
          title="every trade's mark P&L at mid, before fees: the evaluation writes no account-wide net. Each trade's net is in the tables."
        />
      ),
    },
    { key: "acting", node: <Tile key="acting" label="action needed" value={`${acting} of ${a.trades.length}`} /> },
  ]);
  return <TileGrid count={tileCount(tiles.length)}>{tiles.map((t) => t.node)}</TileGrid>;
}

/** Positions: a card per trade with its broker order chain (PositionCards), sorted by P&L. */
function PositionsAccount({ a, loading, oversize }: { a: ManageAccount; loading: boolean; oversize: number | null }) {
  return (
    <>
      <AccountTiles a={a} oversize={oversize} />
      <PositionCards trades={a.trades} loading={loading} detail={(t) => <TradeDetail t={t} />} />
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
      {a.limits ? <LimitsCard limits={a.limits} /> : null}
      <LiveGreeksCard a={a} />
      {a.portfolio ? (
        <PortfolioCards
          p={a.portfolio}
          scenarios={<ScenarioCards p={a.portfolio} />}
          history={<BandsHistoryCard account={a.account} />}
          pace={<PremiumPaceCard premium={a.premium} thetaBand={a.portfolio.bands?.theta ?? null} />}
        />
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          no portfolio reading for this account in this evaluation
        </p>
      )}
    </>
  );
}

/** An earnings date the position's nearest short expiry reaches: the one case the column matters. */
export function earningsInLife(t: ManageTrade): boolean {
  const days = t.earnings?.days ?? null;
  return days !== null && days >= 0 && (t.nearestShortDte === null || days <= t.nearestShortDte);
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

/** Live → pmcc (2026-10-20): each PMCC on the account, from the same evaluation. */
export function PmccPage() {
  return <EvaluationPage view="pmcc" />;
}

const VIEW_TITLE = { positions: "position evaluation", portfolio: "portfolio", wheel: "wheel", pmcc: "pmcc" } as const;

/** What each tab's money is, for the header: the tabs do not share one P&L figure. */
const VIEW_MONEY: Record<keyof typeof VIEW_TITLE, string> = {
  positions: "table P&L is net of fees (the header's mark P&L is gross)",
  portfolio: "mark P&L is gross; the stress and income figures are modelled",
  wheel: "a cycle's P&L net is after fees; its option and stock P&L are gross",
  pmcc: "P&L if assigned is shown gross and net of fees; the skate yield is net",
};

/** Manage snapshots every 30 minutes, 10:00-16:35 ET (`manage.also_at`); past this the newest is late. */
export const LATE_AFTER_MIN = 45;

/**
 * Minutes the newest snapshot has gone without a successor, when that is late: on a trading day
 * (the watchdog's word; unknown is not taken as a holiday), a weekday in ET, between 10:45 and 17:20
 * ET (the first run's slot plus the allowance, to the last run's plus the allowance), and more than
 * LATE_AFTER_MIN old. Null otherwise. Presentation only: nothing here asks for a run.
 */
export function snapshotLate(snapMs: number | null, nowMs: number, tradingDay: boolean | null): number | null {
  if (snapMs === null || tradingDay === false) return null;
  const wall = new Date(nowMs).toLocaleString("sv-SE", { timeZone: "America/New_York" });
  const [day, clock] = wall.split(" ");
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay();
  if (weekday === 0 || weekday === 6 || clock === undefined) return null;
  const [h, m] = clock.split(":").map(Number);
  const minute = (h ?? 0) * 60 + (m ?? 0);
  if (minute < 10 * 60 + LATE_AFTER_MIN || minute > 16 * 60 + 35 + LATE_AFTER_MIN) return null;
  const age = Math.floor((nowMs - snapMs) / 60_000);
  return age > LATE_AFTER_MIN ? age : null;
}

/**
 * The four tabs read one evaluation file (2026-10-18: split from the single `manage` tab, whose dozen
 * account cards had buried the action list; 2026-10-20: the wheel, then the PMCCs). Each keeps the
 * evaluation header, because its caveats -- missing order chains, missing earnings dates, an
 * unapplied rule -- qualify all.
 */
function EvaluationPage({ view }: { view: keyof typeof VIEW_TITLE }) {
  const title = VIEW_TITLE[view];
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useManage(session);
  const tradingDay = useOverview().data?.watchdog?.isTradingDay ?? null;
  const sessions = data?.sessions ?? [];
  const snapMs = parseSuiteTs(data?.snapshotGeneratedAt ?? null);
  const nowMs = Date.now();
  const newest = data?.session !== null && data?.session !== undefined && data.session === sessions[sessions.length - 1];
  const late = newest ? snapshotLate(snapMs, nowMs, tradingDay) : null;
  // Live marks only beside the newest evaluation, and only on the views that show marks or greeks.
  const wantLive = newest && (view === "positions" || view === "portfolio");
  const live = liveMarks(useManageLive(wantLive).data);
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
          No evaluation has been written yet. It runs every 30 minutes from 10:00 to 16:35 ET on trading days once{" "}
          <code>manage.enabled</code> is true in
          the orchestrator config (then re-run <code>install</code>), or by hand with <code>run.py manage</code>. The ruleset
          is <code>~/.cherrypick/config/manage.json</code>.
        </p>
      </Card>
    );
  }

  return (
    <LiveMarksContext.Provider value={wantLive && live?.payload.session === data?.session ? live : null}>
    <div className="view-fade" style={{ display: "grid", gap: "1rem" }}>
      {/* The card's "as of" is the snapshot's time, not the browser's last poll: the poll re-reads
          the same file every minute, and its clock said nothing about how old the marks were. */}
      <Card title={title} updatedAt={snapMs ?? undefined} isError={isError} controls={picker}>
        <p className="muted" style={{ margin: 0 }}>
          {data?.session ?? "…"} · snapshot {etStamp(data?.snapshotGeneratedAt ?? null) ?? data?.snapshotGeneratedAt ?? "—"}
          {snapMs !== null && newest ? `, ${ageText(nowMs - snapMs)} old` : ""} · ruleset {data?.ruleset ?? "—"} (
          {data?.rulesSource ?? "shipped default"}) · advice only: marks are mids, {VIEW_MONEY[view]}, and nothing here places
          an order
        </p>
        {late !== null && (
          <p
            className="pnl-neg"
            title="manage snapshots every 30 minutes, 10:00-16:35 ET; this only reads the file's time, and asks for no run"
          >
            the newest snapshot is {ageText(late * 60_000)} old: a 30-minute run looks to have been missed, so every mark,
            greek and verdict here is older than the schedule says
          </p>
        )}
        <LiveStatus />
        {data?.error && <p className="pnl-neg">evaluation error: {data.error}</p>}
        {data?.snapshotStale && (
          <p className="pnl-neg" title="the watchdog's manage.snapshot_version check; no broker read is made to fix it">
            snapshot older than the code: {data.snapshotStale}
          </p>
        )}
        {data?.preOpen && (
          <p className="pnl-neg" title="manage's pre_open: the snapshot was taken before 09:30 ET on its own session">
            taken {data.preOpen.takenAt ?? "—"} ET, before the open: spot and marks are the last session's close, so every delta,
            theta and stress figure reads off it
            {data.preOpen.expiringUnpriced.length
              ? ` · options expiring today have no quote and are left unpriced (${data.preOpen.expiringUnpriced.join(", ")})`
              : ""}{" "}
            · a run after 09:30 reprices
          </p>
        )}
        {data?.early && (
          <p className="pnl-neg" title="manage's early: the marks were taken in the open's first half hour">
            marks from {data.early.takenAt ?? "—"} ET, the opening half hour: prices, P&amp;L and needs-a-look stand; every
            verdict is held until {data.early.verdictsFrom ?? "10:00"} ET, when the quotes have settled
          </p>
        )}
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
      <VerdictsHeld.Provider value={data?.early ? (data.early.verdictsFrom ?? "10:00") : null}>
        {(data?.accounts ?? []).map((a) =>
          view === "positions" ? (
            <PositionsAccount key={a.account ?? "?"} a={a} loading={isLoading} oversize={oversizeMultiple(data?.rules)} />
          ) : view === "portfolio" ? (
            <PortfolioAccount key={a.account ?? "?"} a={a} oversize={oversizeMultiple(data?.rules)} />
          ) : view === "wheel" ? (
            <WheelCards key={a.account ?? "?"} a={a} />
          ) : (
            <PmccCards key={a.account ?? "?"} a={a} />
          ),
        )}
      </VerdictsHeld.Provider>
      {view === "positions" && (
        <Card title="ruleset" collapseKey="manage-ruleset" defaultCollapsed>
          <pre style={{ margin: 0, fontSize: "0.8rem", whiteSpace: "pre-wrap" }}>
            {data?.rules ? JSON.stringify(data.rules, null, 2) : "—"}
          </pre>
        </Card>
      )}
    </div>
    </LiveMarksContext.Provider>
  );
}
