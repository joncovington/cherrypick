import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { ManageChainOrder, ManageTrade } from "@console/shared";
import { fmtCash, fmtMoney } from "../../lib/format";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { ACTION_LABEL, ActionChip, Kv, Money, tone } from "./manageParts";
import { useLiveMarks } from "./LiveMarks";

/**
 * Live → positions as cards (2026-11-04, the user's pick "A" off the mock-up canvas): one card per
 * trade with its broker order chain down the card, open → rolls → "now, close at mid", as the
 * broker's own client draws it. A card opens the shared lightbox: the whole chain with every leg,
 * and the evaluation's detail (verdict, levels, closing order, legs).
 *
 * Every figure is packages/manage's: the chain's orders, their signed cash, the close at mid and the
 * P&L. The running total under an order is the sum of the chain's own signed cash to that order,
 * nothing re-priced. A trade with no reconciled chain shows its opening alone and says so. A losing
 * trade is red throughout (its P&L, its bar and its card's edge); the order is the reader's choice,
 * P&L high to low or low to high, and a trade with no net sorts last either way.
 */

export type PnlOrder = "desc" | "asc";
type Filter = "all" | "act" | "rolled";
type Tab = "chain" | "evaluation";

const PASSIVE = new Set(["HOLD", "SETTLES"]);

/** P&L net, high to low or low to high; a trade whose net is unknown goes last in both. */
export function sortByPnl(trades: ManageTrade[], order: PnlOrder): ManageTrade[] {
  return [...trades].sort((a, b) => {
    if (a.pnlNet === null && b.pnlNet === null) return a.tradeId.localeCompare(b.tradeId);
    if (a.pnlNet === null) return 1;
    if (b.pnlNet === null) return -1;
    return order === "desc" ? b.pnlNet - a.pnlNet : a.pnlNet - b.pnlNet;
  });
}

export interface ChainStep {
  date: string | null;
  role: string;
  price: string;
  cash: number | null;
  fees: number | null;
  /** The chain's signed cash summed to this order; null once any earlier order's cash is unknown. */
  total: number | null;
  legs: string[];
}

function priceText(price: number | null, side: "cr" | "db" | null): string {
  return price === null || side === null ? "—" : `${price.toFixed(2)} ${side}`;
}

/** The steps a card draws: the broker's chain when one was reconciled, else the opening alone. */
export function chainSteps(t: ManageTrade): ChainStep[] {
  const orders: ManageChainOrder[] = t.chain?.orders ?? [];
  if (orders.length > 0) {
    let total: number | null = 0;
    return orders.map((o, i) => {
      total = total === null || o.net === null ? null : total + o.net;
      return {
        date: o.date,
        role: i === 0 ? "open" : o.role,
        price: priceText(o.netPrice, o.netSide),
        cash: o.net,
        fees: o.fees,
        total,
        legs: o.legs,
      };
    });
  }
  if (t.opening) {
    return [{ date: null, role: "open", price: priceText(t.opening.netPrice, t.opening.netSide), cash: t.opening.net, fees: null, total: t.opening.net, legs: [] }];
  }
  return [];
}

function pct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
}

/** What capture means on this trade: a credit's share kept, a debit's return. */
function captureText(t: ManageTrade): string | null {
  if (t.capture === null) return null;
  return t.kind === "debit" ? `${pct(t.capture)} on the debit` : `${pct(t.capture)} of the credit kept`;
}

function dteText(t: ManageTrade): string | null {
  if (t.nearestShortDte === null) return null;
  return `${t.nearestShortDte} DTE${t.nearestShortExpiry ? ` · ${t.nearestShortExpiry}` : ""}`;
}

function rolls(t: ManageTrade): number {
  return t.chain?.rolls ?? 0;
}

