import type { CSSProperties, ReactNode } from "react";

export type TileCount = 3 | 4 | 5 | 6 | 12;

/** The grid for a row of n tiles where n varies with the view (three to six). */
export function tileCount(n: number): TileCount {
  return n <= 3 ? 3 : n >= 6 ? 6 : (n as 4 | 5);
}

/**
 * A row of stat tiles whose column count always divides the tile count, so a wrapped grid never
 * ends on a ragged, half-empty last row. `.stats-grid`'s auto-fit puts as many columns as fit, and
 * nine tiles in a seven-wide card leave five empty cells under the last two.
 *
 * The columns follow the CARD's width (a container query on the host), not the window's: the same
 * tiles sit in a full-width page card and in a narrow lightbox slide. Six tiles step 2 / 3 / 6;
 * twelve step 2 / 3 / 4 / 6. Three, four and five (rows the simple view shortens, 2026-10-21) step
 * 1 / 3, 2 / 4 and 1 / 5. A row of another length should be made one of these, not given its own
 * breakpoints.
 */
export function TileGrid({ count, style, children }: { count: TileCount; style?: CSSProperties; children: ReactNode }) {
  return (
    <div className="tile-grid-host" style={style}>
      <div className={`stats-grid tile-grid-${count}`}>{children}</div>
    </div>
  );
}
