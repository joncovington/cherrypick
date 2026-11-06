import type { ManageAccount, ManagePmccPosition, ManagePmccRecognize, ManagePmccRoll, ManageTrade } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Cells, HeadRow, useColumns, type Column } from "../../components/columns";
import { fmtCash, fmtMoney, fmtPrice } from "../../lib/format";
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
  feesPaid,
  px,
  etStamp,
  share,
} from "./manageParts";

/**
 * Live → pmcc: each poor man's covered call packages/manage reads on the account (`pmcc.py`,
 * docs/skate-plan.md step 6), one card per name whose calls have a PMCC's shape, and the PMCC rules
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
      {p.rolls && (
        <>
          <p className="md-subhead">
            rolls for the {p.short.leg}
            <span className="muted"> · priced on {pmccRollQuotes(p.rolls)}</span>
          </p>
          {p.rolls.candidates.length === 0 ? (
            <p className="md-note">no roll priced: {p.rolls.why ?? "nothing quoted"}</p>
          ) : (
            <ul className="md-rules">
              {p.rolls.candidates.map((r, i) => (
                <RollLine key={i} r={r} />
              ))}
            </ul>
          )}
        </>
      )}
    </DetailCard>
  );
}

/** Today's check on the short, in the wheel's columns where they mean the same, then the PMCC's own. */
function ShortCheck({ p }: { p: ManagePmccPosition }) {
  const s = p.short;
  const earnings =
    s.earnings.inside === null
      ? (s.earnings.why ?? "—")
      : `${s.earnings.date ?? "?"}: ${s.earnings.inside ? "inside its life" : "after it expires"}`;
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

export function PmccCard({ p, trades }: { p: ManagePmccPosition; trades: ManageTrade[] }) {
  const k = kindOf(p);
  return (
    <Card
      title={
        <>
          {`${p.underlying} · ${p.long.leg} / ${p.short.leg} `}
          <span className={k.chipClass}>{k.kind}</span>
        </>
      }
      collapseKey={`pmcc-${p.underlying}-${p.long.symbol}`}
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
      <div className="md-cards">
        <PositionFacts p={p} />
        <LongCard p={p} />
        <WidthCard p={p} />
        <MoneyCard p={p} />
        <YieldCard p={p} />
        <PmccRules p={p} trades={trades} />
      </div>
      <ShortCheck p={p} />
      <ShortsSold p={p} />
    </Card>
  );
}

/** One account's PMCCs: a card per name, or a plain statement of why there is none. */
export function PmccCards({ a }: { a: ManageAccount }) {
  const v = a.pmcc;
  if (v === null) {
    return (
      <Card title={`pmcc · ${a.account ?? "?"}`}>
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
  if (v.positions.length === 0) {
    return (
      <Card title={`pmcc · ${a.account ?? "?"}`}>
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
    );
  }
  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        {a.account ?? "?"} · {v.positions.length} {v.positions.length === 1 ? "name" : "names"} with a PMCC's shape
        {notes.length ? ` · ${notes.join(" · ")}` : ""}
      </p>
      {v.positions.map((p) => (
        <PmccCard key={`${p.underlying}-${p.long.symbol}`} p={p} trades={a.trades} />
      ))}
    </>
  );
}
