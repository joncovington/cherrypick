import type { ManageAccount, ManagePmccPosition, ManagePmccRecognize, ManagePmccRoll, ManageTrade } from "@console/shared";
import { Card } from "../../components/DataTable";
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
  const long = rc?.minLongDays != null ? `at least ${rc.minLongDays} days` : "enough days";
  const short = rc?.maxShortDays != null ? `at most ${rc.maxShortDays} days` : "few enough days";
  return (
    `${NO_PMCC} a long call entered at ${delta} delta or more, with ${long} left when it was ` +
    `bought (or a name given intent: "pmcc" in the ruleset), has one shorter-dated call sold against it with ${short} ` +
    "left, at or above the long's strike, and no shares on the name"
  );
}

function pct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function delta(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

/** Manage's width verdict. Null is "no cost to judge it against", never a pass. */
export function WidthChip({ passes }: { passes: boolean | null }) {
  if (passes === null) return <span className="chip muted">unknown</span>;
  return passes ? <span className="chip">passes</span> : <span className="chip chip-warn">fails</span>;
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

function PositionFacts({ p }: { p: ManagePmccPosition }) {
  return (
    <DetailCard title="position" aside={<StatusChip status={status(p)} />}>
      <Kv
        rows={[
          ["recognised", p.recognized === null ? "—" : p.recognized ? "yes" : "no, a diagonal"],
          ["because", p.recognizedBy ?? "—"],
          ["underlying", px(p.spot)],
          ["long bought", p.long.opened ?? "before the history"],
          ["trades", p.tradeIds.length ? p.tradeIds.join(", ") : "none"],
        ]}
      />
    </DetailCard>
  );
}

function LongCard({ p }: { p: ManagePmccPosition }) {
  const l = p.long;
  return (
    <DetailCard
      title="the long"
      aside={l.roll.length ? <span className="chip chip-warn">roll the long</span> : <span className="chip">no roll due</span>}
    >
      <Kv
        rows={[
          ["leg", `${l.quantity ?? "?"} ${l.leg}`],
          [
            "delta at entry",
            l.deltaAtEntry === null ? `delta at entry unknown${l.deltaAtEntryWhy ? ` (${l.deltaAtEntryWhy})` : ""}` : delta(l.deltaAtEntry),
          ],
          ["delta today", l.delta === null ? "unknown" : delta(l.delta)],
          ["mid", share(l.mid)],
          ["intrinsic", share(l.intrinsic)],
          ["extrinsic", `${share(l.extrinsic)} (${pct(l.extrinsicShare)} of its price)`],
          ["days left", l.dte === null ? "—" : String(l.dte)],
          ["roll", l.roll.length ? l.roll.join("; ") : "none due"],
        ]}
      />
      {l.rollDeltaUnknown ? <p className="md-note">today's delta is unknown, so the delta floor was not checked</p> : null}
    </DetailCard>
  );
}

function WidthCard({ p }: { p: ManagePmccPosition }) {
  const w = p.width;
  const openLegs = w.basis === "open_legs";
  return (
    <DetailCard title="width check" aside={<WidthChip passes={w.passes} />}>
      <Kv
        rows={[
          ["strike width", share(w.width)],
          ["long's net cost after premiums", `${fmtMoney(w.netCost)} (${share(w.netCostPerShare)})`],
          ["P&L if assigned, gross", <Money key="g" v={w.pnlIfAssigned} />],
          ["P&L if assigned, net of fees", w.pnlIfAssignedNet === null ? (openLegs ? "n/r: the orders do not reconcile" : "—") : <Money key="n" v={w.pnlIfAssignedNet} />],
          ["cost from", openLegs ? "the open legs' prices (earlier shorts not counted)" : (w.basis ?? "—")],
        ]}
      />
      {w.assumes ? <p className="md-note">assumes {w.assumes}</p> : null}
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
  return (
    <>
      <p className="md-subhead">today's check</p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>short</th>
              <th>dte</th>
              <th>underlying</th>
              <th>mid (a share)</th>
              <th>tested</th>
              <th>intrinsic (a share)</th>
              <th>extrinsic (a share)</th>
              <th>skate band</th>
              <th>strike vs band</th>
              <th>assignment</th>
              <th>N(d2)</th>
              <th>earnings</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                {s.quantity ?? "?"} {s.leg}
              </td>
              <td>{s.dte ?? "—"}</td>
              <td>{px(p.spot)}</td>
              <td>{px(s.mid)}</td>
              <td>
                <TestedChip tested={s.tested} />
              </td>
              <td>{px(s.intrinsic)}</td>
              <td>{px(s.extrinsic)}</td>
              <td style={{ whiteSpace: "normal" }}>{bandText(s)}</td>
              <td>{beyondText(s)}</td>
              <td style={{ whiteSpace: "normal" }}>
                <ExposureChip exposed={s.assignment.exposed} />
                {s.assignment.why ? <span className="muted"> {s.assignment.why}</span> : null}
              </td>
              <td>{pct(s.probItm)}</td>
              <td style={{ whiteSpace: "normal" }}>{earnings}</td>
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
  return (
    <Card title={`${p.underlying} · PMCC ${p.long.leg} / ${p.short.leg}`} collapseKey={`pmcc-${p.underlying}-${p.long.symbol}`}>
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
