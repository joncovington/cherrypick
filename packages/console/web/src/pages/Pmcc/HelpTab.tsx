import type { PmccPayload } from "@console/shared";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";

/**
 * What this experiment is, in the module's own terms.
 *
 * Deliberately not the shared ExperimentGuideView: that component reads a flies/meic-shaped config
 * block and derives each arm's differences from its siblings, and PMCC's arms differ by lifecycle
 * (a long re-bought each cycle against one held for a year), which a derived key diff would report
 * as noise. The prose here follows the config's own notes and packages/pmcc/CLAUDE.md, rewritten
 * for the 2026-10-05 shield boundary.
 */
export function HelpTab({ data }: { data: PmccPayload | undefined }) {
  const p = data?.params;
  const settlementStyle = p?.settlementStyle ?? {};
  const symbols = p?.symbols ?? [];
  const physicalSymbols = symbols.filter((s) => settlementStyle[s] === "physical");
  const cashSymbols = symbols.filter((s) => settlementStyle[s] === "cash");
  return (
    <div className="cards cards-wide">
      <Card title="what PMCC is" collapseKey="pmcc-help-what" defaultCollapsed className="view-fade">
        <div className="pmcc-prose">
          <p>
            Hold a deep in-the-money call as a stock substitute and sell a shorter-dated call against it, collecting
            the short's time value. The module runs that idea three ways at once, each its own portfolio on the same
            symbols, so the difference between them is the measurement.
          </p>
          <p>
            By put-call parity a deep call plus a short in-the-money weekly call is a short weekly put plus a far
            out-of-the-money long put: the position earns the variance premium on the weekly, with a crash floor far
            below. Whether that premium is there is exactly what the short leg tests; the replay behind these arms
            found it turns on the implied-volatility level (packages/pmcc/docs/shield-study.md).
          </p>
          <p className="muted">
            {symbols.length > 0 ? symbols.join(", ") : "No symbols configured"} — one position per symbol and arm
            at a time, every symbol its own population, never pooled. Paper only: there is no live loop and no
            order-placement code anywhere in the module.
          </p>
        </div>
      </Card>

      <Card title="the arms" collapseKey="pmcc-help-arms" defaultCollapsed>
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            <dt>control</dt>
            <dd>
              Buy a call inside an 85-90-delta band
              {p?.longDeltaMin != null && p?.longDeltaMax != null && (
                <> ({fmtPct(p.longDeltaMin * 100, 0)}–{fmtPct(p.longDeltaMax * 100, 0)})</>
              )}{" "}
              at ~21 DTE, sell the call nearest spot at ~7 DTE, hold to the short's expiration and close both. The long
              is re-bought every cycle, which is where most of its cost goes.
            </dd>
            <dt>shield</dt>
            <dd>
              Tom King's "Income Shield" (from 2026-10-05): hold a ~1-year call at 0.90-0.95 delta and sell a
              0.70-delta weekly call against it, rolled each Friday an hour before the close, until the long reaches 45
              DTE or the position loses 30% of the long's cost. Also rolls early once the short's extrinsic is 85%
              decayed or spot reaches its strike, at most once a session.
            </dd>
            <dt>shield_hold</dt>
            <dd>
              The same entry as shield, on the same days, holding each short to Friday. The pair isolates the early
              roll.
            </dd>
            <dt>advised:&lt;experiment name&gt;</dt>
            <dd>
              One arm per advisor experiment, shadowing control with that experiment's params frozen at entry. The one
              thing currently worth advising is <span className="mono">tv_managed_exit</span>/
              <span className="mono">tv_close_threshold</span>
              {p?.tvCloseThreshold != null && <> (threshold ≈{fmtMoney(p.tvCloseThreshold)})</>} — the early
              time-value exit, as a paper A/B against hold-to-expiry
              {p?.tvManagedExit === true && (
                <span className="chip chip-warn integrity-chip" style={{ marginLeft: 6 }}>
                  on in this config's defaults
                </span>
              )}
              . Off by default.
            </dd>
          </dl>
          <p className="muted">
            A shield position closes about ten months after it opens, so the closed-results table shows nothing from
            it for most of a year. Read the shield arms on the arms page's "open, marked to market" and "weekly by
            arm", and one position at a time on the tracker.
          </p>
        </div>
      </Card>

      <Card title="the honesty rules" collapseKey="pmcc-help-honesty" defaultCollapsed>
        <div className="pmcc-prose">
          <ol className="pmcc-rules">
            <li>
              <strong>Every result is net of the modeled fee and slippage stack</strong> — commissions, clearing,
              ORF/TAF, the per-ITM-symbol settlement event, and the pass-throughs on the share side of an
              assignment. Gross is not a result.
            </li>
            <li>
              <strong>
                For physical-settlement symbols{physicalSymbols.length > 0 && <> ({physicalSymbols.join(", ")})</>},
                early assignment is unmodelled but measured, so the paper result is an upper bound.
              </strong>{" "}
              Every mark where the short's extrinsic sits under the exposure threshold
              {p?.assignmentExposureTv != null && <> ({fmtMoney(p.assignmentExposureTv)})</>} is flagged. That
              share bounds what the unmodelled mechanism could have touched — read it beside the net, always.{" "}
              {cashSymbols.length > 0 && (
                <>
                  Cash-settled symbols ({cashSymbols.join(", ")}) are European-exercise — there is no early
                  assignment to bound, so this telemetry is exempt for them and their net carries no upper-bound
                  caveat.
                </>
              )}
            </li>
            <li>
              <strong>Ex-dividend spans are refused, not modelled — for physical-settlement symbols only.</strong> A
              short leg on a physical-settlement symbol spanning a declared ex-date is refused; so is a span the
              declared calendar cannot answer for. A lapsed table halts entries loudly, by design — a missing
              calendar and "no dividend" must never look alike. Cash-settled, European-exercise symbols
              {cashSymbols.length > 0 && <> ({cashSymbols.join(", ")})</>} skip this check entirely: there is no
              early-exercise mechanism for a dividend to trigger.
            </li>
            <li>
              <strong>Rules are declared up front and measured, never tuned mid-experiment.</strong> A removed rule
              keeps its negative result on the record.
            </li>
            <li>
              <strong>A hole in the mark path is refused, never zero.</strong> A refused mark is still a row: a
              stalled feed and a quiet market must never look identical.
            </li>
          </ol>
        </div>
      </Card>

      <Card title="settlement, by symbol" collapseKey="pmcc-help-settlement" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            Physical-settlement symbols{physicalSymbols.length > 0 && <> ({physicalSymbols.join(", ")})</>} are
            American delivery. An ITM short call at expiry books its intrinsic value <em>and</em> delivers 100 short
            shares per contract at the settlement print. Shares are booked at the settlement spot rather than the
            strike, which keeps the option accounting untouched.
          </p>
          <p>
            For control the surviving long stays open and the next session's combined disposal covers the shares and
            sells it; the position does not close while shares are outstanding (the{" "}
            <span className="mono">short_settled</span> state), and the Friday-to-Monday gap is left visible because
            it <em>is</em> the weekend exposure. A shield position keeps its long; under the module's{" "}
            <span className="mono">ira</span> account policy every expiring short is bought back before the bell, so a
            delivery means a buyback failed.
          </p>
          {cashSymbols.length > 0 && (
            <p>
              Cash-settled symbols ({cashSymbols.join(", ")}) are European: they can only be exercised at their own
              expiration, so there is no delivered-share disposal and no weekend share-carry exposure for them.
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
