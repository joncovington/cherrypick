import type { BwbCycleRow, BwbOpenPosition, LiveLoopId, LiveMeicOpenRow, LiveMeicPayload, MeicTradeRow } from "@console/shared";
import { useBwb, useBwbHistory } from "../../lib/api";
import { fmtCash, fmtMoney } from "../../lib/format";
import { tone } from "./manageParts";
import { useLiveMeic } from "./MeicLivePage";

/**
 * A live loop's orders today, as chains (2026-11-04, Live → today's loop lightbox): one chain per
 * position the loop held today, entry → what happened to it → now or how it ended, drawn with the
 * positions page's timeline (`pos-chain`). Built from each loop's own live read, nothing new:
 * MEIC's `/api/live/meic` (open rows and resolved history) and bwb's live payload and its history
 * for today's session. Every figure is the reader's: signed whole-position cash, credit +.
 */

export interface DayStep {
  at: string | null;
  /** open, working, add, close, settled, expired, closed, stopped: the step's dot and label. */
  role: string;
  price: string | null;
  cash: number | null;
  what: string;
}

export interface DayChain {
  id: string;
  title: string;
  sub: string;
  steps: DayStep[];
  /** Still held: the line under the last step says so. */
  open: boolean;
  /** Resolved: its net; open: the reader's mark where it has one. */
  net: number | null;
  netLabel: string;
}

function hhmm(ts: string | null): string | null {
  if (ts === null) return null;
  return ts.length >= 16 && ts[10] === "T" ? ts.slice(11, 16) : ts.length >= 16 && ts[10] === " " ? ts.slice(11, 16) : ts;
}

function perShare(v: number | null): string | null {
  return v === null ? null : `${Math.abs(v).toFixed(2)} ${v >= 0 ? "cr" : "db"}`;
}

function condor(put: number | null, call: number | null, wing: number | null, qty: number | null): string {
  const p = put === null ? "?P" : wing === null ? `${put}P` : `${put - wing}/${put}P`;
  const c = call === null ? "?C" : wing === null ? `${call}C` : `${call}/${call + wing}C`;
  return `${p} · ${c}${qty !== null && qty !== 1 ? ` ×${qty}` : ""}`;
}

function meicOpenChain(r: LiveMeicOpenRow): DayChain {
  const steps: DayStep[] = [
    {
      at: hhmm(r.entryTime),
      role: r.working ? "working" : "open",
      price: perShare(r.netCredit),
      cash: r.entryCash,
      what: `${r.working ? "entry working at the broker · " : ""}${condor(r.putStrike, r.callStrike, r.wingWidth, r.quantity)}`,
    },
  ];
  for (const side of ["put", "call"] as const) {
    const held = side === "put" ? r.putHeld : r.callHeld;
    const closing = side === "put" ? r.putClosing : r.callClosing;
    if (closing) steps.push({ at: null, role: "working", price: null, cash: null, what: `${side} side: close order working` });
    else if (!held && !r.working) steps.push({ at: null, role: "close", price: null, cash: null, what: `${side} side closed (stopped)` });
  }
  return {
    id: `meic-${r.id}`,
    title: `${r.symbol} iron condor`,
    sub: [r.arm, r.overdue ? `from ${r.tradeDate}, overdue` : null].filter(Boolean).join(" · "),
    steps,
    open: true,
    net: null,
    netLabel: r.working ? "entry not filled" : "held",
  };
}

function meicResolvedChain(r: MeicTradeRow): DayChain {
  const how = r.exitKind ?? r.status;
  return {
    id: `meic-${r.id}`,
    title: `${r.symbol} iron condor`,
    sub: r.exitReason ?? "",
    steps: [
      { at: hhmm(r.entryTime), role: "open", price: perShare(r.netCredit), cash: r.entryCash, what: condor(r.putStrike, r.callStrike, r.wingWidth, r.quantity) },
      { at: null, role: how, price: null, cash: r.exitCash, what: r.exitReason ?? how },
    ],
    open: false,
    net: r.net,
    netLabel: r.net === null ? "net n/r (fees not recorded)" : "net",
  };
}

/** MEIC's live day: every open row (an overdue one says so), then today's resolved rows. */
export function meicChains(p: LiveMeicPayload): DayChain[] {
  return [...p.open.map(meicOpenChain), ...p.history.filter((r) => r.tradeDate === p.session).map(meicResolvedChain)];
}

function fly(body: number | null, near: number | null, far: number | null, qty: number | null): string {
  return `${[far, body, near].map((k) => (k === null ? "?" : String(k))).join("/")}P broken wing${qty !== null && qty !== 1 ? ` ×${qty}` : ""}`;
}

function bwbAddOn(short: number | null, long: number | null, credit: number | null, cash: number | null, at: string | null): DayStep {
  return { at: hhmm(at), role: "add", price: perShare(credit), cash, what: `add-on ${short ?? "?"}/${long ?? "?"}P credit spread` };
}

function bwbOpenChain(p: BwbOpenPosition): DayChain {
  const steps: DayStep[] = [{ at: p.entrySession, role: "open", price: perShare(p.entryCredit), cash: p.entryCash, what: fly(p.bodyStrike, p.nearStrike, p.farStrike, p.quantity) }];
  if (p.addonFiredAt !== null) steps.push(bwbAddOn(p.addonShortStrike, p.addonLongStrike, p.addonCredit, p.addOnCash, p.addonFiredAt));
  return {
    id: `bwb-${p.positionId}`,
    title: `${p.symbol} ${p.arm}`,
    sub: p.expiration !== null ? `expires ${p.expiration}` : "",
    steps,
    open: true,
    net: p.unrealisedNet,
    netLabel: p.unrealisedNet === null ? "no usable mark yet" : "marked, net of fees so far",
  };
}

