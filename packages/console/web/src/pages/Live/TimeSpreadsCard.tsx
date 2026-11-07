import type { ManageTimeSpreads } from "@console/shared";
import { Card } from "../../components/DataTable";
import { TimeSpreadTable } from "../../components/TimeSpreadTable";
import { TradeCards, timeSpreadModel } from "./TradeCard";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";

/**
 * The time-spread scanner's calendars and diagonals sized to the account (packages/manage
 * `timespreads.suggest`): the ones that fit, with the quantity and the cap that stops the next unit,
 * then the ones that do not. Every figure is the package's. NO TESTED EDGE: ordered by the front/back
 * IV ratio, the hypothesis item 54 tests.
 */
/** The card lists this many of the too-big rows; manage sends them all for the screener tab. */
export const TOO_BIG_SHOWN = 30;

export function TimeSpreadsCard({ t }: { t: ManageTimeSpreads | null }) {
  if (!t) return null;
  return (
    <Card title="time spreads, sized to the account">
      <TimeSpreadsBody t={t} capDollars={null} cards={false} />
    </Card>
  );
}

/** The time spreads' caption, the ones that fit as position cards when `cards` (the portfolio tab's
 *  adding-trades card) or as the table, and the too-big table. */
export function TimeSpreadsBody({
  t,
  capDollars,
  cards = true,
  captions = true,
}: {
  t: ManageTimeSpreads;
  capDollars: number | null;
  cards?: boolean;
  /** False where the card's ? holds the explanation (`timeSpreadsHelp`); what is held back today still shows. */
  captions?: boolean;
}) {
  const dropped = Object.entries(t.dropped)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  const df = t.deltaFilter;
  return (
    <>
      {t.error ? (
        <p className="muted">{t.error}</p>
      ) : (
        <>
          {captions ? (
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            <strong>No tested edge.</strong> {t.order ?? "ordered by the front/back IV ratio, untested"}. From the {t.sourceSession ?? "—"}{" "}
            scan, modelled at mid: re-quote before sending · buying power after the trade at most{" "}
            {t.bpCapPct === null ? "its cap" : `${Math.round(t.bpCapPct * 100)}%`} of net liq
            {df?.bullishHeldBack
              ? ` · the book's beta-weighted delta (${df.bookBwd?.toFixed(0) ?? "—"}) is over +${df.limit ?? "—"}: bullish structures held back`
              : ""}
            {dropped ? ` · left out: ${dropped}` : ""}
          </p>
          ) : df?.bullishHeldBack ? (
            <p className="muted pf-caption" style={{ marginTop: 0 }}>
              the book's beta-weighted delta ({df.bookBwd?.toFixed(0) ?? "—"}) is over +{df.limit ?? "—"}: bullish structures held back
            </p>
          ) : null}
          {t.fits.length === 0 ? (
            <p className="muted">nothing fits today</p>
          ) : cards ? (
            <TradeCards models={t.fits.map((s) => timeSpreadModel(s, capDollars))} />
          ) : (
            <TimeSpreadTable rows={t.fits} sized />
          )}
          {t.fitsTotal > t.fits.length && <p className="muted pf-caption">the first {t.fits.length} of {t.fitsTotal} that fit</p>}
          {t.tooBig.length > 0 && (
            <>
              <p className="muted pf-caption">
                too big for the account today ({t.tooBigTotal}), with the cap that stops each
                {t.tooBig.length > TOO_BIG_SHOWN ? `; the first ${TOO_BIG_SHOWN}, every one on Screeners, time spreads` : ""}:
              </p>
              {cards ? (
                <details className="pf-too-big">
                  <summary>the table</summary>
                  <TimeSpreadTable rows={t.tooBig.slice(0, TOO_BIG_SHOWN)} sized />
                </details>
              ) : (
                <TimeSpreadTable rows={t.tooBig.slice(0, TOO_BIG_SHOWN)} sized />
              )}
            </>
          )}
        </>
      )}
      {captions ? (
        <p className="muted pf-caption">
          caps: one trade's debit within the per-trade risk · buying power under this block's ceiling · delta in band. The theta caps
          are the income candidates', not applied here. Nothing here is placed.
        </p>
      ) : null}
    </>
  );
}

/** The time spreads' explanation, behind the adding-trades card's ? on its time-spreads tab. */
export function timeSpreadsHelp(t: ManageTimeSpreads): CardHelpContent {
  const dropped = Object.entries(t.dropped).map(([why, n]) => `${n} ${why}`);
  return {
    title: "Where these time spreads come from",
    body: (
      <>
        <HelpWarn title="No tested edge">
          <p>{t.order ?? "Ordered by the front/back IV ratio, an untested hypothesis"}.</p>
        </HelpWarn>
        <HelpSection title="Where they come from">
          <p>
            The {t.sourceSession ?? "—"} time-spread scan, modelled at mid at each leg's own IV. Re-quote before sending.
          </p>
        </HelpSection>
        {dropped.length > 0 ? (
          <HelpSection title="Left out">
            <ul>
              {dropped.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </HelpSection>
        ) : null}
        <HelpSection title="Caps">
          <p>
            One trade's debit within the per-trade risk, buying power after the trade at most{" "}
            {t.bpCapPct === null ? "its cap" : `${Math.round(t.bpCapPct * 100)}%`} of net liq, and delta in band. The theta caps are the
            income candidates', not applied here. Nothing here is placed.
          </p>
        </HelpSection>
      </>
    ),
  };
}
