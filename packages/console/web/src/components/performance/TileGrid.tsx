import type { CSSProperties, ReactNode } from "react";

/**
 * A row of stat tiles whose column count always divides the tile count, so a wrapped grid never
 * ends on a ragged, half-empty last row. `.stats-grid`'s auto-fit puts as many columns as fit, and
 * nine tiles in a seven-wide card leave five empty cells under the last two.
 *
 * The columns follow the CARD's width (a container query on the host), not the window's: the same
 * tiles sit in a full-width page card and in a narrow lightbox slide. Six tiles step 2 / 3 / 6;
 * twelve step 2 / 3 / 4 / 6. A row of another length should be made one of these, not given its own
 * breakpoints.
 */
export function TileGrid({ count, style, children }: { count: 6 | 12; style?: CSSProperties; children: ReactNode }) {
  return (
    <div className="tile-grid-host" style={style}>
      <div className={`stats-grid tile-grid-${count}`}>{children}</div>
    </div>
  );
}
