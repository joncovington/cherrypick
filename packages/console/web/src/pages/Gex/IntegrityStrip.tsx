import type { GexPayload } from "@console/shared";
import { Card } from "../../components/DataTable";

/** Beyond a long holiday weekend — a series further back than this is behind, not merely waiting. */
const STALE_DAYS = 5;
/** A flip read is a claim about NOW; past this it is history wearing a live badge. */
const STALE_SECONDS = 900;

function age(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

/**
 * What bounds a GEX reading — above the numbers rather than beneath.
 *
 * This module journals no measurement breaks; what makes its numbers untrustworthy is staleness and
 * truncation instead. Both have bitten. A gamma-flip read that silently failed for a month is what
 * left bwb's flip book unable to fire, and the regime table carried a hidden `LIMIT 60` against a
 * session that records 240-288 rows, so a reader could not tell a quiet session from a truncated
 * one.
 *
 * The close-series block exists because `daily_closes` is the suite's only multi-year series and
 * SPX's froze for 22 sessions in 2026-07/08 while every other symbol stayed current — nothing on
 * any page would have shown it. Staleness is measured against the FRESHEST series rather than a
 * calendar, so it needs no holiday table to be right.
 */
/** Whether this reading has anything the footer chip should flag -- same thresholds the strip
 *  itself renders against, exported so the lightbox wrapping this in a footer drawer can tone its
 *  chip without recomputing a second opinion of "stale". */
export function gexHasAttention(data: GexPayload | undefined): boolean {
  const i = data?.integrity;
  if (i === undefined) return false;
  const stale = i.latest.filter((r) => r.ageSeconds === null || r.ageSeconds > STALE_SECONDS);
  const behind = i.closeSeries.filter((r) => r.daysBehind > STALE_DAYS);
  return stale.length > 0 || behind.length > 0;
}

export function IntegrityStrip({ data, updatedAt }: { data: GexPayload | undefined; updatedAt?: number }) {
  const i = data?.integrity;
  if (i === undefined) return null;
  const stale = i.latest.filter((r) => r.ageSeconds === null || r.ageSeconds > STALE_SECONDS);
  const behind = i.closeSeries.filter((r) => r.daysBehind > STALE_DAYS);

  return (
    <Card title="measurement integrity" collapseKey="gex-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="integrity-integrity-grid">
          <section>
            <h3>reading freshness</h3>
            {i.latest.length === 0 ? (
              <p className="muted">no regime rows recorded yet</p>
            ) : stale.length === 0 ? (
              <p className="muted">
                every symbol read within {Math.round(STALE_SECONDS / 60)} min —{" "}
                {i.latest.map((r) => `${r.symbol} ${age(r.ageSeconds)}`).join(" · ")}
              </p>
            ) : (
              <p className="integrity-warn">
                {stale.map((r) => `${r.symbol} ${age(r.ageSeconds)}`).join(" · ")} — a flip or wall is a claim
                about now, and a stale one reads as live
              </p>
            )}
            <p className="integrity-note">
              {i.sessionRows.toLocaleString()} regime row{i.sessionRows === 1 ? "" : "s"} recorded on{" "}
              {i.sessionDate ?? "—"}. Stated without a threshold: a short session and a stalled recorder both
              produce a small number, and only the timestamps above separate them.
            </p>
          </section>

          <section>
            <h3>daily close series</h3>
            {behind.length === 0 ? (
              <p className="muted">
                every close series is current with the freshest ({i.closeSeries.length} symbols)
              </p>
            ) : (
              <>
                <p className="integrity-warn">
                  {behind.length} series {behind.length === 1 ? "is" : "are"} behind the freshest by more than{" "}
                  {STALE_DAYS} days — this is the suite's only multi-year series, and a frozen one goes on
                  looking like data
                </p>
                <ul className="integrity-plain-list">
                  {behind.map((r) => (
                    <li key={r.symbol}>
                      <span className="mono">{r.symbol}</span> last close {r.latest ?? "—"}{" "}
                      <span className="muted">
                        ({r.daysBehind.toLocaleString()} days behind · {r.rows.toLocaleString()} rows)
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>
      </div>
    </Card>
  );
}
