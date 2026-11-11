import { useContext, useState, type ReactNode } from "react";
import type { ManageAccount, ManageBand, ManageFired, ManagePmccPosition, ManagePmccRecognize, ManagePmccRoll, ManageTrade } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { ActionBlock } from "../../components/ActionBlock";
import { CardLightbox, LightboxFrame, useOpenParam } from "../Backtest/CardLightbox";
import { Tile } from "../Flies/PerformanceTab";
import { BandDial } from "./PortfolioCards";
import { chainLegs } from "./PositionCards";
import { Section } from "../../components/Section";
import { BoxButton, BoxButtons, PortfolioBoxContext } from "./portfolioBoxContext";
import { useLiveMarks } from "./LiveMarks";
import { livePmccs } from "./liveLegs";
import { Cells, HeadRow, useColumns, type Column } from "../../components/columns";
import { fmtCash, fmtMoney, fmtPrice } from "../../lib/format";
import {
  ACTION_LABEL,
  ActionChip,
  DetailCard,
  Kv,
  Money,
  StatusChip,
  TestedChip,
  VerdictsHeld,
  VERDICT_CHIP,
  bandText,
  beyondText,
  feesPaid,
  px,
  etStamp,
  share,
} from "./manageParts";
import { GuideLink } from "../../components/GuideLink";
import { Link } from "react-router-dom";
import { ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { CardRows, type CardRow } from "./CardRows";
import { accountTitle } from "./accountTitle";

/**
 * Live → pmcc: each poor man's covered call packages/manage reads on the account (`pmcc.py`),
 * one card per name whose calls have a PMCC's shape, and the PMCC rules
 * that fired on the trades holding its legs. The columns are the wheel cards' wherever they mean the
 * same thing (today's check on a short, the skate yield, the rules and their rolls).
 *
 * It computes nothing. The delta at entry and today, the width check, the P&L if assigned and the
 * skate yield are manage's; "passes", "exposed", "tested", "beyond the band", "inside its life" and
 * the long's roll are manage's flags, never worked out here from the figures beside them. Money is in
 * the suite's layout: signed whole-position cash (+ received), fees a cost shown once, and every
 * per-share figure says so.
 */

export const NO_PMCC = "no PMCC: one is recognised when";

/** Why there is no PMCC, in the evaluation's own recognition limits (`pmcc.recognize`); where the
 *  evaluation embeds no ruleset, the conditions without their numbers rather than numbers it was not
 *  judged by. */
export function noPmccText(rc: ManagePmccRecognize | null | undefined): string {
  const delta = rc?.entryMinDelta != null ? rc.entryMinDelta.toFixed(2) : "the entry floor";
  // An evaluation from before 2026-11-04 was also judged by days left; say so in its own numbers.
  const long = rc?.minLongDays != null ? `, with at least ${rc.minLongDays} days left when it was bought` : "";
  const short = rc?.maxShortDays != null ? ` with at most ${rc.maxShortDays} days left` : "";
  return (
    `${NO_PMCC} a long call entered at ${delta} delta or more${long} (or a name given intent: "pmcc" in ` +
    `the ruleset), has one shorter-dated call of the same size sold against it${short}, at or above the ` +
    "long's strike, and no shares on the name"
  );
}

function pct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function delta(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

/** Manage's width verdict. Null is "no cost to judge it against", never a pass. A pass manage flags
 *  thin (the cost over three quarters of the width) says so; the flag is manage's, never worked out
 *  here from the figures. */
export function WidthChip({ passes, thin = null }: { passes: boolean | null; thin?: boolean | null }) {
  if (passes === null) return <span className="chip muted">unknown</span>;
  if (!passes) return <span className="chip chip-warn">fails</span>;
  return thin ? <span className="chip chip-warn">passes, thin</span> : <span className="chip">passes</span>;
}

/** Manage's early-assignment flag. Null is "not judged" (no mark), never "not exposed". */
export function ExposureChip({ exposed }: { exposed: boolean | null }) {
  if (exposed === null) return <span className="chip muted">unknown</span>;
  return exposed ? <span className="chip chip-warn">exposed</span> : <span className="chip">not exposed</span>;
}

function status(p: ManagePmccPosition): string {
  if (p.reconciled === false) return "unreconciled";
  return "open";
}

/* ------------------------------------------------------------------ the cards */

/** What manage decided, in words for the card's title chip and the position card. An evaluation
 * written before `recognition` falls back to the flag, and an unrecognised one there says only "not a
 * PMCC", since it cannot tell a diagonal from a candidate not yet decided. */
export function kindOf(p: ManagePmccPosition): { kind: string; chipClass: string; recognised: string } {
  const r = p.recognition ?? (p.recognized === true ? "pmcc" : p.recognized === false ? null : undefined);
  if (r === "pmcc") return { kind: "PMCC", chipClass: "chip", recognised: "yes" };
  if (r === "diagonal") return { kind: "diagonal", chipClass: "chip muted", recognised: "no, a diagonal" };
  if (r === "unknown") {
    // Muted, not amber: nothing to act on, it is decided once the entry day's close is in.
    return { kind: "PMCC? not yet decided", chipClass: "chip muted", recognised: "not yet: the delta at entry is unknown" };
  }
  if (r === null) return { kind: "not a PMCC", chipClass: "chip muted", recognised: "no" };
  return { kind: "unknown", chipClass: "chip muted", recognised: "—" };
}

/** The intent row: what is set and where, and the key that sets it on this PMCC alone (manage's
 * `trades.<long contract>.intent`), so it can be copied into the config as it stands. */
export function intentWords(p: ManagePmccPosition): string {
  const key = p.intentKey ? `trades key "${p.intentKey}"` : null;
  const set =
    p.intent && p.intentSource === "contract"
      ? `${p.intent}, set on this long`
      : p.intent && p.intentSource === "symbol"
        ? `${p.intent}, set on ${p.underlying} (every PMCC on the name)`
        : "none set";
  return key ? `${set} · ${key}` : set;
}

/** Where the chain's money runs from: this long alone, or back through the longs it was rolled from. */
export function chainWords(p: ManagePmccPosition): string {
  const stitched = p.continued.length ? `; continues ${p.continued.join(", ")} (two-order roll, confirmed)` : "";
  if (!p.chainSince || p.chainSince === p.long.opened) return `this long only${stitched}`;
  return `since ${p.chainSince}, through its rolls${stitched}`;
}

function PositionFacts({ p }: { p: ManagePmccPosition }) {
  return (
    <DetailCard title="position" aside={<StatusChip status={status(p)} />}>
      <Kv
        rows={[
          ["recognised", kindOf(p).recognised],
          ["because", p.recognizedBy ?? "—"],
          ["intent", intentWords(p)],
          ["underlying", px(p.spot)],
          ["long bought", p.long.opened ?? "before the history"],
          ["chain", chainWords(p)],
          ["trades", p.tradeIds.length ? p.tradeIds.join(", ") : "none"],
        ]}
      />
    </DetailCard>
  );
}

/** The long's roll chip and row. manage raises its long-roll rule only on a recognised PMCC, so on any
 * other candidate the thresholds the long crossed are shown as information, never as a warning. */
export function rollWords(p: ManagePmccPosition): { chip: string; chipClass: string; row: string } {
  const why = p.long.roll.join("; ");
  if (p.recognized !== true) {
    const not = p.recognition === "unknown" ? "not yet a PMCC" : "not a PMCC";
    return { chip: "no roll rule", chipClass: "chip muted", row: `not raised, ${not}${why ? ` (${why})` : ""}` };
  }
  return why
    ? { chip: "roll the long", chipClass: "chip chip-warn", row: why }
    : { chip: "no roll due", chipClass: "chip", row: "none due" };
}

/** The long's delta at entry with where it came from: recorded after the fill (with the gap and the
 * band the underlying's move over it puts on the delta), or solved from the entry day's close. */
export function entryWords(p: ManagePmccPosition): string {
  const l = p.long;
  if (l.deltaAtEntry === null) return `delta at entry unknown${l.deltaAtEntryWhy ? ` (${l.deltaAtEntryWhy})` : ""}`;
  // The fill the delta rests on: the lowest, as manage takes it.
  const f = l.entryFills.find((x) => x.delta === l.deltaAtEntry);
  if (f?.basis === "recorded") {
    const gap = f.gapSeconds === null ? "" : ` ${Math.round(f.gapSeconds)} s after the fill`;
    const band = f.deltaLow === null || f.deltaHigh === null ? "" : `, ${delta(f.deltaLow)}-${delta(f.deltaHigh)} at the likely move`;
    return `${delta(l.deltaAtEntry)} (recorded${gap}${band})`;
  }
  if (f?.basis === "close") return `${delta(l.deltaAtEntry)} (from the ${f.date ?? "entry day"} close)`;
  return delta(l.deltaAtEntry);
}

function LongCard({ p }: { p: ManagePmccPosition }) {
  const l = p.long;
  const roll = rollWords(p);
  return (
    <DetailCard title="the long" aside={<span className={roll.chipClass}>{roll.chip}</span>}>
      <Kv
        rows={[
          ["leg", `${l.quantity ?? "?"} ${l.leg}`],
          ["delta at entry", entryWords(p)],
          ["delta today", l.delta === null ? "unknown" : delta(l.delta)],
          ["mid", share(l.mid)],
          ["intrinsic", share(l.intrinsic)],
          ["extrinsic", `${share(l.extrinsic)} (${pct(l.extrinsicShare)} of its price)`],
          ["days left", l.dte === null ? "—" : String(l.dte)],
          ["roll", roll.row],
        ]}
      />
      {l.rollDeltaUnknown ? <p className="md-note">today's delta is unknown, so the delta floor was not checked</p> : null}
    </DetailCard>
  );
}

/** The width check's caveat for a possible two-order roll of the long: the chain's cost may leave the
 * old long out, so the check may pass a short whose assignment would lock in a loss. Says how to
 * confirm it (`trades.<this long>.continues`). Null when manage saw none. */
export function twoOrderRollWords(p: ManagePmccPosition): string | null {
  const r = p.possibleRoll;
  if (!r) return null;
  const key = p.intentKey ?? "this long";
  return (
    `possibly rolled in two orders: the ${r.leg} was sold on ${r.closed} and nothing links it to this long, ` +
    `so its cash is left out of the cost above. If it was a roll, set trades "${key}": ` +
    `{"continues": "${r.symbol}"} in manage's config.`
  );
}

function WidthCard({ p }: { p: ManagePmccPosition }) {
  const w = p.width;
  const openLegs = w.basis === "open_legs";
  return (
    <DetailCard title="width check" aside={<WidthChip passes={w.passes} thin={w.thin} />}>
      <Kv
        rows={[
          ["strike width", share(w.width)],
          ["long's net cost after premiums", `${fmtMoney(w.netCost)} (${share(w.netCostPerShare)})`],
          ...(w.costShare === null
            ? []
            : ([["cost as a share of the width", `${pct(w.costShare)}${w.warnShare === null ? "" : ` (thin over ${pct(w.warnShare)})`}`]] as [string, string][])),
          ["P&L if assigned, gross", <Money key="g" v={w.pnlIfAssigned} />],
          ["P&L if assigned, net of fees", w.pnlIfAssignedNet === null ? (openLegs ? "n/r: the orders do not reconcile" : "—") : <Money key="n" v={w.pnlIfAssignedNet} />],
          ["cost from", openLegs ? "the open legs' prices (earlier shorts not counted)" : (w.basis ?? "—")],
        ]}
      />
      {w.assumes ? <p className="md-note">assumes {w.assumes}</p> : null}
      {twoOrderRollWords(p) ? (
        <p className="md-note">
          <span className="chip chip-warn">check the roll</span> {twoOrderRollWords(p)}
        </p>
      ) : null}
    </DetailCard>
  );
}

function MoneyCard({ p }: { p: ManagePmccPosition }) {
  return (
    <DetailCard title="money">
      <Kv
        rows={[
          ["long bought for", fmtMoney(p.longCost)],
          ["premiums, net", fmtCash(p.premiums)],
          ["net cash", fmtCash(p.netCash)],
          ["fees paid", feesPaid(p.fees)],
        ]}
      />
      <p className="md-note">cash is the broker's, signed: + received. Fees are apart from it.</p>
    </DetailCard>
  );
}

function YieldCard({ p }: { p: ManagePmccPosition }) {
  const y = p.skateYield;
  return (
    <DetailCard title="skate yield" aside={<span className="chip muted">comparison figure</span>}>
      <Kv
        rows={[["a year", y?.value === null || y?.value === undefined ? "—" : `${(y.value * 100).toFixed(1)}%${y.capped ? " (past the cap)" : ""}`]]}
      />
      <p className="md-note">{yieldBasis(y?.basis ?? null)}. Never a forecast; none under seven days.</p>
    </DetailCard>
  );
}

/** Manage's statement of what the yield is. An evaluation before 2026-10-21 took the shorts' P&L
 *  gross of their fees and said only "P&L"; "P&L" means net across the suite, so that one is
 *  labelled gross here rather than left reading as net. */
export function yieldBasis(basis: string | null): string {
  if (basis === null) return "the shorts' net P&L over the long's cost, annualised";
  return /net P&L/.test(basis) ? basis : `${basis} (gross: the shorts' fees not subtracted)`;
}

/** Whose quotes the rolls are priced on: this session's roll-out chains first where the snapshot read
 *  them for the name, the income reading for any expiry they lack. */
export function pmccRollQuotes(r: NonNullable<ManagePmccPosition["rolls"]>): string {
  const income = etStamp(r.quotedAt);
  const reading = `the income reading${income ? ` (taken ${income})` : ""}`;
  if (r.chainsTakenAt == null) return reading;
  const chains = etStamp(r.chainsTakenAt);
  return `this session's roll-out chains${chains ? ` (read ${chains})` : ""}, and ${reading} for any expiry they lack`;
}

function RollLine({ r }: { r: ManagePmccRoll }) {
  const verdict = r.check?.verdict ?? null;
  return (
    <li>
      <span className="md-rule-head">
        {r.expiration ?? "?"} {r.open?.strike ?? "?"}C at {delta(r.delta)} delta{" "}
        <span className="md-price">{r.net === null ? "—" : `${fmtPrice(r.net)} a share`}</span>{" "}
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

function PmccRules({ p, trades }: { p: ManagePmccPosition; trades: ManageTrade[] }) {
  if (p.recognized === false) {
    return (
      <DetailCard title="PMCC rules">
        <p className="md-note">not recognised as a PMCC: its diagonal rules stand, and no PMCC rule judges it</p>
      </DetailCard>
    );
  }
  if (p.reconciled === false) {
    return (
      <DetailCard title="PMCC rules">
        <p className="md-note">no PMCC rule judged: the orders do not reconcile, so check it by hand</p>
      </DetailCard>
    );
  }
  const mine = trades.filter((t) => p.tradeIds.includes(t.tradeId));
  const fired = mine.flatMap((t) => t.fired.filter((f) => f.rule.startsWith("pmcc.")).map((f) => ({ t, f })));
  const errors = mine.map((t) => t.pmcc?.error).filter((e): e is string => !!e);
  return (
    <DetailCard title="PMCC rules" aside={<span className="muted">{fired.length} fired</span>}>
      {fired.length === 0 ? (
        <p className="md-note">{mine.length ? `none fired on ${mine.map((t) => t.tradeId).join(", ")}` : "no evaluated trade holds its legs"}</p>
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
      {errors.map((e) => (
        <p key={e} className="pnl-neg">
          {e}
        </p>
      ))}
    </DetailCard>
  );
}

/** The short's earnings line. A report manage calls already out (the broker still lists the last
 *  one) is said as such, never as falling after the expiry; an evaluation from before manage said
 *  so carries no `past` and reads as it did. */
export function earningsWords(e: ManagePmccPosition["short"]["earnings"]): string {
  if (e.past === true) return `${e.date ?? "?"}: already reported; the next date is not known yet`;
  if (e.inside === null) return e.why ?? "—";
  return `${e.date ?? "?"}: ${e.inside ? "inside its life" : "after it expires"}`;
}

/** Today's check on the short, in the wheel's columns where they mean the same, then the PMCC's own. */
function ShortCheck({ p }: { p: ManagePmccPosition }) {
  const s = p.short;
  const earnings = earningsWords(s.earnings);
  // No intrinsic column (2026-10-21): it is the mid less the extrinsic. The band, the strike against
  // it and N(d2) explain a roll; they are the detail view's.
  const cols = useColumns<Column<null>>([
    {
      key: "short",
      label: "short",
      td: () => (
        <td>
          {s.quantity ?? "?"} {s.leg}
        </td>
      ),
    },
    { key: "dte", label: "dte", td: () => <td>{s.dte ?? "—"}</td> },
    { key: "spot", label: "underlying", td: () => <td>{px(p.spot)}</td> },
    { key: "mid", label: "mid (a share)", td: () => <td>{px(s.mid)}</td> },
    {
      key: "tested",
      label: "tested",
      td: () => (
        <td>
          <TestedChip tested={s.tested} />
        </td>
      ),
    },
    { key: "extrinsic", label: "extrinsic (a share)", td: () => <td>{px(s.extrinsic)}</td> },
    { key: "band", label: "skate band", detailOnly: true, td: () => <td style={{ whiteSpace: "normal" }}>{bandText(s)}</td> },
    { key: "beyond", label: "strike vs band", detailOnly: true, td: () => <td>{beyondText(s)}</td> },
    {
      key: "assignment",
      label: "assignment",
      td: () => (
        <td style={{ whiteSpace: "normal" }}>
          <ExposureChip exposed={s.assignment.exposed} />
          {s.assignment.why ? <span className="muted"> {s.assignment.why}</span> : null}
        </td>
      ),
    },
    { key: "nd2", label: "N(d2)", detailOnly: true, td: () => <td>{pct(s.probItm)}</td> },
    { key: "earnings", label: "earnings", td: () => <td style={{ whiteSpace: "normal" }}>{earnings}</td> },
  ]);
  return (
    <>
      <p className="md-subhead">today's check</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <HeadRow cols={cols} />
          </thead>
          <tbody>
            <tr>
              <Cells cols={cols} row={null} />
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function ShortsSold({ p }: { p: ManagePmccPosition }) {
  if (p.shorts.length === 0) return null;
  return (
    <>
      <p className="md-subhead">every short sold against the long</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>short</th>
              <th>sold</th>
              <th>sold at (a share)</th>
              <th>cash</th>
              <th>fees</th>
              <th>ended</th>
            </tr>
          </thead>
          <tbody>
            {p.shorts.map((s) => (
              <tr key={s.symbol}>
                <td>{s.leg}</td>
                <td>{s.sold ?? "—"}</td>
                <td>{px(s.soldPrice)}</td>
                <td>{fmtCash(s.cash)}</td>
                <td>{feesPaid(s.fees)}</td>
                <td>{s.status ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ the roll candidates */

/** The candidates as manage priced them: where each rolls to, its net, the new short's delta and
 *  check-roll's verdict, then each order with what check-roll says of it. */
export function PmccRolls({ p }: { p: ManagePmccPosition }) {
  const r = p.rolls;
  if (!r) return <p className="muted">no roll priced: it is priced only for a recognised, reconciled PMCC whose width fails or whose short is exposed</p>;
  return (
    <>
      <p className="md-subhead">
        rolls for the {p.short.leg}
        <span className="muted"> · priced on {pmccRollQuotes(r)}</span>
      </p>
      {r.candidates.length === 0 ? (
        <p className="md-note">no roll priced: {r.why ?? "nothing quoted"}</p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>roll to</th>
                  <th>net (a share)</th>
                  <th>new short Δ</th>
                  <th>check-roll</th>
                </tr>
              </thead>
              <tbody>
                {r.candidates.map((c, i) => (
                  <tr key={i}>
                    <td>
                      {c.expiration ?? "?"} {c.open?.strike ?? "?"}C
                    </td>
                    <td className={c.net === null ? "" : c.net >= 0 ? "pnl-pos" : "pnl-neg"}>
                      {fmtPrice(c.net)}
                    </td>
                    <td>{delta(c.delta)}</td>
                    <td>
                      {c.check?.verdict ? (
                        <span className={VERDICT_CHIP[c.check.verdict] ?? "chip"}>{c.check.verdict}</span>
                      ) : (
                        <span className="chip muted">unchecked{c.checkError ? `: ${c.checkError}` : ""}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="md-rules">
            {r.candidates.map((c, i) => (
              <RollLine key={i} r={c} />
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ what needs action */

const QUIET = new Set(["HOLD", "SETTLES"]);

/** The PMCC rules that fired on the trades holding this PMCC's legs and ask for something, the most
 *  confident first. Manage's own: nothing here decides that a PMCC needs action. */
export function pmccActions(p: ManagePmccPosition, trades: ManageTrade[]): Array<{ t: ManageTrade; f: ManageFired }> {
  if (p.recognized === false || p.reconciled === false) return [];
  const mine = trades.filter((t) => p.tradeIds.includes(t.tradeId));
  return mine
    .flatMap((t) => t.fired.filter((f) => f.rule.startsWith("pmcc.") && !QUIET.has(f.action)).map((f) => ({ t, f })))
    .sort((a, b) => (b.f.confidence ?? -1) - (a.f.confidence ?? -1));
}

/** The roll to draw as the proposal: the first candidate check-roll passes, else the first priced. */
export function proposedRoll(p: ManagePmccPosition): ManagePmccRoll | null {
  const cs = p.rolls?.candidates ?? [];
  return cs.find((c) => c.check?.verdict === "ok" && c.legs.length > 0) ?? cs.find((c) => c.legs.length > 0) ?? null;
}

/** A leg line's quote, split off its text: "STO 1 2026-12-18 48C @ 0.80 (quoted 0.75/0.85)". */
function splitQuote(text: string): { text: string; quote: string | null } {
  const m = /^(.*?)\s*\(quoted ([^)]*)\)\s*$/.exec(text);
  return m ? { text: m[1] ?? text, quote: (m[2] ?? "").replace("/", " / ") } : { text, quote: null };
}

/** The proposed roll as an order chain, the way the positions tab draws a roll: its legs under a
 *  "proposed · roll" step and its price, then what manage's check-roll says it leaves. */
export function RollChain({ roll }: { roll: ManagePmccRoll }) {
  const legs = chainLegs(roll.legs);
  const price = fmtPrice(roll.net);
  return (
    <ol className="pos-chain" aria-label="the proposed roll">
      <li className="pos-step pos-step-roll">
        <span className="pos-dot" aria-hidden="true" />
        <div className="pos-step-body">
          <div className="pos-step-head">
            <span className="pos-step-date">proposed</span>
            <span className="pos-role">roll</span>
            <span className="pos-step-price">{price}</span>
          </div>
          <ul className="pos-legs">
            {legs.map((l, j) => {
              const q = splitQuote(l.text);
              const opens = l.action === "STO" || l.action === "BTO";
              return (
                <li key={j} className="pos-leg">
                  {l.action ? <span className={`pos-leg-act ${l.buy ? "is-buy" : "is-sell"}`}>{l.action}</span> : null}
                  <span>{q.text}</span>
                  {q.quote || (opens && roll.delta !== null) ? (
                    <span className="tc-leg-quote">{[q.quote, opens && roll.delta !== null ? `Δ ${delta(roll.delta)}` : null].filter(Boolean).join(" · ")}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      </li>
      <li className="pos-step pos-step-now">
        <span className="pos-dot" aria-hidden="true" />
        <div className="pos-step-body">
          <div className="pos-step-now-line">
            <span>
              after: short {roll.open?.strike ?? "?"}C, {roll.expiration ?? "?"}
            </span>
            {roll.check ? (
              <span>
                max loss {fmtMoney(roll.check.maxLossBefore)} → {fmtMoney(roll.check.maxLossAfter)}
              </span>
            ) : null}
          </div>
        </div>
      </li>
    </ol>
  );
}

/* ------------------------------------------------------------------ the parent card */

/** The tabs of a PMCC's lightbox. */
export type PmccTab = "shorts" | "rolls" | "long" | "rules" | "recognised";

export function pmccTabs(p: ManagePmccPosition): Array<{ id: PmccTab; label: string }> {
  return [
    { id: "shorts", label: "shorts sold" },
    ...(p.rolls ? [{ id: "rolls" as const, label: "roll candidates" }] : []),
    { id: "long", label: "long & width" },
    { id: "rules", label: "rules" },
    { id: "recognised", label: "recognised as" },
  ];
}

/** What a PMCC's lightbox shows under a tab. Every figure is manage's. */
export function PmccBody({ p, trades, tab }: { p: ManagePmccPosition; trades: ManageTrade[]; tab: PmccTab }) {
  if (tab === "rolls") return <PmccRolls p={p} />;
  if (tab === "long") {
    return (
      <div className="md-cards">
        <LongCard p={p} />
        <WidthCard p={p} />
        <MoneyCard p={p} />
        <YieldCard p={p} />
      </div>
    );
  }
  if (tab === "rules") {
    return (
      <div className="md-cards">
        <PmccRules p={p} trades={trades} />
      </div>
    );
  }
  if (tab === "recognised") {
    return (
      <div className="md-cards">
        <PositionFacts p={p} />
      </div>
    );
  }
  return (
    <>
      <ShortCheck p={p} />
      <ShortsSold p={p} />
    </>
  );
}

/** A PMCC's id in the URL (`?pm=`), with `:rolls` where a link opens it on the roll candidates. */
export function pmccKey(p: ManagePmccPosition): string {
  return `${p.underlying}-${p.long.symbol}`;
}

function pmccHelp(p: ManagePmccPosition): CardHelpContent {
  const twoOrder = twoOrderRollWords(p);
  return {
    title: "Reading a PMCC",
    body: (
      <>
        {twoOrder ? (
          <HelpWarn title="Possibly a two-order roll">
            <p>{twoOrder}</p>
          </HelpWarn>
        ) : null}
        <HelpSection title="The long">
          <p>Delta today against delta at entry; a deep long keeps its delta, a fading one needs a roll. The dial's band starts at the ruleset's roll floor.</p>
        </HelpSection>
        <HelpSection title="Width check">
          <p>The net cost against the width between the strikes. Past the ruleset's thin line a deep move through the short leaves little to make.</p>
        </HelpSection>
        <HelpSection title="A roll">
          <p>Proposed from today's quotes and sent only for a net credit; check-roll prices it again before you send. Nothing here places an order.</p>
        </HelpSection>
        <GuideLink to="/help/income-trades#step-6-the-pmcc-step" />
      </>
    ),
  };
}

/** The reading of today's short in one line. */
function shortLine(p: ManagePmccPosition): string {
  const s = p.short;
  return `short ${s.leg} · ${s.dte ?? "—"} dte · extrinsic ${px(s.extrinsic)} a share`;
}

/**
 * A PMCC as a parent card: the long's delta as a dial, the width check as a meter, today's short as
 * one line. Shorts sold, the roll candidates, the long and width, the rules and the recognition open
 * from it. A PMCC that needs action carries the action as an amber chip in its head and an action
 * block above its figures.
 */
export function PmccCard({ p, trades, floor = null }: { p: ManagePmccPosition; trades: ManageTrade[]; floor?: number | null }) {
  const k = kindOf(p);
  const acts = useContext(VerdictsHeld) === null ? pmccActions(p, trades) : [];
  const top = acts[0] ?? null;
  const roll = top ? proposedRoll(p) : null;
  const mine = trades.filter((t) => p.tradeIds.includes(t.tradeId));
  const held = mine.find((t) => !QUIET.has(t.action)) ?? mine[0] ?? null;
  const w = p.width;
  const share = w.costShare;
  const dial: ManageBand | null =
    floor !== null && p.long.delta !== null ? { low: floor, high: 1, value: p.long.delta, inside: null } : null;
  const flag = twoOrderRollWords(p) ? "two-order roll" : undefined;
  return (
    <Card
      title={p.recognition === "pmcc" || p.recognition === null ? `${p.underlying} · PMCC` : `${p.underlying} · ${k.kind}`}
      collapseKey={`pmcc-${pmccKey(p)}`}
      className={`pf-cq${top ? " wh-wide" : ""}`}
      help={pmccHelp(p)}
      flag={flag}
      controls={
        top ? (
          <span className="chip chip-warn">{ACTION_LABEL[top.f.action] ?? top.f.action}</span>
        ) : p.recognized === true && held ? (
          <ActionChip action={held.action} />
        ) : (
          <span className={k.chipClass}>{k.kind}</span>
        )
      }
    >
      {p.reconciled === false && (
        <div className="pnl-neg">
          <p style={{ margin: 0 }}>does not reconcile: its money is shown as far as the rows go, not a basis to trade on</p>
          <ul>
            {p.problems.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {top ? (
        <ActionBlock rule={top.f.rule} confidence={top.f.confidence} reason={top.f.reason}>
          {roll ? <RollChain roll={roll} /> : null}
          {p.rolls && p.rolls.candidates.length > 0 ? (
            <BoxButtons>
              <BoxButton id={`${pmccKey(p)}:rolls`} hot>
                roll candidates
              </BoxButton>
            </BoxButtons>
          ) : null}
          {acts.length > 1 ? (
            <p className="muted sm">
              also: {acts.slice(1).map((x) => x.f.rule).join(", ")}
            </p>
          ) : null}
        </ActionBlock>
      ) : null}
      <div className="pf-dwrap">
        <div className="pf-dv">
          {dial ? (
            <BandDial band={dial} unit="long delta today" label="long delta" signedScale={false} reach={1} fmt={(v) => v.toFixed(2)} tick={(v) => v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")} />
          ) : (
            <div className="pf-hero">
              {p.long.delta === null ? "—" : delta(p.long.delta)}
              <span className="pf-unit"> long delta today</span>
            </div>
          )}
        </div>
        <div className="pf-dside">
          <span className="pf-lab">
            width check · {share === null ? "no cost to judge" : `cost ${pct(share)} of the width`} <WidthChip passes={w.passes} thin={w.thin} />
          </span>
          <div className="pf-meter" title="the long's net cost as a share of the strike width">
            <span className={w.passes === false || w.thin ? "is-warn" : ""} style={{ width: `${Math.min(100, Math.max(0, (share ?? 0) * 100))}%` }} />
          </div>
          <div className="pf-tiles pf-tiles-3">
            <Tile label="if assigned, net" value={w.pnlIfAssignedNet === null ? "—" : fmtMoney(w.pnlIfAssignedNet)} tone={w.pnlIfAssignedNet === null ? "dim" : w.pnlIfAssignedNet >= 0 ? "pos" : "neg"} />
            <Tile label="long days left" value={p.long.dte === null ? "—" : String(p.long.dte)} />
            <Tile label="delta at entry" value={p.long.deltaAtEntry === null ? "—" : delta(p.long.deltaAtEntry)} />
          </div>
        </div>
      </div>
      <ul className="lk-list">
        <li className="lk-li lk-li-flat">
          <span className="lk-leg">{shortLine(p)}</span>
          <TestedChip tested={p.short.tested} />
        </li>
      </ul>
      <BoxButtons>
        <BoxButton id={pmccKey(p)}>shorts sold</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/* ------------------------------------------------------------------ the lightbox */

function PmccDialog({ ps, trades, index, open, onStep, onClose }: { ps: ManagePmccPosition[]; trades: ManageTrade[]; index: number; open: string; onStep: (to: number) => void; onClose: () => void }) {
  const p = ps[index]!;
  const tabs = pmccTabs(p);
  const wanted = open.endsWith(":rolls") && tabs.some((t) => t.id === "rolls") ? "rolls" : "shorts";
  const [state, setTab] = useState<{ id: string; tab: PmccTab }>({ id: open, tab: wanted });
  // A step to another PMCC, or a new link, opens on its own first tab.
  const tab = state.id === open ? state.tab : wanted;
  const k = kindOf(p);
  return (
    <CardLightbox count={ps.length} index={index} onStep={onStep} onClose={onClose} wide>
      {(nav) => (
        <LightboxFrame
          kicker="pmcc ›"
          title={`${p.underlying} · ${p.long.leg} / ${p.short.leg}`}
          chips={<span className={k.chipClass}>{k.kind}</span>}
          tabs={tabs}
          tab={tab}
          onTab={(t) => setTab({ id: open, tab: t })}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          <div className="pf-box-body">
            <PmccBody p={p} trades={trades} tab={tab} />
          </div>
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The provider and the open lightbox around one account's PMCC cards (the portfolio tab's context,
 *  so the buttons are the same buttons). */
function PmccBoxes({ a, children }: { a: ManageAccount; children: ReactNode }) {
  const lb = useOpenParam("pm");
  // Only what the tab lists steps in the lightbox: the time spreads open on the positions board.
  const ps = splitPmccs(a.pmcc?.positions ?? []).pmccs;
  const ids = new Set(ps.map(pmccKey));
  const base = (id: string) => id.replace(/:rolls$/, "");
  const index = lb.open === null ? -1 : ps.findIndex((p) => pmccKey(p) === base(lb.open!));
  return (
    <PortfolioBoxContext.Provider value={{ open: lb.show, has: (id) => ids.has(base(id)) }}>
      {children}
      {index >= 0 ? <PmccDialog ps={ps} trades={a.trades} index={index} open={lb.open!} onStep={(to) => lb.step(ps[to] ? pmccKey(ps[to]!) : "")} onClose={lb.close} /> : null}
    </PortfolioBoxContext.Provider>
  );
}

/** The ruleset's delta floor for a PMCC's long (`pmcc.roll_long.min_delta`), where the evaluation embeds one. */
export function longDeltaFloor(rules: Record<string, unknown> | null | undefined): number | null {
  const pmcc = rules?.["pmcc"];
  const roll = pmcc && typeof pmcc === "object" ? (pmcc as Record<string, unknown>)["roll_long"] : null;
  const v = roll && typeof roll === "object" ? (roll as Record<string, unknown>)["min_delta"] : null;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** One account's PMCCs: the ones that need action first, then each PMCC as a parent card, or a plain
 *  statement of why there is none. */
export function PmccCards({ a: evaluated, rules = null }: { a: ManageAccount; rules?: Record<string, unknown> | null }) {
  // The spot and both legs' mids on live quotes where they are fresh (liveLegs.ts); the evaluation's otherwise.
  const a = livePmccs(evaluated, useLiveMarks()?.payload ?? null);
  const v = a.pmcc;
  if (v === null) {
    return (
      <Card title={`pmcc · ${accountTitle(a)}`}>
        <p className="muted" style={{ margin: 0 }}>
          this evaluation was written before the PMCC view: it has no PMCC for the account
        </p>
      </Card>
    );
  }
  const notes = [
    v.underlyingHistoryError ? `deltas at entry: ${v.underlyingHistoryError}` : null,
    v.bandsMissing ? `skate bands: ${v.bandsMissing}` : null,
  ].filter((x): x is string => x !== null);
  const { pmccs, spreads } = splitPmccs(v.positions);
  if (pmccs.length === 0) {
    return (
      <>
        <Card title={`pmcc · ${accountTitle(a)}`}>
          <p className="muted" style={{ margin: 0 }}>
            {noPmccText(v.recognize)}
          </p>
          {v.error && <p className="muted">no PMCC view for this account: {v.error}</p>}
          {notes.map((n) => (
            <p key={n} className="muted">
              {n}
            </p>
          ))}
        </Card>
        <TimeSpreadStrip spreads={spreads} />
      </>
    );
  }
  return <PmccLayout a={a} pmccs={pmccs} spreads={spreads} floor={longDeltaFloor(rules)} notes={notes} />;
}

/**
 * The tab holds PMCCs and the candidates manage has not decided yet (2026-11-10). A call time spread
 * manage decided is not a PMCC (a calendar, or a diagonal whose long was bought too shallow) is judged
 * by the time-spread rules, so it lives on the positions board; the tab names it in one line.
 */
export function splitPmccs(positions: ManagePmccPosition[]): { pmccs: ManagePmccPosition[]; spreads: ManagePmccPosition[] } {
  return {
    pmccs: positions.filter((p) => p.recognition !== "diagonal"),
    spreads: positions.filter((p) => p.recognition === "diagonal"),
  };
}

/** "calendar" or "diagonal" from manage; an evaluation written before the field says "diagonal". */
export function structureOf(p: ManagePmccPosition): string {
  return p.structure ?? "diagonal";
}

/** The call time spreads that are not PMCCs, in one line that opens them on the positions board. */
export function TimeSpreadStrip({ spreads }: { spreads: ManagePmccPosition[] }) {
  if (spreads.length === 0) return null;
  const n = spreads.length;
  return (
    <p className="pf-hint">
      <span className="chip muted">not PMCCs</span>
      <span>
        <strong>
          {n} call time spread{n === 1 ? " is" : "s are"} not {n === 1 ? "a PMCC" : "PMCCs"}
        </strong>{" "}
        · {spreads.map((p) => `${p.underlying} ${structureOf(p)}`).join(", ")} · judged as time spreads, not by the PMCC rules ·{" "}
        <Link to="/live/positions?show=spreads">open on positions ›</Link>
      </span>
    </p>
  );
}

/** One PMCC as a row: the same figures its card leads with, and the card itself when it opens. */
function pmccRow(p: ManagePmccPosition, trades: ManageTrade[], floor: number | null): CardRow {
  const k = kindOf(p);
  const mine = trades.filter((t) => p.tradeIds.includes(t.tradeId));
  const pnl = mine.length && mine.every((t) => t.pnlNet !== null) ? mine.reduce((s, t) => s + (t.pnlNet ?? 0), 0) : null;
  const w = p.width;
  return {
    key: pmccKey(p),
    symbol: p.underlying,
    shape: `${k.kind} · ${p.long.leg} / ${p.short.leg}`,
    cash: pnl,
    label: `width ${w.passes === null ? "—" : w.passes ? "holds" : "fails"}${w.costShare === null ? "" : ` · cost ${pct(w.costShare)} of it`}`,
    after: `long ${p.long.delta === null ? "—" : delta(p.long.delta)} delta · short ${p.short.dte ?? "—"} dte`,
    chips: <span className={k.chipClass}>{k.kind}</span>,
    card: <PmccCard p={p} trades={trades} floor={floor} />,
  };
}

function PmccLayout({
  a,
  pmccs,
  spreads,
  floor,
  notes,
}: {
  a: ManageAccount;
  pmccs: ManagePmccPosition[];
  spreads: ManagePmccPosition[];
  floor: number | null;
  notes: string[];
}) {
  const held = useContext(VerdictsHeld);
  const [view, setView] = useViewChoice("pmcc");
  const needs = held === null ? pmccs.filter((p) => pmccActions(p, a.trades).length > 0) : [];
  const rest = pmccs.filter((p) => !needs.includes(p));
  return (
    <PmccBoxes a={a}>
      <div className="pf-layout">
        {needs.length > 0 ? (
          <Section concept="risk" tag="needs action" question="Which PMCC needs action?">
            {needs.map((p) => (
              <PmccCard key={pmccKey(p)} p={p} trades={a.trades} floor={floor} />
            ))}
          </Section>
        ) : null}
        {rest.length > 0 ? (
          <Section concept="dir" tag="each pmcc" question="Is each PMCC healthy?">
            <div className="pos-controls">
              <p className="muted" style={{ margin: 0 }}>
                {a.account ?? "?"} · {pmccs.length} {pmccs.length === 1 ? "PMCC" : "PMCCs"}
                {notes.length ? ` · ${notes.join(" · ")}` : ""}
              </p>
              <span className="pos-tools">
                <ViewSwitch view={view} onChange={setView} />
              </span>
            </div>
            {view === "rows" ? (
              <CardRows rows={rest.map((p) => pmccRow(p, a.trades, floor))} />
            ) : (
              rest.map((p) => <PmccCard key={pmccKey(p)} p={p} trades={a.trades} floor={floor} />)
            )}
          </Section>
        ) : null}
        <TimeSpreadStrip spreads={spreads} />
      </div>
    </PmccBoxes>
  );
}
