import { useOpeningRange } from "../../lib/api";
import { Card, SkeletonRows } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { FliesLayout } from "./FliesFilter";
import { Section } from "../../components/Section";
import { Spark } from "../../components/chart/Spark";
import { hhmm } from "../../components/chart/time";

/**
 * The 09:30-10:00 window, shown raw.
 *
 * Both flies and MEIC open their entry windows at 10:00, so this is the half-hour a reader has in
 * front of them when entries begin. It reports and judges nothing: no ATR normalisation, no
 * efficiency ratio, no regime label. Those have tunable definitions that live once, in
 * `cherrypick.core.openingrange`; a second copy here would drift at the edges that matter.
 *
 * An incomplete window says so and shows no range. A range over four of six buckets is not a
 * range, and a zero in its place would be the most dangerous number on the page.
 *
 * The sparkline is scaled to the session's own high/low rather than the closes' extent, so the
 * line sits where the window actually sat -- a six-tick drift inside a wide range should look
 * flat, and a self-scaled spark would draw it as a rally.
 */

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value" title={hint}>
        {value}
      </span>
    </div>
  );
}

export const OPENING_RANGE_HELP: CardHelpContent = {
  title: "Opening range",
  body: (
    <>
      <HelpSection title="Raw observations only">
        <p>
          No ATR, no regime, no judgement: the half hour before either entry window opens, as it was. Those definitions live once, in
          <code> cherrypick.core.openingrange</code>, and the study they feed is declared in <code>research/openingrange.md</code>. This
          gates nothing.
        </p>
      </HelpSection>
      <HelpSection title="The sparkline">
        <p>
          Scaled to the session's own high and low, not the closes' extent, so a six-tick drift inside a wide range looks flat rather than
          like a rally.
        </p>
      </HelpSection>
      <HelpSection title="An incomplete window">
        <p>A range over four of six buckets is not a range, so an incomplete window says so and shows none.</p>
      </HelpSection>
    </>
  ),
};

function Wrapped({ children }: { children: React.ReactNode }) {
  return (
    <FliesLayout>
      <Section concept="hist" tag="09:30–10:00" question="What did the opening half hour do?">
        {children}
      </Section>
    </FliesLayout>
  );
}

export function OpeningRangeCard({ filter }: { filter: { date: string | null } }) {
  const { data, isLoading, isError, dataUpdatedAt } = useOpeningRange(filter.date);

  if (isLoading || data === undefined) {
    return (
      <Wrapped>
      <Card title="opening range" help={OPENING_RANGE_HELP} updatedAt={dataUpdatedAt} isError={isError}>
        <div className="table-scroll">
        <table className="data-table">
          <tbody>
            <SkeletonRows n={2} cols={4} />
          </tbody>
        </table>
        </div>
      </Card>
      </Wrapped>
    );
  }

  const closes = data.buckets.map((b) => b.close);
  const scaled = data.high !== null && data.low !== null;
  const rising = closes.length > 0 && closes[closes.length - 1]! >= closes[0]!;

  return (
    <Wrapped>
    <Card title="opening range" help={OPENING_RANGE_HELP} updatedAt={dataUpdatedAt} collapseKey="flies-opening-range">
      <p className="muted" style={{ marginTop: 0 }}>
        09:30–10:00 ET on {data.session ?? "—"} · {data.symbol} · the half-hour before either entry
        window opens.
      </p>
      {!data.complete ? (
        <p className="pnl-neg" style={{ marginBottom: 0 }}>
          incomplete window — {data.reason}. {data.bucketsPresent} of {data.bucketsExpected} five-minute
          buckets recorded, so there is no range to report.
        </p>
      ) : (
        <>
          <div className="stat-row">
            <Stat label="range" value={`${data.rangePoints!.toFixed(2)} pts`} />
            <Stat label="high" value={data.high!.toFixed(2)} />
            <Stat label="low" value={data.low!.toFixed(2)} />
            <Stat
              label="09:55 close"
              value={data.last!.toFixed(2)}
              hint="the last print before the entry window opens"
            />
          </div>
          {scaled && (
            <div style={{ marginTop: "0.4rem" }}>
              <Spark
                values={closes}
                domain={[data.low!, data.high!]}
                width={260}
                height={48}
                tone={rising ? "pos" : "neg"}
                title="opening range bucket closes"
                pointTitles={data.buckets.map((b) => `${hhmm(b.minute)} close ${b.close.toFixed(2)}`)}
                stretch={false}
              />
            </div>
          )}
        </>
      )}
    </Card>
    </Wrapped>
  );
}
