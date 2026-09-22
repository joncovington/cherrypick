import type { OpeningRangePayload } from "@console/shared";
import { useOpeningRange } from "../../lib/api";
import { Card, SkeletonRows } from "../../components/DataTable";
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

function Sparkline({ data }: { data: OpeningRangePayload }) {
  const closes = data.buckets.map((b) => b.close);
  if (closes.length < 2 || data.high === null || data.low === null) return null;
  const span = data.high - data.low || 1;
  const width = 260;
  const height = 48;
  const step = width / (closes.length - 1);
  const points = closes
    .map((c, i) => `${(i * step).toFixed(1)},${(height - ((c - data.low!) / span) * height).toFixed(1)}`)
    .join(" ");
  const rising = closes[closes.length - 1]! >= closes[0]!;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="opening range bucket closes"
      style={{ width: "100%", maxWidth: width, height: "auto", display: "block", marginTop: "0.4rem" }}
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.6}
        stroke={rising ? "var(--ok)" : "var(--err)"}
      />
      {closes.map((c, i) => (
        <circle
          key={i}
          cx={i * step}
          cy={height - ((c - data.low!) / span) * height}
          r={2}
          fill={rising ? "var(--ok)" : "var(--err)"}
        >
          <title>{`${hhmm(data.buckets[i]!.minute)} close ${c.toFixed(2)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export function OpeningRangeCard({ filter }: { filter: { date: string | null } }) {
  const { data, isLoading, isError, dataUpdatedAt } = useOpeningRange(filter.date);

  if (isLoading || data === undefined) {
    return (
      <Card title="opening range" updatedAt={dataUpdatedAt} isError={isError}>
        <table className="data-table">
          <tbody>
            <SkeletonRows n={2} cols={4} />
          </tbody>
        </table>
      </Card>
    );
  }

  return (
    <Card title="opening range" updatedAt={dataUpdatedAt} collapseKey="flies-opening-range">
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
          <Sparkline data={data} />
        </>
      )}
      <p className="muted" style={{ fontSize: 11, marginTop: "0.6rem", marginBottom: 0 }}>
        Raw observations only — no ATR, no regime, no judgement. Those definitions live in
        <code> cherrypick.core.openingrange</code>, and the study they feed is declared in
        <code> flies/docs/openingrange.md</code>. This gates nothing.
      </p>
    </Card>
  );
}
