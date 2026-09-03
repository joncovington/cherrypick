import type { PmccPayload } from "@console/shared";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";

/**
 * What this experiment is, in the module's own terms.
 *
 * Deliberately not the shared ExperimentGuideView: that component reads a flies/meic-shaped config
 * block and derives each arm's differences from its siblings. PMCC's single book plus its advised
 * twin differ by one frozen overlay, not by a set of declared arms, so a derived diff would report
 * almost nothing. The prose here is the config's own `_what_this_is`/`_selection_note`/
 * `_management_note`, kept in one place. Rewritten for the 2026-08-23 redesign — see
 * packages/pmcc/CLAUDE.md's measurement-break note for what changed and why.
 */
export function HelpTab({ data }: { data: PmccPayload | undefined }) {
  const p = data?.params;
  const settlementStyle = p?.settlementStyle ?? {};
  const symbols = p?.symbols ?? [];
  const physicalSymbols = symbols.filter((s) => settlementStyle[s] === "physical");
  const cashSymbols = symbols.filter((s) => settlementStyle[s] === "cash");
  return (
    <div className="cards cards-wide">
      <Card title="what PMCC-99 is" collapseKey="pmcc-help-what" defaultCollapsed className="view-fade">
        <div className="pmcc-prose">
          <p>
            Buy a call inside an 85-90-delta band
            {p?.longDeltaMin != null && p?.longDeltaMax != null && (
              <> ({fmtPct(p.longDeltaMin * 100, 0)}–{fmtPct(p.longDeltaMax * 100, 0)})</>
            )}{" "}
            at ~21 DTE — a stock substitute, deliberately <em>not</em> a LEAP — and sell the ATM call nearest spot at
            ~7 DTE, whichever side of spot it lands on. There is no yield search on the short any more: it is simply
            the nearest strike, so it can land OTM as easily as ITM.
          </p>
          <p>
            The default exit <strong>holds to the short's own expiration</strong>, then closes both legs together and
            re-enters — no more early close on time-value exhaustion by default. That earlier rule survives as{" "}
            <span className="mono">tv_managed_exit</span>
            {p?.tvCloseThreshold != null && <> (threshold ≈{fmtMoney(p.tvCloseThreshold)})</>}, a live,
            advisor-tunable override read only through the <span className="mono">advised:control</span> book's
            frozen params
            {p?.tvManagedExit === true && (
              <span className="chip chip-warn integrity-chip" style={{ marginLeft: 6 }}>
                on in this config's defaults
              </span>
            )}
            .
          </p>
          <p className="muted">
            {symbols.length > 0 ? symbols.join(", ") : "One symbol"} since the 2026-08-23 redesign, one position per
            symbol at a time — TQQQ (American, physical-settlement) and XSP (Mini-SPX, European, cash-settled) added
            the same day, run as separate populations under the identical rule set. Paper only: there is no live
            loop and no order-placement code anywhere in the module.
          </p>
        </div>
      </Card>

      <Card title="one book, plus its advised twin" collapseKey="pmcc-help-books" defaultCollapsed>
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            <dt>control</dt>
            <dd>
              The strategy as taught: mechanical entry whenever the slot is free, an 85-90-delta long, an ATM short
              with no yield floor, hold to the short's own expiration, then close both legs together. Never rolls —
              there is no more roll book.
            </dd>
            <dt>advised:control</dt>
            <dd>
              The AI advisor's admitted params, frozen on each row at entry and restated every tick through the
              module's one choke point. The one thing currently worth advising is{" "}
              <span className="mono">tv_managed_exit</span>/<span className="mono">tv_close_threshold</span> —
              flipping the exit rule back to early-tv-exhaustion, as a paper A/B against hold-to-expiry. Off by
              default.
            </dd>
          </dl>
          <p className="muted">
            There is no more multi-book fill pairing to reason about: with one book plus its advised twin, every{" "}
            <span className="mono">control</span> cycle is directly comparable to every other{" "}
            <span className="mono">control</span> cycle.
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
            TQQQ is American physical delivery. An ITM short call at expiry books its intrinsic <em>and</em> delivers
            100 short shares per contract at the settlement print; the surviving ~14-DTE long stays open, and the
            next session's combined disposal covers the shares and sells the long.
          </p>
          <p>
            A position does not close while its shares are outstanding — that is the{" "}
            <span className="mono">short_settled</span> state on this page — and the Friday-to-Monday gap is left
            visible, because it <em>is</em> the weekend exposure. Shares are booked at the settlement spot rather
            than the strike, which keeps the option accounting untouched.
          </p>
          <p>
            XSP (Mini-SPX) is European, cash-settled: it can only be exercised at its own expiration, never early,
            so there is no <span className="mono">short_settled</span> state, no delivered-share disposal, and no
            weekend share-carry exposure for it. Both legs simply close at expiry.
          </p>
        </div>
      </Card>
    </div>
  );
}
