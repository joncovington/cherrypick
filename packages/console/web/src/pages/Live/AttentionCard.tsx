import type { ManageAttention, ManageAttentionItem } from "@console/shared";

/**
 * Live → wheel's "needs a look" (2026-11-04): manage's `attention` list, every short option near its
 * strike, in the money or likely exercised early, worst first, and every leg expiring within the
 * ruleset's days. The level, the distance and the why are manage's; the meter only draws the distance
 * against the alert threshold (full at the strike or through it). It decides nothing.
 */

const LEVEL_CLASS: Record<string, string> = {
  "early exercise": "att-chip is-bad",
  "in the money": "att-chip is-bad",
  near: "att-chip is-warn",
};

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** How full the meter is: 100% at or through the strike, 0% at the alert distance. */
export function meterFill(distance: number | null, nearPct: number | null): number {
  if (distance === null || nearPct === null || nearPct <= 0) return 0;
  if (distance <= 0) return 100;
  return Math.max(0, Math.min(100, (1 - distance / nearPct) * 100));
}

function Row({ i, nearPct }: { i: ManageAttentionItem; nearPct: number | null }) {
  const fill = meterFill(i.distance, nearPct);
  return (
    <li className="att-row">
      <div className="att-leg">
        <span className="att-name">{i.underlying}</span> <span className="muted">{i.leg}</span>
        <span className="att-why">{i.why}</span>
      </div>
      <div className="att-meter" role="img" aria-label={`${pct(i.distance)} from the strike`}>
        <span className={i.level === "near" ? "att-fill is-warn" : "att-fill is-bad"} style={{ width: `${fill}%` }} />
      </div>
      <span className="att-nums">
        spot {i.spot === null ? "—" : i.spot.toFixed(2)} · time value {i.extrinsic === null ? "—" : i.extrinsic.toFixed(2)}
      </span>
      <span className={LEVEL_CLASS[i.level] ?? "att-chip"}>{i.level}</span>
    </li>
  );
}

/** Manage's attention reading, `scope` (the wheel's names) keeping only those names' legs. */
export function AttentionCard({ a: all, scope = null }: { a: ManageAttention; scope?: string[] | null }) {
  const keep = (x: { underlying: string }) => scope === null || scope.includes(x.underlying);
  const a = { ...all, items: all.items.filter(keep), expiring: all.expiring.filter(keep) };
  const of = scope === null ? "" : " on a wheel name";
  return (
    <section className="att-board" aria-label="Needs a look">
      <div className="att-col">
        <div className="att-head">
          <span className="att-title">Needs a look</span>
          <span className="muted">
            a short{of} within {pct(a.nearPct, 0)} of its strike, through it, or likely exercised early
          </span>
        </div>
        {a.items.length === 0 ? (
          <p className="muted">
            nothing: every short option{of} is further than {pct(a.nearPct, 0)} from its strike
          </p>
        ) : (
          <ul className="att-list">
            {a.items.map((i) => (
              <Row key={`${i.symbol ?? i.leg}-${i.underlying}`} i={i} nearPct={a.nearPct} />
            ))}
          </ul>
        )}
      </div>
      <div className="att-col att-side">
        <div className="att-head">
          <span className="att-title">Expiring in {a.expiringDays ?? "?"} days</span>
        </div>
        {a.expiring.length === 0 ? (
          <p className="muted">
            nothing{of} expires in {a.expiringDays ?? "?"} days
          </p>
        ) : (
          <ul className="att-exp">
            {a.expiring.map((e) => (
              <li key={`${e.symbol ?? e.leg}-${e.underlying}`}>
                <span className="att-name">{e.underlying}</span> {e.quantity > 0 ? "+" : "−"}
                {Math.abs(e.quantity)} {e.leg} <span className="muted">· {e.dte === 0 ? "today" : `${e.dte} d`}</span>
                {e.distance !== null ? <span className="muted"> · {pct(e.distance)} from the strike</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