function bwbResolvedChain(r: BwbCycleRow): DayChain {
  const steps: DayStep[] = [{ at: r.entrySession, role: "open", price: perShare(r.entryCredit), cash: r.entryCash, what: fly(r.bodyStrike, r.nearStrike, r.farStrike, r.quantity) }];
  if (r.addonFiredAt !== null) steps.push(bwbAddOn(r.addonShortStrike, r.addonLongStrike, r.addonCredit, r.addOnCash, r.addonFiredAt));
  const how = r.exitKind ?? r.status;
  steps.push({ at: r.closedSession, role: how, price: null, cash: r.exitCash, what: r.exitReason ?? how });
  return { id: `bwb-${r.positionId}`, title: `${r.symbol} ${r.arm}`, sub: r.exitReason ?? "", steps, open: false, net: r.netPnl, netLabel: "net" };
}

/** bwb's live day: what it holds, then what it closed or settled today. */
export function bwbChains(open: BwbOpenPosition[], closed: BwbCycleRow[], session: string): DayChain[] {
  return [...open.map(bwbOpenChain), ...closed.filter((r) => r.closedSession === session).map(bwbResolvedChain)];
}

const DOT_ROLE: Record<string, string> = { open: "open", working: "add", add: "add", close: "close", stopped: "close", closed: "close", settled: "roll", expired: "roll" };

export function DayChains({ chains, empty }: { chains: DayChain[]; empty: string }) {
  if (chains.length === 0) return <p className="muted">{empty}</p>;
  return (
    <div className="lt-chains">
      {chains.map((c) => (
        <section key={c.id} className={c.net !== null && c.net < 0 ? "lt-chain is-neg" : "lt-chain"}>
          <header className="lt-chain-head">
            <span className="lt-chain-title">{c.title}</span>
            <span className={`lt-chain-net ${tone(c.net)}`}>{fmtMoney(c.net)}</span>
          </header>
          {c.sub ? <div className="lt-chain-sub">{c.sub}</div> : null}
          <ol className="pos-chain" aria-label={`${c.title} orders`}>
            {c.steps.map((s, i) => (
              <li key={i} className={`pos-step pos-step-${DOT_ROLE[s.role] ?? "open"}`}>
                <span className="pos-dot" aria-hidden="true" />
                <div className="pos-step-body">
                  <div className="pos-step-head">
                    {s.at !== null ? <span className="pos-step-date">{s.at}</span> : null}
                    <span className="pos-role">{s.role}</span>
                    {s.price !== null ? <span className="pos-step-price">{s.price}</span> : <span className="pos-step-price" />}
                    <span className={`pos-step-cash ${tone(s.cash)}`}>{s.cash === null ? "" : fmtCash(s.cash)}</span>
                  </div>
                  <span className="pos-step-legs">{s.what}</span>
                </div>
              </li>
            ))}
            <li className="pos-step pos-step-now">
              <span className="pos-dot" aria-hidden="true" />
              <div className="pos-step-body">
                <div className="pos-step-now-line">
                  <span>{c.open ? `now · ${c.netLabel}` : `ended · ${c.netLabel}`}</span>
                  <span className={`pos-num ${tone(c.net)}`}>{fmtMoney(c.net)}</span>
                </div>
              </div>
            </li>
          </ol>
        </section>
      ))}
    </div>
  );
}

function MeicOrders({ login = null }: { login?: string | null }) {
  const { data, isLoading, isError } = useLiveMeic(login);
  if (isError) return <p className="pnl-neg">MEIC's live ledger could not be read.</p>;
  if (isLoading || data === undefined) return <p className="muted">Reading MEIC's live ledger…</p>;
  if (data.ledger !== "ok") return <p className="muted">{data.ledger === "absent" ? "No live ledger on this machine." : `Read failed: ${data.ledgerError ?? "unknown"}`}</p>;
  return <DayChains chains={meicChains(data)} empty="Nothing open and nothing resolved today." />;
}

function BwbOrders({ session }: { session: string }) {
  const live = useBwb("live");
  const hist = useBwbHistory({ arm: null, symbol: null, from: session, to: session }, { limit: 100, offset: 0 }, "live");
  if (live.isError || hist.isError) return <p className="pnl-neg">bwb's live ledger could not be read.</p>;
  if (live.data === undefined || hist.data === undefined) return <p className="muted">Reading bwb's live ledger…</p>;
  return <DayChains chains={bwbChains(live.data.openPositions, hist.data.rows, session)} empty="Nothing open and nothing closed today." />;
}

/** The loop lightbox's orders tab. */
export function LoopOrders({ loop, session }: { loop: LiveLoopId; session: string }) {
  if (loop === "meic") return <MeicOrders />;
  // A MEIC live account on another broker login: `meic.<login>`.
  if (loop.startsWith("meic.")) return <MeicOrders login={loop.slice("meic.".length)} />;
  if (loop === "bwb") return <BwbOrders session={session} />;
  return <p className="muted">This loop's orders are on its own tab.</p>;
}
