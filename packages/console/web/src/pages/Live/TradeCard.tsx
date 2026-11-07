import type { ReactNode } from "react";
import type { ManageCandidate, ManageHedgeSuggestion, ManageHedges, TimeSpreadStructure } from "@console/shared";
import { fmtCash, fmtMoney } from "../../lib/format";
import { tone } from "./manageParts";

/**
 * A trade that is not held yet, drawn as the positions tab draws a held one (2026-11-06): symbol and
 * spot, shape, chips, the headline cash with its side note, a bar, the legs as the order that would
 * open it, the line saying what it does to the book, and a foot. It uses the position card's own
 * classes, so the two read alike. Every figure is manage's; the mappers below only place them.
 */

export interface TradeCardLeg {
  buy: boolean;
  text: string;
  quote: string | null;
}

export interface TradeCardModel {
  key: string;
  symbol: string;
  spot: string | null;
  shape: string;
  chips: ReactNode;
  /** Whole-position cash, signed: a credit is +, a debit −. */
  cash: number | null;
  side: string[];
  /** 0-100; the bar's share of the cap it is measured against. */
  bar: number | null;
  barTitle: string;
  label: string;
  open: { date: string; price: string };
  legs: TradeCardLeg[];
  after: string[];
  foot: [string, string];
  /** Shown for the reason in its foot, not offered: drawn faded. */
  faded?: boolean;
}

