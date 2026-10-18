import type { ManageCandidate, ManageCandidates } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtCash, fmtMoney } from "../../lib/format";

/**
 * Income trades from our own universe, sized to the account (packages/manage `candidates.py`): the
 * ones that fit, with their quantity and the cap the next unit would break, and the ones that do not,
 * with the cap that stopped them and the narrowed version where one fits. Every figure is the
 * package's, in its order (theta per dollar of buying power): a ranking the server made is not
 * re-sorted here. Money follows the suite layout: a credit received is +, whole-position dollars,
 * the one per-share figure is the net price and says so. Priced at mid from the previous afternoon's
 * quotes, and the card says to re-quote before sending.
 */

const KIND: Record<string, string> = {
  short_put: "short put",
  put_spread: "put credit spread",
  call_spread: "call credit spread",
  covered_call: "covered call",
};

const BASIS: Record<string, string> = {
  cash: "a cash account or IRA (puts cash-secured)",
  margin: "a margin account (Reg T)",
  portfolio_margin: "portfolio margin (buying power not modelled)",
};

function legs(c: ManageCandidate): string {
  return c.legs.map((l) => `${l.quantity > 0 ? "+" : "−"}${l.strike}${l.right}`).join(" / ");
}

function price(v: number | null): string {
  return v === null ? "—" : `${Math.abs(v).toFixed(2)} ${v >= 0 ? "cr" : "db"}`;
}

function times(n: number, v: number | null): number | null {
  return v === null ? null : n * v;
}

/** The loss the per-trade cap was applied to: a short put's stress loss, a spread's max loss (the
 *  package records it as both). A covered call's risk is the shares already held. */
function risk(c: ManageCandidate, n: number): string {
  return c.kind === "covered_call" ? "covered" : fmtMoney(times(n, c.perUnit.stressLoss));
}

function narrowed(c: ManageCandidate): string | null {
  if (!c.alternativeTo) return null;
  return c.alternativeTo === c.kind ? "narrowed to fit" : `narrowed from the ${KIND[c.alternativeTo] ?? c.alternativeTo}`;
}

function FitsTable({ rows }: { rows: ManageCandidate[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>name</th>
            <th>trade</th>
            <th>legs</th>
            <th title="contracts (spreads: one of each leg)">qty</th>
            <th title="per-share net price at mid, and the natural fill (sell at the bid, buy at the ask)">price</th>
            <th title="signed cash flow, whole position: a credit received is +">cash</th>
            <th title="a spread's max loss; a short put's loss at the name's 1st-percentile move over the holding period">risk</th>
            <th title="buying power the position ties up">buying power</th>
            <th title="theta, whole position, dollars a day">θ / day</th>
            <th title="beta-weighted delta the position adds, in SPY shares">Δ SPY sh</th>
            <th>earnings</th>
            <th title="the cap the next contract would break">next stops at</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={`${c.underlying}-${c.kind}-${i}`}>
              <td>{c.underlying}</td>
              <td>
                {KIND[c.kind] ?? c.kind}
                {narrowed(c) ? <span className="muted"> · {narrowed(c)}</span> : null}
              </td>
              <td className="num">
                {c.expiry} {legs(c)}
              </td>
              <td className="num">{c.units}</td>
              <td className="num" title={`natural ${price(c.perUnit.natural)}`}>
                {price(c.perUnit.credit)}
              </td>
              <td className="num pnl-pos">{fmtCash(times(c.units, c.perUnit.cash))}</td>
              <td className="num">{risk(c, c.units)}</td>
              <td className="num">{fmtMoney(times(c.units, c.perUnit.buyingPower))}</td>
              <td className="num">{fmtCash(times(c.units, c.perUnit.theta))}</td>
              <td className="num">{c.perUnit.bwd === null ? "—" : (c.units * c.perUnit.bwd).toFixed(1)}</td>
              <td>{c.earningsUnknown ? <span className="muted">unknown</span> : c.earningsDate ?? "—"}</td>
              <td className="muted">{c.binding ?? "max contracts"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TooBig({ rows, total }: { rows: ManageCandidate[]; total: number }) {
  if (rows.length === 0) return null;
  return (
    <details className="pf-too-big">
      <summary>
        {total} that do not fit at even one contract{total > rows.length ? ` (the best ${rows.length} shown)` : ""}
      </summary>
      <ul>
        {rows.map((c, i) => (
          <li key={`${c.underlying}-${c.kind}-${i}`}>
            <strong>{c.underlying}</strong> {KIND[c.kind] ?? c.kind} {legs(c)} · stopped by <em>{c.binding ?? "—"}</em>
            <span className="muted">
              {" "}
              (one ties up {fmtMoney(c.perUnit.buyingPower)}, risks {risk(c, 1)})
            </span>
            {c.alternative && (
              <span>
                {" "}
                → narrowed to {legs(c.alternative)}:{" "}
                {c.alternative.units > 0 ? `fits ${c.alternative.units}` : `still stopped by ${c.alternative.binding ?? "—"}`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function IncomeCard({ c }: { c: ManageCandidates | null }) {
  if (!c) return null;
  const excluded = Object.entries(c.excluded)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  return (
    <Card title="income trades, sized to the account">
      {c.error ? (
        <p className="muted">{c.error}</p>
      ) : (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            from our own universe ({c.names ?? "—"} tradable names) on the cycle monthly, priced at mid from the {c.sourceSession ?? "—"}{" "}
            afternoon quotes: re-quote before sending · sized for {BASIS[c.accountBasis ?? ""] ?? "an account of unknown kind"}
            {excluded ? ` · left out: ${excluded}` : ""}
          </p>
          {c.fits.length > 0 ? (
            <FitsTable rows={c.fits} />
          ) : (
            <p className="muted">nothing fits today: every candidate is stopped by a cap below</p>
          )}
          {c.fitsTotal > c.fits.length && (
            <p className="muted pf-caption">the best {c.fits.length} of {c.fitsTotal} that fit, by theta per dollar of buying power</p>
          )}
          <TooBig rows={c.tooBig} total={c.tooBigTotal} />
        </>
      )}
      <p className="muted pf-caption">
        caps: one trade's risk 3% of net liq · a short put's strike 25% of net liq (cash) · buying power under the VIX cap · delta
        in band · theta under its ceiling · no name over a quarter of the theta the account may carry. Retail heuristics, modelled
        at mid: nothing here is placed.
      </p>
    </Card>
  );
}
