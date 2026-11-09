import { useState, type ReactNode } from "react";
import type {
  ManageAccount,
  ManageAttention,
  ManageTrade,
  ManageWheelCheck,
  ManageWheelCycle,
  ManageWheelNote,
  ManageWheelRoll,
  ManageWheelRolls,
} from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { Tile } from "../Flies/PerformanceTab";
import { Cells, HeadRow, useColumns, type Column } from "../../components/columns";
import { fmtCash, fmtMoney, fmtPrice } from "../../lib/format";
import { ExpiringCard, NearStrikeCard, distanceWords } from "./AttentionCard";
import { PayoffStrip } from "./PayoffCard";
import { LedgerDesk } from "./LedgerDesk";
import { Section } from "../../components/Section";
import { BoxButton, BoxButtons, PortfolioBoxContext } from "./portfolioBoxContext";
import {
  ActionChip,
  DetailCard,
  Kv,
  Money,
  StatusChip,
  TestedChip,
  VERDICT_CHIP,
  bandText,
  beyondText,
  etStamp,
  feesPaid,
  px,
  share,
  strike,
  tone,
  yesNo,
} from "./manageParts";
import { GuideLink } from "../../components/GuideLink";

/**
 * Live → wheel: each wheel cycle packages/manage follows on the account (`wheel.py`),
 * one card per cycle, and the wheel rules that fired on the trades holding its legs.
 *
 * It computes nothing. Status, the bases, the breakeven, every P&L figure and the skate yield are
 * manage's; "tested", "at or under the buy-to-close" and "beyond the band" are manage's flags, read and
 * never worked out here from a price and a strike. Money follows the suite's layout: cash flows are
 * signed whole-position dollars (+ received), fees a cost shown once, and the per-share figures (the
 * bases, the breakeven, option prices) say "a share".
 */

const UNKNOWN_BASIS = "unknown, shares predate the history";

/* ------------------------------------------------------------------ the cycle's facts */

function CycleFacts({ c }: { c: ManageWheelCycle }) {
  const days =
    c.days === null ? "—" : c.startKnown === false ? `at least ${c.days} (shares predate the history)` : String(c.days);
  return (
    <DetailCard title="cycle" aside={<StatusChip status={c.status} />}>
      <Kv
        rows={[
          ["started", c.start ?? "—"],
          ["ended", c.end ?? "running"],
          ["days in cycle", days],
          ["shares held", c.shares === null ? "—" : String(c.shares)],
          ["assignments", c.assignments === null ? "—" : String(c.assignments)],
          ["called away", c.calledAway === null ? "—" : String(c.calledAway)],
          ["open options", c.openOptions.length ? c.openOptions.map((o) => `${o.quantity ?? "?"} ${o.leg}`).join(" · ") : "none"],
          ["trades", c.tradeIds.length ? c.tradeIds.join(", ") : "none open"],
        ]}
      />
    </DetailCard>
  );
}

function CycleMoney({ c }: { c: ManageWheelCycle }) {
  const known = c.basisKnown !== false;
  return (
    <DetailCard title="money">
      <Kv
        rows={[
          ["credits", fmtCash(c.credits)],
          ["debits", fmtCash(c.debits)],
          ["net cash", fmtCash(c.netCash)],
          ["of which net premium", fmtCash(c.netPremium)],
          ["of which dividends", fmtCash(c.dividends)],
          ["fees paid", feesPaid(c.fees)],
          ["tax basis", known ? share(c.taxBasis) : UNKNOWN_BASIS],
          ["wheel basis", known ? share(c.wheelBasis) : UNKNOWN_BASIS],
          [
            "breakeven",
            c.breakeven && c.breakeven.price !== null
              ? `${share(c.breakeven.price)}${c.breakeven.basis ? ` (${c.breakeven.basis})` : ""}`
              : "—",
          ],
        ]}
      />
      <p className="md-note">cash is the broker's, signed: + received. Fees are apart from it.</p>
    </DetailCard>
  );
}

