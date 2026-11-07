import { Fragment } from "react";
import type { CalendarsPolicyRow } from "@console/shared";
import { useCalendarsPolicies } from "../../lib/api";
import { Card, DataCard, fmtMoney, fmtPct, PnlCell, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";

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
    "let the shorts settle and ride the longs to their own Monday expiration morning — the path arm's own shape.",
};

/** The table's method behind a ?, with the module's granularity caveat as the amber flag when it states one. */
function introHelp(caveat: string | null | undefined): CardHelpContent {
  return {
    title: "Exit policies",
    body: (
      <>
        {caveat != null && (
          <HelpWarn title="Granularity caveat">
            <p>{caveat}.</p>
          </HelpWarn>
        )}
        <HelpSection title="What this table is">
          <p>
            One entry stream, two real arms, and a recorded per-tick mark path make every candidate exit rule answerable{" "}
            <em>after the fact</em> without running it as its own arm. Each policy is replayed over the path arm&rsquo;s recorded marks, an
            exact replay at the recorded prices, and the exit it would have taken is priced at that tick&rsquo;s own bid/ask through the
            same cost stack the live arms use.
          </p>
        </HelpSection>
        <HelpSection title="Pairing">
          <p>
            Pairing is exact by construction: every arm&rsquo;s week is entered from the same plan at the same fills, so this is a
            like-for-like comparison rather than an estimate. That is also why the grid is not fifteen arms: one permissive arm answers the
            whole thing.
          </p>
        </HelpSection>
        <HelpSection title="Derivable">
          <p>
            <strong>derivable</strong> counts the weeks whose recorded path could answer this policy. A hole in the path is excluded and
            counted as excluded: silently pricing a missing tick would let a feed outage flatter whichever policy it happened to favour.
          </p>
        </HelpSection>
      </>
    ),
  };
}

const GRID_HELP: CardHelpContent = {
  title: "The declared grid",
  body: (
    <>
      <HelpSection title="Fixed up front">
        <p>
          Twelve rules, fixed before the first week was traded. The grid shows whether or not a week has been measured yet, so the
          declaration can be read before any number exists to argue with.
        </p>
      </HelpSection>
      <HelpSection title="Rules that never trigger">
        <p>
          Every whole-structure policy that never triggers falls through to the control terminal, which closes everything in the Friday exit
          window, so a rule that never fires reports control&rsquo;s result rather than nothing. <span className="mono">touch-close-side</span>{" "}
          is the one per-side rule; the expiry policies instead let the shorts settle and differ only in when the longs go.
        </p>
      </HelpSection>
    </>
  ),
};

export function PoliciesTab() {
  const { data, isLoading, isError, dataUpdatedAt } = useCalendarsPolicies();

  const structures = [...new Set((data?.policies ?? []).flatMap((p) => p.buckets.map((b) => b.structure)))].sort();
  const help = introHelp(data?.caveat);
  const flag = data?.caveat != null ? "granularity caveat" : undefined;

  return (
    <div className="pf-layout">
      <Section concept="edge" tag="exit rules" question="Which exit rule is worth anything?">
        {data?.error != null && (
          <Card title="derivation unavailable" collapseKey="cal-policies-error" isError className="lv-12">
            <p className="integrity-err">{data.error}</p>
          </Card>
        )}

        {data !== undefined && data.error === null && data.weeksConsidered === 0 && (
          <Card title="no completed weeks" collapseKey="cal-policies-empty" className="view-fade lv-12" help={help} flag={flag}>
            <p className="muted">
              The derivation runs over weeks whose <span className="mono">path</span> positions have all closed.
              None have yet, so there is no table — and a ranking over zero weeks would be worse than none. The
              first entry to reach its Monday disposition fills this in.
            </p>
          </Card>
        )}

        {structures.map((structure) => (
          <PolicyTable
            key={structure}
            structure={structure}
            policies={data?.policies ?? []}
            loading={isLoading}
            isError={isError}
            updatedAt={dataUpdatedAt}
            help={help}
            flag={flag}
          />
        ))}
      </Section>

      {/* The grid itself, whether or not a week has been measured yet. The module's second honesty
          rule is that the exit rules are declared UP FRONT and measured, never tuned mid-experiment
          — a page that showed them only once they had results would be a page you could not check
          that rule against, and it is exactly at the start, before any number exists to argue with,
          that the declaration is worth reading. */}
      <Section concept="room" tag="the grid" question="What does each rule do?">
        <Card title="the declared grid" collapseKey="cal-policies-grid" className="view-fade lv-12" help={GRID_HELP}>
          <dl className="cal-defs">
            {Object.entries(POLICIES).map(([name, text]) => (
              <Fragment key={name}>
                <dt className="mono">{name}</dt>
                <dd>{text}</dd>
              </Fragment>
            ))}
          </dl>
        </Card>
      </Section>
    </div>
  );
}

/**
 * One structure's policies. Its own component so each structure keeps its own sort; every value
 * is read through that structure's bucket, never another tag's.
 */
function PolicyTable({
  structure,
  policies,
  loading,
  isError,
  updatedAt,
  help,
  flag,
}: {
  structure: string;
  policies: CalendarsPolicyRow[];
  loading: boolean;
  isError: boolean;
  updatedAt: number;
  help: CardHelpContent;
  flag?: string;
}) {
  const sort = useSort();
  const rows = policies.flatMap((p) => {
    const b = p.buckets.find((x) => x.structure === structure);
    return b === undefined ? [] : [{ p, b }];
  });
  const shown = sortRows(rows, sort, {
    policy: (r) => r.p.policy,
    weeks: (r) => r.b.weeks,
    derivable: (r) => r.b.derivable,
    total: (r) => r.b.totalNet,
    avg: (r) => r.b.avgNet,
    winRate: (r) => r.b.winRate,
    worst: (r) => r.b.worst?.netPnl ?? null,
  });
  return (
    <DataCard
      title={`exit policies · ${structure}`}
      sort={sort}
      headers={[
        { label: "policy", k: "policy" },
        { label: "weeks", k: "weeks" },
        { label: "derivable", k: "derivable" },
        { label: "total net", k: "total" },
        { label: "avg net", k: "avg" },
        { label: "win rate", k: "winRate" },
        { label: "worst week", k: "worst", title: "sorts by the worst week's net" },
      ]}
      loading={loading}
      isError={isError}
      rowCount={rows.length}
      numFrom={1}
      empty="nothing derived for this structure"
      updatedAt={updatedAt}
      help={help}
      flag={flag}
      className="lv-12"
    >
      {shown.map(({ p, b }) => {
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
  );
}
