/**
 * One calibration-metric tile -- the shared shape every module's performance slide uses
 * (docs/console-refactor plan: "replaces the local Tile in Flies/PerformanceTab.tsx and
 * MeicPerformanceTab's inline tiles"). A metric this suite computes always carries a coverage
 * count and, where the reading says so, a flag that it hasn't cleared the promotion gate --
 * both stay attached to the number rather than living in prose beside it, so a reader scanning
 * tiles can't miss either.
 */
export function Tile({
  label,
  value,
  tone,
  n,
  nUnit,
  afterFees,
  underpowered,
  title,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "dim";
  /** Sample size the value is computed over. Omitted (not 0) when the metric carries no count of
   * its own -- distinct from `n={0}`, which is a real empty sample. */
  n?: number | null;
  /** What `n` counts when it is not trades, e.g. "sessions": the footer then reads "8 sessions". */
  nUnit?: string;
  /** Every net figure in this suite is already net of the modeled fee/slippage stack
   * (core.fees) -- stated on the tile so "after fees" never has to be inferred from context. */
  afterFees?: boolean;
  underpowered?: boolean;
  title?: string;
}) {
  const cls = tone === "pos" ? "pnl-pos" : tone === "neg" ? "pnl-neg" : tone === "dim" ? "muted" : "";
  const count = n === undefined || n === null ? null : nUnit === undefined ? `n=${n}` : `${n} ${nUnit}`;
  const footer = [count, afterFees ? "after fees" : null].filter((s): s is string => s !== null);
  return (
    <div className="stat-tile" title={title}>
      <span className="stat-label">
        {label}
        {underpowered && (
          <span
            className="chip chip-warn"
            style={{ marginLeft: 6, fontSize: 9 }}
            title="Below the promotion gate's sample and day thresholds — not measured, which is neither a pass nor a fail"
          >
            underpowered
          </span>
        )}
      </span>
      <span className={`stat-value ${cls}`}>{value}</span>
      {footer.length > 0 && (
        <span className="muted" style={{ fontSize: 10, display: "block" }}>
          {footer.join(" · ")}
        </span>
      )}
    </div>
  );
}
