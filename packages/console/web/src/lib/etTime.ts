/**
 * Reading the suite's timestamps on the suite's clock.
 *
 * Everything the modules record happens on the ET session clock, and the console is read from
 * wherever the developer happens to be sitting. Two things follow, and getting either wrong is
 * invisible until someone notices a chart disagreeing with a number:
 *
 *  - **Two ledger formats.** Flies writes an offset (`2026-08-13T09:30:15-04:00`). MEIC writes a
 *    bare ET wall clock (`09:30`), which the server can date but cannot zone, so it arrives as
 *    `2026-08-13T09:30:00`. `Date.parse` reads that second form as the VIEWER's local time — so on
 *    a Mountain-time machine a 09:30 ET attempt becomes 11:30 ET. An offset-naive stamp from this
 *    suite is ET by construction, and that is the rule encoded here.
 *  - **Local rendering.** Formatting an instant without naming a zone prints the viewer's clock,
 *    which for market data is never what is meant.
 *
 * Both mistakes were live in the attempts timeline: the first put MEIC's session two hours right of
 * its own axis, the second put flies' two hours left, where a third of the day's fills fell off the
 * canvas entirely and read as an arm that barely traded.
 */

const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/;

const ET_HMS = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** How far ahead of the ET wall clock UTC runs at a given instant — 4h on EDT, 5h on EST. */
function etOffsetMs(atMs: number): number {
  // "sv-SE" renders as "YYYY-MM-DD HH:MM:SS", which parses straight back.
  const wall = new Date(atMs).toLocaleString("sv-SE", { timeZone: "America/New_York" });
  const asUtc = Date.parse(`${wall.replace(" ", "T")}Z`);
  return Number.isNaN(asUtc) ? 0 : atMs - asUtc;
}

/**
 * A suite timestamp as a real instant, in either ledger format. Offset-naive input is read as ET,
 * never as the viewer's local time. Returns null for anything unparseable.
 */
export function parseSuiteTs(ts: string | null): number | null {
  if (ts === null) return null;
  const s = ts.trim();
  if (s === "") return null;
  if (HAS_ZONE.test(s)) {
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? null : ms;
  }
  const f = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(s);
  if (f === null) return null;
  const wallAsUtc = Date.UTC(
    Number(f[1]),
    Number(f[2]) - 1,
    Number(f[3]),
    Number(f[4]),
    Number(f[5]),
    Number(f[6] ?? 0),
  );
  // The offset at the guessed instant. RTH data is never near a DST boundary, where this would be
  // ambiguous anyway.
  return wallAsUtc + etOffsetMs(wallAsUtc);
}

function etParts(ms: number): { h: number; m: number; s: number } | null {
  const parts = ET_HMS.formatToParts(new Date(ms));
  const of = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const h = of("hour");
  const m = of("minute");
  const s = of("second");
  if (!Number.isFinite(h) || !Number.isFinite(m) || !Number.isFinite(s)) return null;
  // The h23 cycle reports midnight as 24 in some engines.
  return { h: h === 24 ? 0 : h, m, s };
}

/**
 * Minutes since ET midnight, fractional on the seconds — the unit a session-clock axis is drawn in.
 * Session bounds (09:30 = 570, 16:00 = 960) are ET, so anything plotted against them must be too.
 */
export function etMinuteOfDay(ts: string | null): number | null {
  const ms = parseSuiteTs(ts);
  if (ms === null) return null;
  const p = etParts(ms);
  return p === null ? null : p.h * 60 + p.m + p.s / 60;
}

/** HH:MM in ET — what every time the suite prints means. */
export function etClock(ts: string | null, fallback = "—"): string {
  const ms = parseSuiteTs(ts);
  if (ms === null) return fallback;
  return new Date(ms).toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