function CyclePnl({ c }: { c: ManageWheelCycle }) {
  const stock = (v: number | null): ReactNode => (c.basisKnown === false ? UNKNOWN_BASIS : <Money v={v} />);
  return (
    <DetailCard title="P&L">
      <Kv
        rows={[
          ["option, realised", <Money key="or" v={c.optionPnl.realised} />],
          ["option, unrealised", <Money key="ou" v={c.optionPnl.unrealised} />],
          ["stock, realised", stock(c.stockPnl.realised)],
          ["stock, unrealised", stock(c.stockPnl.unrealised)],
          ["dividends", <Money key="d" v={c.dividends} />],
          ["fees paid", feesPaid(c.fees)],
          ["P&L net", c.pnlNet === null ? "unknown: a part of it is" : <Money key="n" v={c.pnlNet} />],
        ]}
      />
      <p className="md-note">option and stock P&L are gross of fees; P&L net is their sum less fees. Unrealised is at today's mid, not a fill.</p>
    </DetailCard>
  );
}

function CycleYield({ c }: { c: ManageWheelCycle }) {
  const y = c.skateYield;
  return (
    <DetailCard title="skate yield" aside={<span className="chip muted">comparison figure</span>}>
      <Kv
        rows={[
          ["a year", y?.value === null || y?.value === undefined ? "—" : `${(y.value * 100).toFixed(1)}%${y.capped ? " (past the cap)" : ""}`],
          ["time-weighted capital", fmtMoney(y?.capital ?? null)],
        ]}
      />
      <p className="md-note">{y?.basis ?? "net P&L over time-weighted capital, annualised"}. Never a forecast; none under seven days.</p>
    </DetailCard>
  );
}

/* ------------------------------------------------------------------ today's check */

/** Today's check on each short. No intrinsic column (2026-10-21): it is the mid less the extrinsic,
 *  both shown. The band and where the strike sits against it are the reasoning behind "tested", in
 *  the detail view. */
const CHECK_COLUMNS: Column<ManageWheelCheck>[] = [
  {
    key: "short",
    label: "short",
    td: (r) => (
      <td>
        {r.quantity ?? "?"} {r.leg}
      </td>
    ),
  },
  { key: "dte", label: "dte", td: (r) => <td>{r.dte ?? "—"}</td> },
  { key: "spot", label: "underlying", td: (r) => <td>{px(r.spot)}</td> },
  { key: "mid", label: "mid (a share)", td: (r) => <td>{px(r.mid)}</td> },
  {
    key: "tested",
    label: "tested",
    td: (r) => (
      <td>
        <TestedChip tested={r.tested} />
      </td>
    ),
  },
  {
    key: "extrinsic",
    label: "extrinsic",
    td: (r) => (
      <td>
        {px(r.extrinsic)} a share · {fmtMoney(r.extrinsicDollars)}
      </td>
    ),
  },
  { key: "band", label: "skate band", detailOnly: true, td: (r) => <td style={{ whiteSpace: "normal" }}>{bandText(r)}</td> },
  { key: "beyond", label: "strike vs band", detailOnly: true, td: (r) => <td>{beyondText(r)}</td> },
  {
    key: "btc",
    label: "resting buy-to-close",
    td: (r) => (
      <td>
        {r.restingBtc.length === 0
          ? "none resting"
          : r.restingBtc.map((o) => `${px(o.price)} × ${o.quantity ?? "?"}${o.timeInForce ? ` ${o.timeInForce}` : ""}`).join(", ")}
      </td>
    ),
  },
  {
    key: "underBtc",
    label: "mark at or under it",
    td: (r) => <td>{r.btcPrice === null ? "—" : yesNo(r.markAtOrUnderBtc, "at or under", "above")}</td>,
  },
];

