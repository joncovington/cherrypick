import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

/** The declared card sizes. A card is a span and a height, and the pair is its size. */
export type CardSpan = 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 12;
export type CardHeight = 64 | 96 | 128 | 248 | 304;

/**
 * A link to one of the module's own pages, carrying the query string it was opened under.
 *
 * The mode, date, arm and era a reader chose are in the URL's query, so a link that dropped it
 * would open the right page on the wrong session -- a working link showing the wrong numbers.
 * Its own component so `useLocation` runs only for a card that links: a card without one renders
 * outside a router (every test here is `renderToString`), as it always has.
 */
function CardLink({ to, className, label, children }: { to: string; className?: string; label: string; children: ReactNode }) {
  const { search } = useLocation();
  return (
    <Link to={`${to}${search}`} className={className} aria-label={label} title={label}>
      {children}
    </Link>
  );
}

/**
 * A card on the frame's 12-column grid: head, one visualization, one foot line.
 *
 * The constraint worth naming is the foot. It is a single line and it is for the caveat that
 * makes the number above it honest — n, the era, after fees, thin, stale — because a chart that
 * drops its qualifier is the specific way this package could get less truthful while looking
 * better. Anything longer belongs on the page the card links to, and a second chart belongs in a
 * second card.
 *
 * Heights are fixed rather than content-sized. `auto` rows would make every card as tall as its
 * own content and the grid would read as the ragged stack it is replacing; a card whose detail
 * cannot fit links to the page that holds it, not a taller box.
 *
 * A card that explains a number in more detail LINKS to that module's page for it (`to`) -- the
 * title and the ⤢ both go there. Until 2026-09-24 it opened an overlay sheet over the frame
 * instead; every place a reader can go is now a page in the rail, reachable, reloadable and
 * shareable the same way.
 */
export function GridCard({
  label,
  span,
  h,
  foot,
  to,
  toLabel,
  className,
  children,
}: {
  label: ReactNode;
  span: CardSpan;
  h: CardHeight;
  /** One line. The caveat, not a summary. */
  foot?: ReactNode;
  /** The module page that holds this card's detail, e.g. `/flies/books`. Present means the card links. */
  to?: string;
  /** What the linked page shows, for the link's accessible name. Defaults to the card's own label. */
  toLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const name = `open ${toLabel ?? (typeof label === "string" ? label : "detail")}`;
  return (
    <section className={`gcard span-${String(span)} h-${String(h)}${className !== undefined ? ` ${className}` : ""}`}>
      <div className="gcard-head">
        {to !== undefined ? (
          <CardLink to={to} className="gcard-label gcard-label-link" label={name}>
            {label}
          </CardLink>
        ) : (
          <span className="gcard-label">{label}</span>
        )}
        {to !== undefined && (
          <CardLink to={to} className="gcard-expand" label={name}>
            ⤢
          </CardLink>
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
  to,
  toLabel,
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
  to?: string;
  toLabel?: string;
  span?: CardSpan;
  h?: CardHeight;
  /** A spark, a bullet — whatever gives the number its shape. */
  children?: ReactNode;
}) {
  const toneClass =
    value === null ? "" : tone === "pos" ? " pnl-pos" : tone === "neg" ? " pnl-neg" : tone === "dim" ? " muted" : "";
  return (
    <GridCard label={label} span={span} h={h} foot={foot} to={to} toLabel={toLabel}>
      <div className="stat-big" title={title}>
        <span className={`stat-big-value${toneClass}`}>{value ?? "—"}</span>
        {children}
      </div>
    </GridCard>
  );
}
