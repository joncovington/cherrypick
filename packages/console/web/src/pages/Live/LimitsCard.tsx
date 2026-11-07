import type { ManageLimitRow, ManageLimits } from "@console/shared";
import { Card } from "../../components/DataTable";
import { fmtCash } from "../../lib/format";

/**
 * Live → portfolio's "your limits" (2026-11-04): manage's `limits`, each measure beside the limit it is
 * judged against and whose limit that is (yours, a guidance band, the candidate cap). The state is
 * manage's; the bar only draws the value's share of the way to the limit it faces. Set the limits in
 * Config › Position rules › limits.
 */

const STATE_CHIP: Record<string, string> = {
  over: "chip chip-bad",
  near: "chip chip-warn",
  under: "chip chip-warn",
  within: "chip",
  unknown: "chip muted",
  "no limit": "chip muted",
};

function fmt(v: number | null, unit: string): string {
  if (v === null) return "—";
  if (unit === "share") return `${(v * 100).toFixed(1)}%`;
  if (unit === "$") return fmtCash(Math.round(v * 100) / 100);
  return v.toFixed(1);
}

function limitText(r: ManageLimitRow): string {
  if (r.low === null && r.high === null) return "no limit";
  if (r.low !== null && r.high !== null) return `${fmt(r.low, r.unit)} to ${fmt(r.high, r.unit)}`;
  return r.high !== null ? `up to ${fmt(r.high, r.unit)}` : `down to ${fmt(r.low, r.unit)}`;
}

/** How far the value is toward the edge it faces, 0-100, for the bar. */
export function barFill(r: ManageLimitRow): number {
  if (r.value === null) return 0;
  const edge = r.value >= 0 ? (r.high ?? null) : (r.low ?? null);
  if (edge === null || edge === 0 || Math.sign(edge) !== Math.sign(r.value || edge)) return 0;
  return Math.max(0, Math.min(100, (r.value / edge) * 100));
}

/** The state chip's class, for the room card's stacked rows. */
export function limitChip(state: string): string {
  return STATE_CHIP[state] ?? "chip";
}

export { fmt as limitValue, limitText };

export function LimitsCard({ limits }: { limits: ManageLimits }) {
  return (
    <Card title="your limits" collapseKey="manage-limits">
      <LimitsBody limits={limits} />
    </Card>
  );
}

/** The limits list and its notes; the portfolio tab shows it in the room card's lightbox. */
export function LimitsBody({ limits }: { limits: ManageLimits }) {
  return (
    <>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        each measure beside the limit it is judged against; a blank limit in Config › Position rules › limits uses the guidance band or
        the candidate cap
      </p>
      <ul className="lim-list">
        {limits.rows.map((r) => (
          <li key={r.id} className="lim-row">
            <span className="lim-label">
              {r.label}
              <span className="muted"> · {r.source}</span>
            </span>
            <span className="lim-bar" aria-hidden="true">
              <span className={`lim-fill is-${r.state.replace(" ", "-")}`} style={{ width: `${barFill(r)}%` }} />
            </span>
            <span className="lim-num">
              {fmt(r.value, r.unit)} / {limitText(r)}
            </span>
            <span className={STATE_CHIP[r.state] ?? "chip"}>{r.state}</span>
          </li>
        ))}
      </ul>
      {limits.dayLoss.why ? <p className="muted">the day's P&amp;L is unknown: {limits.dayLoss.why}</p> : null}
      {limits.unknownNames.length ? (
        <p className="muted">no worst case modelled for {limits.unknownNames.join(", ")}: left out of the one-name measure</p>
      ) : null}
    </>
  );
}
