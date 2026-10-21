import { EARNINGS_WEEK_DAYS } from "@console/shared";

/**
 * The expiry an expected move is priced on and how far past the reaction it lies: "to Oct 23 +2d".
 * The event expiry is the first one LISTED on or after the reaction, so for a name without weeklies it
 * can be weeks out; past `EARNINGS_WEEK_DAYS` the move is flagged as a fortnight's or a month's, not
 * the announcement's alone. Which expiry is used is the user's choice; this only says which it was.
 * Shared by the earnings calendar and its record.
 */
export function ExpiryNote({ expiry, days }: { expiry: string | null; days: number | null }) {
  if (expiry === null) return null;
  const when = new Date(`${expiry}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const long = days !== null && days > EARNINGS_WEEK_DAYS;
  const span = days !== null && days > 14 ? "month" : "fortnight";
  return (
    <>
      {" "}
      <span className="muted">
        to {when}
        {days === null ? "" : ` +${days}d`}
      </span>
      {long && (
        <>
          {" "}
          <span
            className="chip chip-warn"
            title={`Priced on an expiry ${days} days past the reaction (the first one listed): the move to that date, a ${span}'s worth, not the announcement's alone`}
          >
            {span}'s move
          </span>
        </>
      )}
    </>
  );
}
