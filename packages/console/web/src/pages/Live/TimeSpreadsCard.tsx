import type { ManageTimeSpreads } from "@console/shared";
import { Card } from "../../components/DataTable";
import { TimeSpreadTable } from "../../components/TimeSpreadTable";

/**
 * The time-spread scanner's calendars and diagonals sized to the account (packages/manage
 * `timespreads.suggest`): the ones that fit, with the quantity and the cap that stops the next unit,
 * then the ones that do not. Every figure is the package's. NO TESTED EDGE: ordered by the front/back
 * IV ratio, the hypothesis item 54 tests.
 */
export function TimeSpreadsCard({ t }: { t: ManageTimeSpreads | null }) {
  if (!t) return null;
  const dropped = Object.entries(t.dropped)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  const df = t.deltaFilter;
  return (
    <Card title="time spreads, sized to the account">
      {t.error ? (
        <p className="muted">{t.error}</p>
      ) : (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            <strong>No tested edge.</strong> {t.order ?? "ordered by the front/back IV ratio, untested"}. From the {t.sourceSession ?? "—"}{" "}
            scan, modelled at mid: re-quote before sending · buying power after the trade at most{" "}
            {t.bpCapPct === null ? "its cap" : `${Math.round(t.bpCapPct * 100)}%`} of net liq
            {df?.bullishHeldBack
              ? ` · the book's beta-weighted delta (${df.bookBwd?.toFixed(0) ?? "—"}) is over +${df.limit ?? "—"}: bullish structures held back`
              : ""}
            {dropped ? ` · left out: ${dropped}` : ""}
          </p>
          {t.fits.length > 0 ? <TimeSpreadTable rows={t.fits} sized /> : <p className="muted">nothing fits today</p>}
          {t.fitsTotal > t.fits.length && <p className="muted pf-caption">the first {t.fits.length} of {t.fitsTotal} that fit</p>}
          {t.tooBig.length > 0 && (
            <>
              <p className="muted pf-caption">too big for the account today ({t.tooBigTotal}), with the cap that stops each:</p>
              <TimeSpreadTable rows={t.tooBig} sized />
            </>
          )}
        </>
      )}
      <p className="muted pf-caption">
        caps: one trade's debit within the per-trade risk · buying power under this block's ceiling · delta in band. The theta caps
        are the income candidates', not applied here. Nothing here is placed.
      </p>
    </Card>
  );
}
