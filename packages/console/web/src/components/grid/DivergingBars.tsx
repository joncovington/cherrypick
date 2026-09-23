import { SignedBar } from "../Charts";

export interface DivergingRow {
  label: string;
  value: number | null;
  title?: string;
}

/**
 * A per-arm comparison as bars rather than a table column.
 *
 * Built on the existing `SignedBar`, which already solves the part that matters: a loss and a win
 * of the same size get the same visual weight off a shared zero, rather than losses being scaled
 * against wins and reading as smaller than they are.
 *
 * `tone="none"` is for a measure whose sign is not a verdict — fee drag is a percentage of credit,
 * and every value is positive, so colouring it by sign would tint the whole column green and
 * imply something nobody claimed. The old table tinted drag above 30% red; that threshold is the
 * console's own invention and does not come along.
 */
export function DivergingBars({
  rows,
  format,
  tone = "sign",
  emptyText = "nothing to compare",
}: {
  rows: DivergingRow[];
  format: (v: number) => string;
  tone?: "sign" | "none";
  emptyText?: string;
}) {
  if (rows.length === 0) return <p className="muted">{emptyText}</p>;
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.value ?? 0)));

  return (
    <div className="diverge">
      {rows.map((r) => (
        <div className="diverge-row" key={r.label} title={r.title}>
          <div className="diverge-head">
            <span className="diverge-label">{r.label}</span>
            <span
              className={
                r.value === null
                  ? "diverge-value muted"
                  : tone === "none"
                    ? "diverge-value"
                    : `diverge-value ${r.value >= 0 ? "pnl-pos" : "pnl-neg"}`
              }
            >
              {r.value === null ? "—" : format(r.value)}
            </span>
          </div>
          {r.value !== null && (
            <SignedBar
              value={r.value}
              maxAbs={maxAbs}
              compact
              className={tone === "none" ? "diverge-neutral" : ""}
            />
          )}
        </div>
      ))}
    </div>
  );
}
