import { Fragment } from "react";
import { useCalendarsPolicies } from "../../lib/api";
import { Card, DataCard, fmtMoney, fmtPct, PnlCell } from "../../components/DataTable";

/**
 * The exit-policy comparison table — the thing the module exists to produce.
 *
 * Rendered, never computed. The derivation is a tick-by-tick replay over the recorded mark path and
 * it arrives from the module's own `exit_policies.comparison_table` through the server bridge, with
 * its validation welded on. See `services/calendarsBridge.ts` for why a TypeScript second opinion
 * would be the one kind of drift the validation could not catch.
 *
 * One table per structure tag. Distinct tags are distinct trades and a pooled row would be the
 * module's fourth honesty rule broken in the one place it would be least visible.
 */

/** Each policy in the module's own terms, so a reader can tell a rule from its name. */
const POLICIES: Record<string, string> = {
  control: "close every leg in the Friday exit window. The user-defined baseline: no stops, no targets, no weekend hold.",
  "pt-10": "close the whole structure the first tick it is up 10% of the entry debit.",
  "pt-20": "close the whole structure the first tick it is up 20% of the entry debit.",
  "pt-30": "close the whole structure the first tick it is up 30% of the entry debit.",
  "sl-25": "close the whole structure the first tick it is down 25% of the entry debit.",
  "sl-50": "close the whole structure the first tick it is down 50% of the entry debit.",
  "sl-100": "close the whole structure the first tick it is down the full entry debit.",
  "touch-close-side":
    "close only the side whose short strike spot has touched. The one per-side rule in the grid; the other side runs on.",
  "time-thu-close": "close everything in Thursday's exit window, a day before the shorts expire.",
  "time-fri-noon": "close everything from Friday noon rather than waiting for the bell.",
  "expiry-longs-fri": "let the shorts cash-settle or deliver, and sell the longs at Friday's close.",
  "expiry-longs-mon":
    "let the shorts settle and ride the longs to their own Monday expiration morning — the path book's own shape.",
};

export function PoliciesTab() {
  const { data, isLoading, isError, dataUpdatedAt } = useCalendarsPolicies();

  const structures = [...new Set((data?.policies ?? []).flatMap((p) => p.buckets.map((b) => b.structure)))].sort();

  return (
    <div className="cards cards-wide">
      <Card title="what this table is" collapseKey="cal-policies-intro" className="view-fade">
        <div className="cal-prose">
          <p>
            One entry stream, two real books, and a recorded per-tick mark path make every candidate exit rule
            answerable <em>after the fact</em> without running it as its own book. Each policy below is
            replayed over the path book&rsquo;s recorded marks — an exact replay at the recorded prices — and
            the exit it would have taken is priced at that tick&rsquo;s own bid/ask through the same cost stack
            the live books use.
          </p>
          <p>
            Pairing is exact by construction: every book&rsquo;s week is entered from the same plan at the same
            fills, so this is a like-for-like comparison rather than an estimate. That is also why the grid is
            not fifteen books — one permissive arm answers the whole thing.
          </p>
          {data?.caveat != null && (
            <p className="integrity-warn">
              <strong>Granularity caveat:</strong> {data.caveat}.
            </p>
          )}
        </div>
      </Card>

      {data?.error != null && (
        <Card title="derivation unavailable" collapseKey="cal-policies-error" isError>
          <p className="integrity-err">{data.error}</p>
        </Card>
      )}

      {data !== undefined && data.error === null && data.weeksConsidered === 0 && (
        <Card title="no completed weeks" collapseKey="cal-policies-empty" className="view-fade">
          <p className="muted">
            The derivation runs over weeks whose <span className="mono">path</span> positions have all closed.
            None have yet, so there is no table — and a ranking over zero weeks would be worse than none. The
            first entry to reach its Monday disposition fills this in.
          </p>
        </Card>
      )}

      {/* The grid itself, whether or not a week has been measured yet. The module's second honesty
          rule is that the exit rules are declared UP FRONT and measured, never tuned mid-experiment
          — a page that showed them only once they had results would be a page you could not check
          that rule against, and it is exactly at the start, before any number exists to argue with,
          that the declaration is worth reading. */}
      <Card title="the declared grid" collapseKey="cal-policies-grid" className="view-fade">
        <dl className="cal-defs">
          {Object.entries(POLICIES).map(([name, text]) => (
            <Fragment key={name}>
              <dt className="mono">{name}</dt>
              <dd>{text}</dd>
            </Fragment>
          ))}
        </dl>
        <p className="integrity-note">
          Twelve rules, fixed before the first week was traded. Every whole-structure policy that never
          triggers falls through to the control terminal — close everything in the Friday exit window — so a
          rule that never fires reports control&rsquo;s result rather than nothing.{" "}
          <span className="mono">touch-close-side</span> is the one per-side rule; the expiry policies instead
          let the shorts settle and differ only in when the longs go.
        </p>
      </Card>

      {structures.map((structure) => (
        <DataCard
          key={structure}
          title={`exit policies · ${structure}`}
          headers={["policy", "weeks", "derivable", "total net", "avg net", "win rate", "worst week"]}
          loading={isLoading}
          isError={isError}
          rowCount={(data?.policies ?? []).filter((p) => p.buckets.some((b) => b.structure === structure)).length}
          numFrom={1}
          empty="nothing derived for this structure"
          updatedAt={dataUpdatedAt}
          footer={
            <p className="integrity-note">
              <strong>derivable</strong> counts the weeks whose recorded path could answer this policy. A hole
              in the path is excluded and counted as excluded — silently pricing a missing tick would let a
              feed outage flatter whichever policy it happened to favour.
            </p>
          }
        >
          {(data?.policies ?? []).map((p) => {
            const b = p.buckets.find((x) => x.structure === structure);
            if (b === undefined) return null;
            const excluded = b.weeks - b.derivable;
            return (
              <tr key={p.policy}>
                <td>
                  <span className="mono">{p.policy}</span>
                  {POLICIES[p.policy] !== undefined && (
                    <span className="cal-help" title={POLICIES[p.policy]}>
                      ?
                    </span>
                  )}
                </td>
                <td>{b.weeks}</td>
                <td className={excluded > 0 ? "integrity-warn" : ""}>
                  {b.derivable}
                  {excluded > 0 && <span className="muted"> ({excluded} excluded)</span>}
                </td>
                <td>
                  <PnlCell v={b.totalNet} />
                </td>
                <td>
                  <PnlCell v={b.avgNet} />
                </td>
                <td>{fmtPct(b.winRate === null ? null : b.winRate * 100, 0)}</td>
                <td>
                  {b.worst === null ? (
                    <span className="muted">—</span>
                  ) : (
                    <>
                      {fmtMoney(b.worst.netPnl)} <span className="muted mono">{b.worst.weekOf}</span>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </DataCard>
      ))}
    </div>
  );
}