function TodayCheck({ rows }: { rows: ManageWheelCheck[] }) {
  const cols = useColumns(CHECK_COLUMNS);
  return (
    <>
      <p className="md-subhead">today's check</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <HeadRow cols={cols} />
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol}>
                <Cells cols={cols} row={r} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ the rules */

function RollLine({ kind, r, why }: { kind: string; r: ManageWheelRoll | null; why: string | null }) {
  if (!r) {
    return (
      <li>
        <span className="md-rule-head">{kind}</span>
        <span className="md-sub">none: {why ?? "not priced"}</span>
      </li>
    );
  }
  const verdict = r.check?.verdict ?? null;
  return (
    <li>
      <span className="md-rule-head">
        {kind} <span className="md-price">{r.net === null ? "—" : `${fmtPrice(r.net)} a share`}</span>{" "}
        {verdict ? (
          <span className={VERDICT_CHIP[verdict] ?? "chip"}>check-roll {verdict}</span>
        ) : (
          <span className="chip muted">unchecked{r.checkError ? `: ${r.checkError}` : ""}</span>
        )}
      </span>
      <span className="md-sub">{r.legs.join(" · ")}</span>
      {r.check && (
        <span className="md-sub">
          credit {fmtCash(r.check.credit)} · max loss {fmtMoney(r.check.maxLossBefore)} → {fmtMoney(r.check.maxLossAfter)}
          {r.check.problems.length ? ` · problems: ${r.check.problems.join("; ")}` : ""}
          {r.check.notes.length ? ` · ${r.check.notes.join("; ")}` : ""}
        </span>
      )}
    </li>
  );
}

/** Whose quotes a roll is priced on, from manage's own `source` and stamp: this session's roll-out
 *  chains, or the income reading (taken on an afternoon, which may be this one or the one before). */
export function rollQuotes(r: ManageWheelRolls): string {
  const when = etStamp(r.quotedAt);
  if (r.source === "roll-out chains") {
    const looked = r.considered.filter((c) => c.expiration !== null);
    const tried =
      looked.length > 1
        ? ` (looked at ${looked.map((c) => `${c.expiration}${c.netZero ? "" : ", no net-zero"}`).join("; ")})`
        : "";
    return `this session's roll-out chains${when ? `, read ${when}` : ""}${tried}`;
  }
  return `the income reading${when ? `, taken ${when}` : ""}`;
}

function Rolls({ r }: { r: ManageWheelRolls }) {
  return (
    <>
      <p className="md-subhead">
        rolls for the {r.leg}
        {r.expiration ? ` to ${r.expiration}` : ""}
        {r.expiration ? <span className="muted"> · priced on {rollQuotes(r)}</span> : null}
      </p>
      {r.expiration === null ? (
        <p className="md-note">no roll priced: {r.why ?? "no quoted chain"}</p>
      ) : (
        <ul className="md-rules">
          <RollLine kind="net-zero" r={r.netZero} why={r.netZeroWhy} />
          <RollLine kind="premium gambit" r={r.premiumGambit} why={r.premiumGambitWhy} />
        </ul>
      )}
    </>
  );
}

function NoteDetail({ n }: { n: ManageWheelNote }) {
  return (
    <>
      {(n.takeProfit ?? []).map((tp) => (
        <p key={tp.leg} className="md-note">
          {tp.leg}: buy-to-close {px(tp.btcPrice)} ({tp.source ?? "?"}), marked {px(tp.mid)}:{" "}
          {yesNo(tp.hit, "at or under it", "above it")}
        </p>
      ))}
      {(n.rolls ?? []).map((r) => (
        <Rolls key={r.leg} r={r} />
      ))}
      {n.nextLeg && (
        <>
          <p className="md-subhead">next leg</p>
          <p className="md-note">
            {n.nextLeg.strike !== null
              ? `sell ${n.nextLeg.contracts ?? "?"} ${n.nextLeg.expiration ?? "?"} ${strike(n.nextLeg.strike)}C at ${px(n.nextLeg.quote?.mid ?? null)} a share (the income reading's mid${etStamp(n.nextLeg.quotedAt) ? `, taken ${etStamp(n.nextLeg.quotedAt)}` : ""}), the first quoted strike at or above the ${n.nextLeg.bandKind ?? "?"} call band ${px(n.nextLeg.band)}`
              : n.nextLeg.band !== null
                ? `the ${n.nextLeg.bandKind ?? "?"} call band is ${px(n.nextLeg.band)} on ${n.nextLeg.expiration ?? "?"}: ${n.nextLeg.why ?? "no quote"}`
                : `no covered call named: ${n.nextLeg.why ?? "no band"}`}
            {n.nextLeg.belowWheelBasis ? " · under the wheel basis, so a call-away there loses" : ""}
          </p>
        </>
      )}
    </>
  );
}

function WheelRules({ c, trades }: { c: ManageWheelCycle; trades: ManageTrade[] }) {
  if (c.status === "unreconciled") {
    return (
      <DetailCard title="wheel rules">
        <p className="md-note">no wheel rule judged: the cycle does not reconcile, so check it by hand</p>
      </DetailCard>
    );
  }
  const mine = trades.filter((t) => c.tradeIds.includes(t.tradeId));
  const fired = mine.flatMap((t) => t.fired.filter((f) => f.rule.startsWith("wheel.")).map((f) => ({ t, f })));
  const notes = mine.flatMap((t) => (t.wheel ?? []).filter((n) => n.underlying === c.underlying && n.start === c.start));
  return (
    <DetailCard title="wheel rules" aside={<span className="muted">{fired.length} fired</span>}>
      {mine.length === 0 ? (
        <p className="md-note">{c.end ? "the cycle is finished: no trade holds its legs" : "no evaluated trade holds its legs"}</p>
      ) : fired.length === 0 ? (
        <p className="md-note">none fired on {mine.map((t) => t.tradeId).join(", ")}</p>
      ) : (
        <ul className="md-rules">
          {fired.map(({ t, f }, i) => (
            <li key={i}>
              <span className="md-rule-head">
                <ActionChip action={f.action} /> <code>{f.rule}</code>
                {f.confidence !== null ? <span className="muted"> {f.confidence}</span> : null}
                <span className="muted"> · {t.tradeId}</span>
              </span>
              <span className="md-sub">{f.reason}</span>
            </li>
          ))}
        </ul>
      )}
      {notes.map((n, i) => (
        <NoteDetail key={i} n={n} />
      ))}
    </DetailCard>
  );
}

/* ------------------------------------------------------------------ one cycle, the account */

function Events({ c }: { c: ManageWheelCycle }) {
  if (c.events.length === 0) return null;
  return (
    <>
      <p className="md-subhead">history</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>date</th>
              <th>what</th>
              <th>legs</th>
              <th>cash</th>
              <th>fees</th>
            </tr>
          </thead>
          <tbody>
            {c.events.map((e, i) => (
              <tr key={i}>
                <td>{e.date ?? "before the history"}</td>
                <td>{e.what}</td>
                <td style={{ whiteSpace: "normal" }}>{e.legs.join(" · ")}</td>
                <td>{fmtCash(e.cash)}</td>
                <td>{feesPaid(e.fees)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** The tabs of a cycle's lightbox: what happened, the money, and the rules and checks. */
export type CycleTab = "history" | "money" | "rules";

export const CYCLE_TABS: Array<{ id: CycleTab; label: string }> = [
  { id: "history", label: "history" },
  { id: "money", label: "money & P&L" },
  { id: "rules", label: "wheel rules" },
];

/** A cycle's id in the URL (`?wb=`). */
export function cycleKey(c: ManageWheelCycle): string {
  return `${c.underlying}-${c.start ?? ""}`;
}

/** What a cycle's lightbox shows under a tab. Every figure is manage's. */
export function CycleBody({ c, trades, tab }: { c: ManageWheelCycle; trades: ManageTrade[]; tab: CycleTab }) {
  if (tab === "money") {
    return (
      <div className="md-cards">
        <CycleMoney c={c} />
        <CyclePnl c={c} />
        <CycleYield c={c} />
      </div>
    );
  }
  if (tab === "rules") {
    return (
      <>
        {c.today.length > 0 && <TodayCheck rows={c.today} />}
        <div className="md-cards">
          <WheelRules c={c} trades={trades} />
        </div>
      </>
    );
  }
  return (
    <>
      <div className="md-cards">
        <CycleFacts c={c} />
      </div>
      <Events c={c} />
    </>
  );
}

function cycleHelp(): CardHelpContent {
  return {
    title: "Reading a cycle",
    body: (
      <>
        <HelpSection title="Net">
          <p>After fees. Option and stock P&amp;L are gross of fees; the breakeven is a price per share.</p>
        </HelpSection>
        <HelpSection title="Today's check">
          <p>Each short the cycle holds, with manage's tested flag. How far it is from its strike is shown where the needs-a-look list has it.</p>
        </HelpSection>
        <HelpSection title="Skate yield">
          <p>A comparison figure, a year time-weighted; never a forecast, none under seven days. It is under money &amp; P&amp;L.</p>
        </HelpSection>
        <GuideLink to="/help/income-trades#step-3-the-wheel-view" />
      </>
    ),
  };
}

/** Each short's distance from its strike where manage's attention lists have the leg, by option symbol. */
export function distancesBySymbol(a: ManageAttention | null): Map<string, number | null> {
  const m = new Map<string, number | null>();
  for (const x of [...(a?.items ?? []), ...(a?.expiring ?? [])]) if (x.symbol) m.set(x.symbol, x.distance);
  return m;
}

function daysText(c: ManageWheelCycle): string {
  return c.days === null ? "—" : c.startKnown === false ? `${c.days}+` : String(c.days);
}

/** The line under a cycle's figures: what it holds against today's strike, or why it holds nothing. */
function CheckLines({ c, near }: { c: ManageWheelCycle; near: Map<string, number | null> }) {
  if (c.today.length === 0) {
    const said =
      c.status === "waiting"
        ? "flat · waiting for a put"
        : c.status === "complete"
          ? c.calledAway ? `called away ${c.end ?? "?"}` : `ended ${c.end ?? "?"}`
          : "no short option held";
    return (
      <ul className="lk-list">
        <li className="lk-li lk-li-flat">
          <span className="lk-leg">{said}</span>
        </li>
      </ul>
    );
  }
  return (
    <ul className="lk-list">
      {c.today.map((r) => {
        const d = near.get(r.symbol);
        return (
          <li key={r.symbol} className="lk-li lk-li-flat">
            <span className="lk-leg">
              short {r.leg} · {r.dte ?? "—"} dte{d === undefined ? "" : ` · ${distanceWords(d)}`}
            </span>
            <TestedChip tested={r.tested} />
          </li>
        );
      })}
    </ul>
  );
}

/** A cycle as a parent card: net, breakeven, days, today's short; its history, money and rules open
 *  from it. */
export function CycleCard({ c, near = new Map() }: { c: ManageWheelCycle; near?: Map<string, number | null> }) {
  const breakeven = c.breakeven && c.breakeven.price !== null ? `breakeven ${px(c.breakeven.price)} a share` : "breakeven —";
  return (
    <Card
      title={`${c.underlying} · wheel`}
      collapseKey={`wheel-${c.underlying}-${c.start ?? ""}`}
      className="pf-cq wh-cycle"
      help={cycleHelp()}
      controls={<StatusChip status={c.status} />}
    >
      {c.status === "unreconciled" && (
        <div className="pnl-neg">
          <p style={{ margin: 0 }}>does not reconcile: its money is shown as far as the rows go, not a basis to trade on</p>
          <ul>
            {c.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="pf-hero">
        {c.pnlNet === null ? <span className="muted">net unknown</span> : <span className={tone(c.pnlNet)}>{fmtCash(c.pnlNet)}</span>}
        <span className="pf-unit"> net · {breakeven}</span>
      </div>
      <div className="pf-tiles pf-tiles-3">
        <Tile label="days" value={daysText(c)} title={c.startKnown === false ? "shares predate the history: at least this many" : undefined} />
        <Tile label="shares" value={c.shares === null ? "—" : String(c.shares)} />
        <Tile label="assigned" value={c.assignments === null ? "—" : String(c.assignments)} />
      </div>
      <CheckLines c={c} near={near} />
      <BoxButtons>
        <BoxButton id={cycleKey(c)}>history</BoxButton>
      </BoxButtons>
    </Card>
  );
}

export const NO_WHEEL = "no wheel running: a wheel starts with a short put sold alone or shares held";

/** The cycles by manage's status: "2 open · 1 waiting for a put · 3 complete". */
export function cycleCount(cycles: ManageWheelCycle[]): string {
  const n = (s: string) => cycles.filter((c) => c.status === s).length;
  const parts: string[] = [];
  const running = n("open") + n("unreconciled");
  if (running) parts.push(`${running} open`);
  if (n("waiting")) parts.push(`${n("waiting")} waiting for a put`);
  if (n("complete")) parts.push(`${n("complete")} complete`);
  return parts.length ? parts.join(" · ") : `${cycles.length} cycle${cycles.length === 1 ? "" : "s"}`;
}

/** Cash-settled index names manage left out of the wheel, said once: no shares are delivered there. */
export function leftOutText(names: string[]): string | null {
  return names.length ? `left out, cash-settled (no shares are ever delivered): ${names.join(", ")}` : null;
}

/* ------------------------------------------------------------------ the cycles' lightbox */

function CycleDialog({ cycles, trades, index, onStep, onClose }: { cycles: ManageWheelCycle[]; trades: ManageTrade[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const c = cycles[index]!;
  const id = cycleKey(c);
  const [state, setTab] = useState<{ id: string; tab: CycleTab }>({ id, tab: "history" });
  // A step to another cycle opens on its first tab.
  const tab = state.id === id ? state.tab : "history";
  const fired = trades.filter((t) => c.tradeIds.includes(t.tradeId)).flatMap((t) => t.fired.filter((f) => f.rule.startsWith("wheel.")));
  const tabs: LightboxTab<CycleTab>[] = CYCLE_TABS.map((t) => ({ ...t, badge: t.id === "rules" && fired.length ? `${fired.length} fired` : null }));
  return (
    <CardLightbox count={cycles.length} index={index} onStep={onStep} onClose={onClose} wide>
      {(nav) => (
        <LightboxFrame
          kicker="wheel ›"
          title={`${c.underlying} · cycle from ${c.start ?? "?"}`}
          chips={<StatusChip status={c.status} />}
          tabs={tabs}
          tab={tab}
          onTab={(t) => setTab({ id, tab: t })}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          <div className="pf-box-body">
            <CycleBody c={c} trades={trades} tab={tab} />
          </div>
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The provider and the open lightbox around one account's wheel cards; the cycle buttons reach it
 *  through the portfolio tab's context, so they are the same buttons. */
function WheelBoxes({ a, children }: { a: ManageAccount; children: ReactNode }) {
  const lb = useOpenParam("wb");
  const cycles = a.wheel?.cycles ?? [];
  const ids = new Set(cycles.map(cycleKey));
  const index = cycles.findIndex((c) => cycleKey(c) === lb.open);
  return (
    <PortfolioBoxContext.Provider value={{ open: lb.show, has: (id) => ids.has(id) }}>
      {children}
      {index >= 0 ? <CycleDialog cycles={cycles} trades={a.trades} index={index} onStep={(to) => lb.step(cycles[to] ? cycleKey(cycles[to]!) : "")} onClose={lb.close} /> : null}
    </PortfolioBoxContext.Provider>
  );
}

/* ------------------------------------------------------------------ the tab */

/**
 * One account's wheel, laid out by question: what needs a look, how much it has made, how each cycle
 * stands, what a trade would do.
 */
export function WheelCards({ a }: { a: ManageAccount }) {
  const scope = a.wheel?.names ?? null;
  const held = a.payoff ? (scope === null ? a.payoff.names : a.payoff.names.filter((n) => scope.includes(n.underlying))).length : 0;
  return (
    <WheelBoxes a={a}>
      <div className="pf-layout">
        {a.attention ? (
          <Section concept="risk" tag="needs a look" question="What needs a look?">
            <NearStrikeCard a={a.attention} scope={scope} />
            <ExpiringCard a={a.attention} scope={scope} />
          </Section>
        ) : null}
        {a.ledger ? (
          <Section concept="income" tag="the ledger" question="How much has it made?">
            <LedgerDesk ledger={a.ledger} />
          </Section>
        ) : null}
        <Section concept="dir" tag="each cycle" question="How does each cycle stand?">
          <WheelCycles a={a} />
        </Section>
        {a.payoff && held > 0 ? (
          <Section concept="add" tag="what if" question="What would a trade do?">
            <PayoffStrip payoff={a.payoff} scope={scope} />
          </Section>
        ) : null}
      </div>
    </WheelBoxes>
  );
}

/** The wheel's cycles, a card each, or a plain statement of why there is none. */
function WheelCycles({ a }: { a: ManageAccount }) {
  const w = a.wheel;
  if (w === null) {
    return (
      <Card title={`wheel · ${a.account ?? "?"}`}>
        <p className="muted" style={{ margin: 0 }}>
          this evaluation was written before the wheel view: it has no wheel for the account
        </p>
      </Card>
    );
  }
  const flagged = w.flagged;
  const outside = Object.entries(w.outside).filter(([, rows]) => rows.length > 0);
  const near = distancesBySymbol(a.attention);
  return (
    <>
      {w.cycles.length === 0 ? (
        <Card title={`wheel · ${a.account ?? "?"}`}>
          <p className="muted" style={{ margin: 0 }}>
            {NO_WHEEL}
          </p>
          {w.error && <p className="muted">no wheel view for this account: {w.error}</p>}
        </Card>
      ) : (
        <>
          <p className="muted">
            {a.account ?? "?"} · {cycleCount(w.cycles)}
            {w.bandsMissing ? ` · skate bands: ${w.bandsMissing}` : w.bandsDay ? ` · skate bands from ${w.bandsDay}` : ""}
          </p>
          {w.cycles.map((c) => (
            <CycleCard key={cycleKey(c)} c={c} near={near} />
          ))}
        </>
      )}
      {leftOutText(w.leftOut) && <p className="muted">{leftOutText(w.leftOut)}</p>}
      {flagged.length > 0 && (
        <Card title="deliveries nothing explains" className="wh-wide">
          <ul style={{ margin: 0 }}>
            {flagged.map((f, i) => (
              <li key={i}>
                {f.underlying} · {f.date ?? "?"} · {f.kind}
                {f.row ? `: ${f.row}` : ""}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {outside.length > 0 && (
        <Card title="history outside any cycle" className="wh-wide">
          <p className="muted" style={{ marginTop: 0 }}>
            rows manage read for a name that belong to no wheel cycle (a call sold with no shares behind it, say), listed as
            it wrote them
          </p>
          <ul style={{ margin: 0 }}>
            {outside.map(([name, rows]) => (
              <li key={name}>
                {name}: {rows.join(" · ")}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
