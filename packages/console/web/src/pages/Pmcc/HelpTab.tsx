import type { PmccPayload } from "@console/shared";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";

/**
 * What this experiment is, in the module's own terms.
 *
 * Deliberately not the shared ExperimentGuideView: that component reads a flies/meic-shaped config
 * block and derives each arm's differences from its siblings. PMCC's three books differ by one
 * stated rule each rather than by a set of overridden parameters, so a derived diff would report
 * almost nothing and miss the whole design. The prose here is the config's own `_what_this_is` and
 * `_books_note`, kept in one place.
 */
export function HelpTab({ data }: { data: PmccPayload | undefined }) {
  const p = data?.params;
  return (
    <div className="cards cards-wide">
      <Card title="what PMCC-99 is" collapseKey="pmcc-help-what" className="view-fade">
        <div className="pmcc-prose">
          <p>
            Buy the deepest ~99-delta call at ~21 DTE — a stock substitute with near-zero extrinsic, deliberately
            <em> not</em> a LEAP — and sell an ITM call at ~9 DTE. The short's intrinsic is the downside buffer; its
            time value is the entire profit. When that time value is exhausted
            {p?.tvCloseThreshold != null && <> (≈{fmtMoney(p.tvCloseThreshold)})</>}, close <strong>both</strong>{" "}
            legs together and re-enter. Never roll — except in the book built to measure rolling.
          </p>
          <p>
            The short strike is the deepest ITM strike whose net time value clears the weekly yield floor
            {p?.targetWeeklyYieldMin != null && <> ({fmtPct(p.targetWeeklyYieldMin * 100, 1)} per week on the net debit)</>}
            : maximum protection subject to yield, rather than maximum yield.
          </p>
          <p className="muted">
            Three leveraged ETFs{p?.symbols !== undefined && p.symbols.length > 0 && <> — {p.symbols.join(", ")}</>}, one
            position per (symbol, book) at a time. Paper only: there is no live loop and no order-placement code
            anywhere in the module.
          </p>
        </div>
      </Card>

      <Card title="the three books — one variable each" collapseKey="pmcc-help-books">
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            <dt>control</dt>
            <dd>
              The strategy as taught: mechanical entry whenever its slot is free, close both legs at the time-value
              threshold, hold like a covered call on a breach, never roll.
            </dd>
            <dt>keltner</dt>
            <dd>
              Control's management <em>exactly</em>; only the entry differs. It enters only when spot sits within
              0.5×ATR of the Keltner midline, above yesterday's close, and has bounced ≥0.25×ATR off the day's low.
              It refuses everything for its first ~{p?.keltnerMinHistory ?? 21} trading days while daily bars
              accumulate — the cold start is design, not failure.
            </dd>
            <dt>roll</dt>
            <dd>
              Control's entry <em>exactly</em>; only the breach handling differs. It rolls the short down and out
              (once per position per session, never past the long's expiration) instead of holding, and closes once
              the long runs short of days.
            </dd>
            <dt>advised:&lt;base&gt;</dt>
            <dd>
              The AI advisor's admitted parameters, frozen on each row at entry. Off by default, and excluded from
              the pairing below — its entries are its own.
            </dd>
          </dl>
        </div>
      </Card>

      <Card title="why the books are only partly comparable" collapseKey="pmcc-help-pairing">
        <div className="pmcc-prose">
          <p>
            <strong>control and roll are exactly paired.</strong> They enter from the same plan on the same tick,
            with identical strikes, mids and modeled costs, so any difference between them is the roll rule and
            nothing else.
          </p>
          <p>
            <strong>keltner is not.</strong> Its variable <em>is</em> the entry tick, so it holds a different set of
            fills by construction. Comparing it to control cycle-by-cycle would credit the entry filter with
            whatever the market did on days it happened to trade. Its aggregate over time is the honest read, which
            is why this page gives it its own section and no delta column.
          </p>
          <p className="muted">
            Every book's rows carry the Keltner measures, including control's — so the filter's counterfactual stays
            readable from the book that ignored it.
          </p>
        </div>
      </Card>

      <Card title="the honesty rules" collapseKey="pmcc-help-honesty">
        <div className="pmcc-prose">
          <ol className="pmcc-rules">
            <li>
              <strong>Every result is net of the modeled fee and slippage stack</strong> — commissions, clearing,
              ORF/TAF, the per-ITM-symbol settlement event, and the pass-throughs on the share side of an
              assignment. Gross is not a result.
            </li>
            <li>
              <strong>Early assignment is unmodelled but measured, so the paper result is an upper bound.</strong>{" "}
              Every mark where the short's extrinsic sits under the exposure threshold
              {p?.assignmentExposureTv != null && <> ({fmtMoney(p.assignmentExposureTv)})</>} is flagged. That
              share bounds what the unmodelled mechanism could have touched — read it beside the net, always.
            </li>
            <li>
              <strong>Ex-dividend spans are refused, not modelled.</strong> A short leg spanning a declared ex-date
              is refused; so is a span the declared calendar cannot answer for. A lapsed table halts entries loudly,
              by design — a missing calendar and "no dividend" must never look alike.
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

      <Card title="physical settlement" collapseKey="pmcc-help-settlement">
        <div className="pmcc-prose">
          <p>
            All three symbols are American physical delivery. An ITM short call at expiry books its intrinsic{" "}
            <em>and</em> delivers 100 short shares per contract at the settlement print; the surviving ~12-DTE long
            stays open, and the next session's combined disposal covers the shares and sells the long.
          </p>
          <p>
            A position does not close while its shares are outstanding — that is the{" "}
            <span className="mono">short_settled</span> state on this page — and the Friday-to-Monday gap is left
            visible, because it <em>is</em> the weekend exposure. Shares are booked at the settlement spot rather
            than the strike, which keeps the option accounting untouched.
          </p>
        </div>
      </Card>
    </div>
  );
}
