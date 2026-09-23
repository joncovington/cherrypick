import type { ReactNode } from "react";

/** The declared card sizes. A card is a span and a height, and the pair is its size. */
export type CardSpan = 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 12;
export type CardHeight = 64 | 96 | 128 | 248 | 304;

/**
 * A card on the frame's 12-column grid: head, one visualization, one foot line.
 *
 * The constraint worth naming is the foot. It is a single line and it is for the caveat that
 * makes the number above it honest — n, the era, after fees, thin, stale — because a chart that
 * drops its qualifier is the specific way this package could get less truthful while looking
 * better. Anything longer belongs in a detail sheet, and a second chart belongs in a second card.
 *
 * Heights are fixed rather than content-sized. `auto` rows would make every card as tall as its
 * own content and the grid would read as the ragged stack it is replacing; a card whose content
 * cannot fit gets a sheet, not a taller box.
 */
export function GridCard({
  label,
  span,
  h,
  foot,
  onExpand,
  expandLabel,
  className,
  children,
}: {
  label: ReactNode;
  span: CardSpan;
  h: CardHeight;
  /** One line. The caveat, not a summary. */
  foot?: ReactNode;
  /** Present means this card has a dense form worth opening. */
  onExpand?: () => void;
  /** What the ⤢ opens, for the button's accessible name. Defaults to the card's own label. */
  expandLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const name = expandLabel ?? (typeof label === "string" ? label : "detail");
  return (
    <section className={`gcard span-${String(span)} h-${String(h)}${className !== undefined ? ` ${className}` : ""}`}>
      <div className="gcard-head">
        <span className="gcard-label">{label}</span>
        {onExpand !== undefined && (
          <button
            type="button"
            className="gcard-expand"
            aria-haspopup="dialog"
            aria-label={`open ${name} detail`}
            title={`open ${name} detail`}
            onClick={onExpand}
          >
            ⤢
          </button>
        )}
      </div>
      <div className="gcard-body">{children}</div>
      {foot !== undefined && <div className="gcard-foot">{foot}</div>}
    </section>
  );
}

/**
 * A card that leads with one number.
 *
 * `value` is `null` for "not recorded", and it renders as an em dash with no tone. That is the
 * suite's rule (`lib/format.ts`) and it matters more here than in a table: a tile is read at a
 * glance, and a zero standing in for a missing measurement is the most dangerous number the
 * console can draw. The tone is likewise never a verdict — sign, or a flag a writer already set.
 */
export function StatTile({
  label,
  value,
  tone,
  title,
  foot,
  onExpand,
  expandLabel,
  span = 3,
  // 128 rather than 96: a tile carrying a number AND a shape under it does not fit the short
  // step, and a clipped value is worse than a taller row.
  h = 128,
  children,
}: {
  label: ReactNode;
  value: string | null;
  tone?: "pos" | "neg" | "dim";
  title?: string;
  foot?: ReactNode;
  onExpand?: () => void;
  expandLabel?: string;
  span?: CardSpan;
  h?: CardHeight;
  /** A spark, a bullet — whatever gives the number its shape. */
  children?: ReactNode;
}) {
  const toneClass =
    value === null ? "" : tone === "pos" ? " pnl-pos" : tone === "neg" ? " pnl-neg" : tone === "dim" ? " muted" : "";
  return (
    <GridCard label={label} span={span} h={h} foot={foot} onExpand={onExpand} expandLabel={expandLabel}>
      <div className="stat-big" title={title}>
        <span className={`stat-big-value${toneClass}`}>{value ?? "—"}</span>
        {children}
      </div>
    </GridCard>
  );
}
