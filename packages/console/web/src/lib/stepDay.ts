/**
 * One step of MEIC's trade-summary day traversal, as a pure function so the house rule holds — a
 * new guard is shown to fail by breaking its invariant on purpose (docs/conventions/working-rules.md).
 *
 * The seam this walks: the summaries list is the newest-20 `daily_summary` rows, written once per
 * session after the close. Between the first entry and the roll-up the current day has no row
 * there, while the header still names it — so an unknown current is that day, sitting one past the
 * newest summary, never an earlier position (the select only offers "latest session" or a summary
 * day). The session is therefore a virtual slot at `days.length`: the previous arrow steps onto
 * the newest settled day from it, and the next arrow steps back onto it — returning null, which
 * the caller turns into `setDay(null)`, "latest session", a value the select actually offers.
 */
export function stepDay(
  days: readonly string[],
  current: string,
  session: string | null,
  delta: -1 | 1,
): string | null {
  // Ascending and unique: the summaries read newest-first; the walk wants oldest-first.
  const sorted = [...new Set(days)].sort();
  const i = sorted.indexOf(current);
  // An unknown current is the virtual session slot at the newest end.
  const at = i < 0 ? sorted.length : i;
  if (delta < 0) {
    return sorted[Math.max(0, at - 1)] ?? null;
  }
  const next = at + 1;
  if (next >= sorted.length) {
    // Past the newest summary: the session, when one exists. With no session there is nothing
    // to step onto, so stay where we are.
    return session !== null ? null : current;
  }
  return sorted[next] ?? null;
}
