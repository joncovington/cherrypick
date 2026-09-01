/** Shared between Builder (which has structured leg data before it flattens to an OCC symbol) and
    Orders (which now receives that same structured data from staging) so a leg's expiry reads the
    same way in both places. */

export function dteOf(expiration: string | null): number | null {
  if (expiration === null) return null;
  const t = Date.parse(expiration);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((t - Date.now()) / 86_400_000));
}

/**
 * A strike, at its own precision.
 *
 * Never round these: leveraged ETFs list half-dollar strikes, and a fixed 0-decimal format turned a
 * real TQQQ 67.5 into "68" — a strike that does not exist on the chain. Trailing zeros are still
 * dropped, so the common whole-dollar strike stays clean.
 */
export function fmtStrike(strike: number | null): string {
  if (strike === null) return "—";
  return String(Number(strike.toFixed(2)));
}

export function fmtExpiry(expiration: string | null): string {
  if (expiration === null) return "—";
  const t = Date.parse(expiration);
  if (Number.isNaN(t)) return expiration;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
