/**
 * A bullet bar: one measure against a scale, optionally with a band behind it and a comparison
 * tick on it.
 *
 * Stephen Few designed the form in 2005 to replace dashboard gauges, which spend a great deal of
 * space saying very little. The shape earns its place here for the readings this console keeps
 * writing out as sentences — a completion rate against the era's own, buying power against the
 * gate's cap, an experiment's progress against the sample it needs.
 *
 * It computes nothing. Every part is optional and a null part is simply not drawn, because the
 * alternative — a band at zero, a tick at the left edge — is a drawn claim about a thing nobody
 * measured. The bands are the writer's ranges, never thresholds invented here.
 */
export function Bullet({
  min,
  max,
  value,
  band,
  marker,
  label,
}: {
  min: number;
  max: number;
  value: number | null;
  /** A qualitative range behind the bar — a floor's price band, a promotion gate's window. */
  band?: [number, number] | null;
  /** The comparison: an era median, a cap, spot. */
  marker?: number | null;
  label?: string;
}) {
  const span = max - min || 1;
  const pct = (v: number) => Math.min(100, Math.max(0, ((v - min) / span) * 100));

  return (
    <div className="bullet" role="img" aria-label={label ?? "measure against its scale"}>
      {band != null && (
        <span
          className="bullet-band"
          style={{ left: `${String(pct(band[0]))}%`, width: `${String(pct(band[1]) - pct(band[0]))}%` }}
        />
      )}
      {value !== null && <span className="bullet-value" style={{ width: `${String(pct(value))}%` }} />}
      {marker != null && <span className="bullet-marker" style={{ left: `${String(pct(marker))}%` }} />}
    </div>
  );
}
