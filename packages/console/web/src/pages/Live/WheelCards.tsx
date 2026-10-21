import type { ReactNode } from "react";
import type {
  ManageAccount,
  ManageTrade,
  ManageWheelCheck,
  ManageWheelCycle,
  ManageWheelNote,
  ManageWheelRoll,
  ManageWheelRolls,
} from "@console/shared";
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
  etStamp,
  feesPaid,
  px,
  share,
  strike,
  yesNo,
} from "./manageParts";

/**
 * Live → wheel: each wheel cycle packages/manage follows on the account (`wheel.py`, docs/skate-plan.md
 * step 5), one card per cycle, and the wheel rules that fired on the trades holding its legs.
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

function TodayCheck({ rows }: { rows: ManageWheelCheck[] }) {
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
              <th>extrinsic</th>
              <th>skate band</th>
              <th>strike vs band</th>
              <th>resting buy-to-close</th>
              <th>mark at or under it</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol}>
                <td>
                  {r.quantity ?? "?"} {r.leg}
                </td>
                <td>{r.dte ?? "—"}</td>
                <td>{px(r.spot)}</td>
                <td>{px(r.mid)}</td>
                <td>
                  <TestedChip tested={r.tested} />
                </td>
                <td>{px(r.intrinsic)}</td>
                <td>
                  {px(r.extrinsic)} a share · {fmtMoney(r.extrinsicDollars)}
                </td>
                <td style={{ whiteSpace: "normal" }}>{bandText(r)}</td>
                <td>{beyondText(r)}</td>
                <td>
                  {r.restingBtc.length === 0
                    ? "none resting"
                    : r.restingBtc.map((o) => `${px(o.price)} × ${o.quantity ?? "?"}${o.timeInForce ? ` ${o.timeInForce}` : ""}`).join(", ")}
                </td>
                <td>{r.btcPrice === null ? "—" : yesNo(r.markAtOrUnderBtc, "at or under", "above")}</td>
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

export function CycleCard({ c, trades }: { c: ManageWheelCycle; trades: ManageTrade[] }) {
  return (
    <Card title={`${c.underlying} · wheel from ${c.start ?? "?"}`} collapseKey={`wheel-${c.underlying}-${c.start ?? ""}`}>
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
      <div className="md-cards">
        <CycleFacts c={c} />
        <CycleMoney c={c} />
        <CyclePnl c={c} />
        <CycleYield c={c} />
        <WheelRules c={c} trades={trades} />
      </div>
      {c.today.length > 0 && <TodayCheck rows={c.today} />}
      <Events c={c} />
    </Card>
  );
}

export const NO_WHEEL = "no wheel running: a wheel starts with a short put sold alone or shares held";

/** One account's wheel: a card per cycle, or a plain statement of why there is none. */
export function WheelCards({ a }: { a: ManageAccount }) {
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
          <p className="muted" style={{ margin: 0 }}>
            {a.account ?? "?"} · {w.cycles.length} cycle{w.cycles.length === 1 ? "" : "s"}
            {w.bandsMissing ? ` · skate bands: ${w.bandsMissing}` : w.bandsDay ? ` · skate bands from ${w.bandsDay}` : ""}
          </p>
          {w.cycles.map((c) => (
            <CycleCard key={`${c.underlying}-${c.start ?? ""}`} c={c} trades={a.trades} />
          ))}
        </>
      )}
      {flagged.length > 0 && (
        <Card title="deliveries nothing explains">
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
        <Card title="history outside any cycle">
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