function Timeline({ t, full }: { t: ManageTrade; full: boolean }) {
  const steps = chainSteps(t);
  return (
    <ol className="pos-chain" aria-label="order chain">
      {steps.map((s, i) => (
        <li key={i} className={`pos-step pos-step-${s.role}`}>
          <span className="pos-dot" aria-hidden="true" />
          <div className="pos-step-body">
            <div className="pos-step-head">
              <span className="pos-step-date">{s.date ?? "opened"}</span>
              <span className="pos-role">{s.role}</span>
              <span className="pos-step-price">{s.price}</span>
              {full ? <span className={`pos-step-cash ${tone(s.cash)}`}>{fmtCash(s.cash)}</span> : null}
            </div>
            {s.legs.length > 0 ? (
              full ? (
                <ul className="pos-legs">
                  {s.legs.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              ) : (
                <span className="pos-step-legs">{s.legs.join(" · ")}</span>
              )
            ) : null}
            {full ? (
              <span className="pos-step-foot">
                fees {s.fees === null ? "—" : fmtMoney(Math.abs(s.fees))} · running total <span className={tone(s.total)}>{fmtCash(s.total)}</span>
              </span>
            ) : null}
          </div>
        </li>
      ))}
      <li className="pos-step pos-step-now">
        <span className="pos-dot" aria-hidden="true" />
        <div className="pos-step-body">
          <div className="pos-step-now-line">
            <span>
              now · close at mid <span className={`pos-num ${tone(t.value)}`}>{fmtCash(t.value)}</span>
            </span>
            {t.chain ? (
              <span>
                {t.chain.cash !== null && t.chain.cash < 0 ? "paid in" : "taken in"}{" "}
                <span className={`pos-num ${tone(t.chain.cash)}`}>{fmtCash(t.chain.cash)}</span>
              </span>
            ) : null}
          </div>
        </div>
      </li>
      {t.basis === "open_legs" ? <li className="pos-chain-note">no reconciled chain: from the open legs, so earlier rolls are not counted</li> : null}
    </ol>
  );
}

function PnlFigure({ t }: { t: ManageTrade }) {
  return (
    <span
      className={`pos-card-pnl ${tone(t.pnlNet)}`}
      title={t.pnlNet === null ? `fees not read, so no net; gross ${fmtMoney(t.pnlGross)}` : `net of fees · gross ${fmtMoney(t.pnlGross)}`}
    >
      {fmtMoney(t.pnlNet)}
    </span>
  );
}

export function PositionCard({ t, onOpen }: { t: ManageTrade; onOpen: () => void }) {
  const live = useLiveMarks()?.trades.get(t.tradeId);
  const livePnl = live ? (live.pnlNet ?? live.pnlGross) : null;
  const losing = t.pnlNet !== null && t.pnlNet < 0;
  const bar = t.capture === null ? 0 : Math.min(100, Math.abs(t.capture) * 100);
  const n = rolls(t);
  const fees = t.fees === null ? null : Math.abs(t.fees);
  return (
    <button type="button" className={losing ? "pos-card is-neg" : "pos-card"} onClick={onOpen} aria-label={`${t.tradeId}: ${ACTION_LABEL[t.action] ?? t.action}, P&L ${fmtMoney(t.pnlNet)}`}>
      <span className="pos-card-head">
        <span className="pos-card-titles">
          <span className="pos-card-sym">{t.tradeId}</span>
          <span className="pos-card-shape">{[t.shape, t.intent].filter(Boolean).join(" · ") || "—"}</span>
        </span>
        <span className="pos-card-chips">
          {t.earningsTrade ? <span className={`chip ${t.earningsTrade.passed ? "chip-bad" : "chip-warn"}`}>earnings</span> : null}
          <ActionChip action={t.action} />
        </span>
      </span>
      <span className="pos-card-figs">
        <PnlFigure t={t} />
        <span className="pos-card-side">
          {captureText(t) ?? "—"}
          <br />
          {dteText(t) ?? ""}
        </span>
      </span>
      <span className="pos-bar" aria-hidden="true">
        <span className={t.capture !== null && t.capture < 0 ? "pos-bar-fill is-neg" : "pos-bar-fill"} style={{ width: `${bar}%` }} />
      </span>
      <span className="pos-chain-label">
        order chain · {n === 0 ? "no rolls" : `${n} roll${n === 1 ? "" : "s"}`}
        {livePnl !== null ? (
          <span>
            {" "}
            · live <span className={tone(livePnl)}>{fmtMoney(livePnl)}</span>
          </span>
        ) : null}
      </span>
      <Timeline t={t} full={false} />
      <span className="pos-card-foot">
        <span>fees {fmtMoney(fees)}</span>
        <span className="pos-card-reason">{t.reason}</span>
      </span>
    </button>
  );
}

function ChainTab({ t }: { t: ManageTrade }) {
  const fees = t.fees === null ? null : Math.abs(t.fees);
  return (
    <div className="pos-open">
      <Timeline t={t} full />
      <aside className="pos-open-side">
        <div className="lbl">P&amp;L, whole chain</div>
        <PnlFigure t={t} />
        <Kv
          rows={[
            [t.chain && t.chain.cash !== null && t.chain.cash < 0 ? "net paid in" : "net taken in", <Money key="c" v={t.chain?.cash ?? t.opening?.net ?? null} />],
            ["close at mid", <Money key="m" v={t.value} />],
            ["P&L gross", <Money key="g" v={t.pnlGross} />],
            ["fees paid", fmtMoney(fees)],
            ["P&L net of fees", <Money key="n" v={t.pnlNet} />],
          ]}
        />
      </aside>
    </div>
  );
}

function TradeDialog({
  trades,
  index,
  detail,
  onStep,
  onClose,
}: {
  trades: ManageTrade[];
  index: number;
  detail: (t: ManageTrade) => ReactNode;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("chain");
  const t = trades[index];
  if (t === undefined) return null;
  const tabs: LightboxTab<Tab>[] = [
    { id: "chain", label: "order chain", badge: rolls(t) > 0 ? `${rolls(t)} roll${rolls(t) === 1 ? "" : "s"}` : null },
    { id: "evaluation", label: "evaluation" },
  ];
  return (
    <CardLightbox count={trades.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={[t.shape, t.kind, t.confidence !== null ? `confidence ${t.confidence}` : null].filter(Boolean).join(" · ")}
          title={t.tradeId}
          chips={
            <>
              <ActionChip action={t.action} />
              <span className={`chip ${tone(t.pnlNet)}`}>{fmtMoney(t.pnlNet)}</span>
            </>
          }
          tabs={tabs}
          tab={tab}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {tab === "chain" ? <ChainTab t={t} /> : detail(t)}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The board: filter and sort chips, then a card per trade. `detail` renders the evaluation tab. */
export function PositionCards({ trades, loading, detail }: { trades: ManageTrade[]; loading: boolean; detail: (t: ManageTrade) => ReactNode }) {
  const [params, setParams] = useSearchParams();
  const lightbox = useOpenParam("trade");
  const order: PnlOrder = params.get("pnl") === "asc" ? "asc" : "desc";
  const raw = params.get("show");
  const filter: Filter = raw === "act" || raw === "rolled" ? raw : "all";
  const setParam = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };
  const acting = trades.filter((t) => !PASSIVE.has(t.action));
  const rolled = trades.filter((t) => rolls(t) > 0);
  const shown = sortByPnl(filter === "act" ? acting : filter === "rolled" ? rolled : trades, order);
  // A trade named in the URL but filtered out still opens, stepping through every trade.
  const stepTrades = shown.some((t) => t.tradeId === lightbox.open) ? shown : sortByPnl(trades, order);
  const index = stepTrades.findIndex((t) => t.tradeId === lightbox.open);
  const chip = (on: boolean) => (on ? "chip chip-ok" : "chip");
  if (loading && trades.length === 0) return <p className="muted">Loading the evaluation…</p>;
  if (trades.length === 0) return <p className="muted">No trades in this account.</p>;
  return (
    <section className="pos-board">
      <div className="pos-controls">
        <div className="history-controls" role="group" aria-label="Show">
          <button type="button" className={chip(filter === "all")} aria-pressed={filter === "all"} onClick={() => setParam("show", null)}>
            all {trades.length}
          </button>
          <button type="button" className={chip(filter === "act")} aria-pressed={filter === "act"} onClick={() => setParam("show", "act")}>
            needs action {acting.length}
          </button>
          <button type="button" className={chip(filter === "rolled")} aria-pressed={filter === "rolled"} onClick={() => setParam("show", "rolled")}>
            rolled {rolled.length}
          </button>
        </div>
        <div className="history-controls" role="group" aria-label="Sort by P&L">
          <span className="muted">sort by P&amp;L</span>
          <button type="button" className={chip(order === "desc")} aria-pressed={order === "desc"} onClick={() => setParam("pnl", null)}>
            high → low
          </button>
          <button type="button" className={chip(order === "asc")} aria-pressed={order === "asc"} onClick={() => setParam("pnl", "asc")}>
            low → high
          </button>
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="muted">{filter === "act" ? "Nothing to do today." : "No rolled trades."}</p>
      ) : (
        <div className="pos-cards">
          {shown.map((t) => (
            <PositionCard key={t.tradeId} t={t} onOpen={() => lightbox.show(t.tradeId)} />
          ))}
        </div>
      )}
      {index >= 0 ? (
        <TradeDialog trades={stepTrades} index={index} detail={detail} onStep={(to) => lightbox.step(stepTrades[to]?.tradeId ?? "")} onClose={lightbox.close} />
      ) : null}
    </section>
  );
}
