/**
 * Whether a bucketed breakdown can currently support a conclusion.
 *
 * A card that cannot should say so rather than draw one. Every misleading surface found on
 * 2026-08-11 was the same failure in a different costume — the daily_summary zeros, the paper NLV
 * chart, a weekday table over three sessions — and none of them looked broken. They looked like
 * findings.
 *
 * Two causes, reported apart because they call for opposite responses:
 *
 * - ONE BUCKET is degenerate by construction. "By symbol" in the current MEIC era is SPX alone; a
 *   one-row table showing 100% of everything is not a comparison. The fix is to widen the scope, or
 *   to wait for a second bucket to exist.
 * - EVERY BUCKET ON ONE SESSION is underpowered. Three sessions split by weekday is one Monday, one
 *   Tuesday and one Friday — three single-day cells presented as a weekday effect. The fix is more
 *   sessions; nothing about the scope will help.
 *
 * Withheld, never deleted. The dimension is real and the card self-heals as sessions accumulate,
 * which is also how the modules' own EOD reports treat a single-bucket dimension.
 */
export interface BucketedRow {
  bucket: string;
  sessions: number;
}

export function powerNote(rows: BucketedRow[]): string | null {
  if (rows.length === 0) return null;
  if (rows.length < 2) {
    return `Single bucket (${rows[0]!.bucket}) — degenerate by construction in this scope, so there is nothing to compare. Widen the era, or wait for a second bucket to exist.`;
  }
  if (Math.max(...rows.map((r) => r.sessions)) < 2) {
    return `Every bucket rests on a single session — this is ${rows.length} days, not a ${rows.length}-way comparison. Withheld until a bucket repeats.`;
  }
  return null;
}

/** A card body replaced by the reason it is withheld. */
export function WithheldNote({ note }: { note: string }) {
  return (
    <p className="muted" style={{ fontSize: 12 }}>
      {note}
    </p>
  );
}