export function TradeCard({ m }: { m: TradeCardModel }) {
  return (
    <article className={`pos-card tc-card${m.faded ? " is-faded" : ""}`} aria-label={`${m.symbol}: ${m.shape}`}>
      <span className="pos-card-head">
        <span className="pos-card-titles">
          <span className="pos-card-sym-line">
            <span className="pos-card-sym">{m.symbol}</span>
            {m.spot ? <span className="pos-card-spot">{m.spot}</span> : null}
          </span>
          <span className="pos-card-shape">{m.shape}</span>
        </span>
        <span className="pos-card-chips">{m.chips}</span>
      </span>
      <span className="pos-card-figs">
        <span className={`pos-card-pnl ${tone(m.cash)}`}>{fmtCash(m.cash)}</span>
        <span className="pos-card-side">
          {m.side.map((s, i) => (
            <span key={i}>
              {i > 0 ? <br /> : null}
              {s}
            </span>
          ))}
        </span>
      </span>
      <span className="pos-bar" title={m.barTitle}>
        <span className="pos-bar-fill tc-bar-cap" style={{ width: `${Math.max(0, Math.min(100, m.bar ?? 0))}%` }} />
      </span>
      <span className="pos-chain-label">{m.label}</span>
      <ol className="pos-chain" aria-label="the order that would open it">
        <li className="pos-step pos-step-open">
          <span className="pos-dot" aria-hidden="true" />
          <div className="pos-step-body">
            <div className="pos-step-head">
              <span className="pos-step-date">{m.open.date}</span>
              <span className="pos-role">open</span>
              <span className="pos-step-price">{m.open.price}</span>
            </div>
            <ul className="pos-legs">
              {m.legs.map((l, j) => (
                <li key={j} className="pos-leg">
                  <span className={`pos-leg-act ${l.buy ? "is-buy" : "is-sell"}`}>{l.buy ? "buy" : "sell"}</span>
                  <span>
                    {l.text}
                    {l.quote ? <span className="muted"> {l.quote}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </li>
        <li className="pos-step pos-step-now">
          <span className="pos-dot" aria-hidden="true" />
          <div className="pos-step-body">
            <div className="pos-step-now-line">
              {m.after.map((a, i) => (
                <span key={i}>{a}</span>
              ))}
            </div>
          </div>
        </li>
      </ol>
      <span className="pos-card-foot">
        <span>{m.foot[0]}</span>
        <span className="pos-card-reason">{m.foot[1]}</span>
      </span>
    </article>
  );
}

export function TradeCards({ models, empty }: { models: TradeCardModel[]; empty?: string }) {
  if (models.length === 0) return empty ? <p className="muted">{empty}</p> : null;
  return (
    <div className="tc-grid">
      {models.map((m) => (
        <TradeCard key={m.key} m={m} />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- the mappers */

function signed(v: number | null, digits = 1): string {
  return v === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
}

function quote(bid: number | null, ask: number | null, delta: number | null): string {
  const q = bid === null && ask === null ? "" : `${bid ?? "—"} / ${ask ?? "—"}`;
  const d = delta === null ? "" : `Δ ${delta.toFixed(2)}`;
  return [q, d].filter(Boolean).join(" · ");
}

/** "12/18" from "2026-12-18": the date as an option symbol shows it. */
function mmdd(iso: string | null): string {
  return iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : "—";
}

/** "18 Dec" from "2026-12-18". */
export function dayMonth(iso: string | null): string {
  if (!iso) return "—";
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(iso.slice(8, 10))} ${names[Number(iso.slice(5, 7)) - 1] ?? ""}`;
}

function share(part: number | null, whole: number | null): number | null {
  return part === null || whole === null || whole === 0 ? null : (Math.abs(part) / whole) * 100;
}

const KIND: Record<string, string> = {
  short_put: "short put",
  put_spread: "put credit spread",
  call_spread: "call credit spread",
  covered_call: "covered call",
};

function pct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** An income candidate at the size manage fitted (`units`), against the per-trade cap in dollars. */
export function candidateModel(
  c: ManageCandidate,
  capDollars: number | null,
  chips: ReactNode,
  /** The per-share net price as the income card states it (`netPrice`), and what a narrowed row is. */
  said: { price: string; narrowed: string | null; units?: number; binding?: string | null; waiting?: boolean },
): TradeCardModel {
  // A waiting trade is drawn at the size it takes once the book has room (`ifRoom`).
  const n = said.units ?? c.units;
  const binding = said.units === undefined ? c.binding : (said.binding ?? null);
  const cash = c.perUnit.cash === null ? null : c.perUnit.cash * n;
  // The loss the cap was applied to: a spread's max loss, a short put's 1st-percentile stress loss.
  const risk = c.kind === "covered_call" ? null : c.perUnit.stressLoss === null ? null : Math.abs(c.perUnit.stressLoss) * n;
  const price = said.price;
  const o = c.odds;
  return {
    key: `${c.underlying}-${c.kind}-${c.expiry ?? ""}-${c.legs.map((l) => l.strike).join("/")}`,
    symbol: c.underlying,
    spot: null,
    shape: `${KIND[c.kind] ?? c.kind.replace(/_/g, " ")} · income${said.narrowed ? ` · ${said.narrowed}` : ""}${said.waiting ? " · waiting for room" : ""}`,
    chips,
    cash,
    side: [`${price} at mid${n > 1 ? ` × ${n}` : ""}`, `${c.dte ?? "—"} days · ${dayMonth(c.expiry)}`],
    bar: share(risk, capDollars),
    barTitle: "the loss the per-trade cap is applied to, against that cap",
    label:
      c.kind === "covered_call"
        ? "covered by shares held"
        : `${c.kind === "short_put" ? "1st-percentile loss" : "max loss"} ${fmtMoney(risk)}${capDollars === null ? "" : ` of a ${fmtMoney(capDollars)} cap`}`,
    open: { date: dayMonth(c.expiry), price },
    legs: c.legs.map((l) => ({
      buy: l.quantity > 0,
      text: `${Math.abs(l.quantity) * n} × ${c.underlying} ${mmdd(c.expiry)} ${l.strike}${l.right}`,
      quote: quote(l.bid, l.ask, l.delta),
    })),
    after: [
      `delta ${signed(c.perUnit.bwd === null ? null : c.perUnit.bwd * n)} SPY sh`,
      `theta ${fmtCash(c.perUnit.theta === null ? null : c.perUnit.theta * n)} per day`,
    ],
    foot: [
      `next stops at: ${binding ?? "max contracts"}`,
      // A spread's odds: the chance of any loss / of the max loss, its break-even, and what getting in
      // costs as a share of the credit. A known earnings date is after expiry (or the trade was dropped),
      // so only an unknown one is said.
      o
        ? `odds ${pct(o.pLoss)} / ${pct(o.pMaxLoss)} · b/e ${o.breakEven ?? "—"} · cost ${pct(o.entryCostShare)}`
        : c.earningsUnknown
          ? "earnings date unknown: check before sending"
          : "re-quote before sending",
    ],
  };
}

/** A calendar or diagonal at manage's size. The debit is per share; theta, the profits and the max
 *  loss are whole-position dollars for one unit (TimeSpreadTable), so each is times the units. */
export function timeSpreadModel(s: TimeSpreadStructure, capDollars: number | null): TradeCardModel {
  const n = s.units ?? 1;
  const debit = s.debitMid === null ? null : -s.debitMid * 100 * n;
  const [longQ, shortQ] = s.entryQuotes ?? [
    [null, null],
    [null, null],
  ];
  const leg = (l: TimeSpreadStructure["longLeg"], exp: string) => (l ? `${n} × ${s.underlying} ${mmdd(exp)} ${l.strike ?? "—"}${l.right ?? ""}` : "—");
  const maxLoss = s.maxLoss === null ? null : Math.abs(s.maxLoss) * n;
  return {
    key: `${s.underlying}-${s.kind}-${s.front}-${s.back}-${s.label}`,
    symbol: s.underlying,
    spot: s.spot === null ? null : s.spot.toFixed(2),
    shape: `${s.label || s.kind} · ${s.direction}`,
    chips: (
      <>
        {s.units !== null && s.units > 0 ? <span className="chip chip-ok">fits ×{s.units}</span> : <span className="chip chip-warn">too big</span>}
        {s.heldName ? <span className="chip">held name</span> : null}
      </>
    ),
    cash: debit,
    side: [`${s.debitMid === null ? "—" : s.debitMid.toFixed(2)} db at mid${n > 1 ? ` × ${n}` : ""}`, `${dayMonth(s.front)} / ${dayMonth(s.back)}`],
    bar: share(maxLoss, capDollars),
    barTitle: "the debit at risk, against the per-trade cap",
    label: `max loss ${fmtMoney(maxLoss)}${capDollars === null ? "" : ` of a ${fmtMoney(capDollars)} cap`}${s.binding ? ` · next stops at ${s.binding}` : ""}`,
    open: { date: `${dayMonth(s.front)} / ${dayMonth(s.back)}`, price: s.debitMid === null ? "—" : `${s.debitMid.toFixed(2)} db` },
    legs: [
      { buy: false, text: leg(s.shortLeg, s.front), quote: quote(shortQ[0], shortQ[1], null) },
      { buy: true, text: leg(s.longLeg, s.back), quote: quote(longQ[0], longQ[1], null) },
    ],
    after: [
      `theta ${fmtCash(s.thetaDay === null ? null : s.thetaDay * n)} per day`,
      `flat at the front's expiry ${fmtCash(s.pnlFlat === null ? null : s.pnlFlat * n)}`,
    ],
    foot: [
      `front IV ${pct(s.frontIv)} · back ${pct(s.backIv)}${s.bpAfterPct !== null ? ` · buying power after ${pct(s.bpAfterPct)}` : ""}`,
      s.earningsDate ? `earnings ${s.earningsDate}` : s.rollBy ? `roll by ${s.rollBy}` : "no earnings date inside its life",
    ],
    faded: !(s.units !== null && s.units > 0),
  };
}

/** One sized hedge; the bar is the share of the needed delta it covers. */
export function hedgeModel(s: ManageHedgeSuggestion, h: ManageHedges): TradeCardModel {
  const a = s.after;
  const covered = s.bwdChange !== null && h.need ? (s.bwdChange / h.need) * 100 : null;
  return {
    key: s.kind,
    symbol: h.root ?? "—",
    spot: null,
    shape: `${s.structure ?? s.kind} · hedge`,
    chips: (
      <>
        <span className="chip">hedge</span>
        {a?.inBand === true ? <span className="chip chip-ok">in band after</span> : null}
        {a?.inBand === false ? <span className="chip chip-warn">still outside</span> : null}
        {s.pastCap ? <span className="chip chip-warn">past the cap</span> : null}
      </>
    ),
    cash: s.cash,
    side: [
      `${s.priceLabel ?? "—"} at mid${s.units && s.units > 1 ? ` × ${s.units}` : ""}`,
      `natural ${s.natural === null ? "—" : `${Math.abs(s.natural).toFixed(2)} ${s.natural >= 0 ? "cr" : "db"}`} · ${h.dte ?? "—"} days`,
    ],
    bar: covered,
    barTitle: "the share of the delta it needs that this hedge covers",
    label: `${s.units ?? "—"} unit${s.units === 1 ? "" : "s"} · covers ${signed(s.bwdChange)} of the ${signed(h.need)} sh needed · max loss ${fmtMoney(s.maxLoss)}`,
    open: { date: dayMonth(h.expiration), price: s.priceLabel ?? "—" },
    legs: s.legs.map((l) => ({ buy: l.quantity > 0, text: `${Math.abs(l.quantity)} × ${l.label}`, quote: quote(l.bid, l.ask, l.delta) })),
    after: a
      ? [`after: delta ${signed(a.bwd)} SPY sh`, `theta ${fmtMoney(a.theta)} per day`, `buying power ${pct(a.buyingPowerUsedPct)}`]
      : ["no after-figures"],
    foot: [
      `break-even after ${pct(a?.breakevenPct ?? null, 2)}`,
      s.pastCap ? "past the buying-power cap: offered anyway, because a debit spread only lowers the book's worst case" : "nothing is placed",
    ],
  };
}
