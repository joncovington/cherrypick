/**
 * Why the GEX pages lead with volume: the broker's feed (tastytrade) has no level 2 data, so open
 * interest does not stream. It is the once-a-day count, as of the open, and never moves during the
 * session; the session's volume is what changes. One text for every GEX surface, so the pages cannot
 * explain it two ways.
 */
export const GEX_OI_NOTE =
  "tastytrade does not offer level 2 data, so open interest does not stream: it is the broker's once-a-day count, as of the open. " +
  "GEX by volume (gamma × the session's traded volume) is the intraday read and is shown by default; the open-interest figures are positioning as of the open.";

export function GexOiNote() {
  return (
    <p className="gex-oi-note muted" role="note">
      {GEX_OI_NOTE}
    </p>
  );
}
