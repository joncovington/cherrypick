import type { CandleBar } from "@console/shared";

/**
 * One session's Market Profile (TPO), a MIRROR of `cherrypick.core.marketprofile.build_profile`.
 *
 * The Intraday chart draws the profile from the candles it already streams, so it needs the
 * derivation in the browser; a port of a derivation is only safe while it is checked, so both this
 * and the Python module answer `packages/core/tests/fixtures/marketprofile_cases.json`
 * (`web/test/marketProfile.test.ts` here, `tests/test_marketprofile.py` there). Change both or
 * neither. The rules, in brief (the module's docstring has them whole):
 *
 *  - Regular session 09:30-16:00 ET for every symbol, futures included; other bars are ignored.
 *  - 30-minute periods lettered A..M; a bar must sit inside one period (`barSeconds` divides it).
 *  - Rows are `floor(price / rowSize)`, a row's price its lower edge; a period's letters are the
 *    union of its bars' rows. Volume is spread evenly over each bar's rows.
 *  - POC: most TPOs, a tie to the row nearest the middle, then the lower. Value area: the CBOT
 *    two-row expansion to 70%, upward on a tie. IB: periods A and B's high and low.
 *
 * Chart only: it informs no decision anywhere in the suite.
 */

export const SESSION_OPEN_MIN = 9 * 60 + 30;
export const SESSION_CLOSE_MIN = 16 * 60;
export const PERIOD_MINUTES = 30;
export const VALUE_AREA_SHARE = 0.7;
export const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
export const ROW_FRACTION = 0.0003;
const NICE = [1, 2, 2.5, 5];
const EDGE = 1e-9;

export interface ProfileRow {
  price: number;
  letters: string;
  volume: number;
}

export interface MarketProfile {
  session: string;
  rowSize: number;
  rows: ProfileRow[];
  periods: string;
  open: number;
  close: number;
  high: number;
  low: number;
  poc: number;
  val: number;
  vah: number;
  tpoTotal: number;
  ibLow: number | null;
  ibHigh: number | null;
  extensionUp: number | null;
  extensionDown: number | null;
  tailHigh: number;
  tailLow: number;
  poorHigh: boolean;
  poorLow: boolean;
  singlePrints: number[];
  volumeTotal: number;
  volumePoc: number | null;
}

const tidy = (x: number): number => Number(x.toPrecision(10));

/** A display row size: ROW_FRACTION of the price, to the nearest 1/2/2.5/5 step on a log scale. */
export function defaultRowSize(price: number): number {
  if (!(price > 0 && Number.isFinite(price))) throw new Error(`price must be positive, got ${price}`);
  const target = price * ROW_FRACTION;
  const exp = Math.floor(Math.log10(target));
  let best = 1;
  let bestDist = Infinity;
  for (const e of [exp - 1, exp, exp + 1]) {
    for (const m of NICE) {
      const cand = m * 10 ** e;
      const dist = Math.abs(Math.log(cand / target));
      if (dist < bestDist - 1e-12) {
        best = cand;
        bestDist = dist;
      }
    }
  }
  return tidy(best);
}

const ET_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** An epoch-seconds instant on the ET wall clock: its date and seconds since midnight. */
export function etClock(t: number): { date: string; seconds: number } {
  const parts: Record<string, string> = {};
  for (const p of ET_PARTS.formatToParts(t * 1000)) parts[p.type] = p.value;
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    seconds: Number(parts.hour) * 3600 + Number(parts.minute) * 60 + Number(parts.second),
  };
}

const rowOf = (price: number, rowSize: number): number => Math.floor(price / rowSize + EDGE);

/** The ET dates with at least one bar starting inside the regular session, oldest first. */
export function sessionDates(bars: readonly CandleBar[]): string[] {
  const out = new Set<string>();
  for (const b of bars) {
    const { date, seconds } = etClock(b.t);
    if (seconds >= SESSION_OPEN_MIN * 60 && seconds < SESSION_CLOSE_MIN * 60) out.add(date);
  }
  return [...out].sort();
}

function pointOfControl(counts: readonly number[]): number {
  const mid = (counts.length - 1) / 2;
  let best = 0;
  counts.forEach((n, i) => {
    const b = counts[best]!;
    if (n > b || (n === b && Math.abs(i - mid) < Math.abs(best - mid))) best = i;
  });
  return best;
}

interface Bar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export function buildProfile(
  bars: readonly CandleBar[],
  opts: { rowSize: number; barSeconds: number; session?: string | null },
): MarketProfile | null {
  const { rowSize, barSeconds } = opts;
  if (!(rowSize > 0 && Number.isFinite(rowSize))) throw new Error(`rowSize must be positive, got ${rowSize}`);
  const periodS = PERIOD_MINUTES * 60;
  if (barSeconds <= 0 || periodS % barSeconds !== 0) {
    throw new Error(`barSeconds ${barSeconds} must divide the ${PERIOD_MINUTES}-minute period`);
  }
  let session = opts.session ?? null;
  if (session === null) {
    const dates = sessionDates(bars);
    if (dates.length === 0) return null;
    session = dates.at(-1)!;
  }

  const byPeriod = new Map<number, Bar[]>();
  for (const bar of bars) {
    const { date, seconds } = etClock(bar.t);
    if (date !== session) continue;
    const offset = seconds - SESSION_OPEN_MIN * 60;
    if (offset < 0 || offset >= (SESSION_CLOSE_MIN - SESSION_OPEN_MIN) * 60) continue;
    if (offset % barSeconds !== 0) throw new Error(`bar at ${bar.t} does not start on a ${barSeconds}s boundary`);
    const { o, h, l, c } = bar;
    if (![o, h, l, c].every((x) => Number.isFinite(x) && x > 0)) continue;
    const v = Number.isFinite(bar.v) && bar.v > 0 ? bar.v : 0;
    const p = Math.floor(offset / periodS);
    if (!byPeriod.has(p)) byPeriod.set(p, []);
    byPeriod.get(p)!.push({ t: bar.t, o, h, l, c, v });
  }
  if (byPeriod.size === 0) return null;

  const periods = [...byPeriod.keys()].sort((a, b) => a - b);
  const all = periods.flatMap((p) => [...byPeriod.get(p)!].sort((a, b) => a.t - b.t));
  let loIdx = Infinity;
  let hiIdx = -Infinity;
  for (const b of all) {
    loIdx = Math.min(loIdx, rowOf(b.l, rowSize));
    hiIdx = Math.max(hiIdx, rowOf(b.h, rowSize));
  }
  const nRows = hiIdx - loIdx + 1;
  const letters: string[] = Array.from({ length: nRows }, () => "");
  const volume = new Array<number>(nRows).fill(0);
  for (const p of periods) {
    const touched = new Set<number>();
    for (const b of byPeriod.get(p)!) {
      const a = rowOf(b.l, rowSize) - loIdx;
      const z = rowOf(b.h, rowSize) - loIdx;
      const share = b.v / (z - a + 1);
      for (let i = a; i <= z; i++) {
        touched.add(i);
        volume[i] = volume[i]! + share;
      }
    }
    for (const i of touched) letters[i] = letters[i]! + LETTERS[p]!;
  }

  const counts = letters.map((s) => s.length);
  const total = counts.reduce((s, n) => s + n, 0);
  const poc = pointOfControl(counts);
  let loVa = poc;
  let hiVa = poc;
  let acc = counts[poc]!;
  while (acc < VALUE_AREA_SHARE * total) {
    const upRows: number[] = [];
    for (let i = hiVa + 1; i < Math.min(hiVa + 3, nRows); i++) upRows.push(i);
    const dnRows: number[] = [];
    for (let i = loVa - 1; i > Math.max(loVa - 3, -1); i--) dnRows.push(i);
    if (upRows.length === 0 && dnRows.length === 0) break;
    const up = upRows.reduce((s, i) => s + counts[i]!, 0);
    const dn = dnRows.reduce((s, i) => s + counts[i]!, 0);
    if (upRows.length > 0 && (up >= dn || dnRows.length === 0)) {
      hiVa = upRows.at(-1)!;
      acc += up;
    } else {
      loVa = dnRows.at(-1)!;
      acc += dn;
    }
  }

  const runOfSingles = (from: number, step: number): number => {
    let k = 0;
    for (let i = from; i >= 0 && i < nRows; i += step) {
      if (counts[i] !== 1) break;
      k++;
    }
    return k;
  };
  const tailHigh = runOfSingles(nRows - 1, -1);
  const tailLow = tailHigh === nRows ? 0 : runOfSingles(0, 1);
  const price = (i: number): number => tidy((loIdx + i) * rowSize);
  const singlePrints: number[] = [];
  for (let i = tailLow; i < nRows - tailHigh; i++) if (counts[i] === 1) singlePrints.push(price(i));

  const high = Math.max(...all.map((b) => b.h));
  const low = Math.min(...all.map((b) => b.l));
  const hasIb = byPeriod.has(0) && byPeriod.has(1);
  const ibBars = hasIb ? [...byPeriod.get(0)!, ...byPeriod.get(1)!] : [];
  const ibHigh = hasIb ? Math.max(...ibBars.map((b) => b.h)) : null;
  const ibLow = hasIb ? Math.min(...ibBars.map((b) => b.l)) : null;
  const volumeTotal = volume.reduce((s, x) => s + x, 0);

  return {
    session,
    rowSize,
    rows: letters.map((l, i) => ({ price: price(i), letters: l, volume: volume[i]! })),
    periods: periods.map((p) => LETTERS[p]).join(""),
    open: all[0]!.o,
    close: all.at(-1)!.c,
    high,
    low,
    poc: price(poc),
    val: price(loVa),
    vah: price(hiVa),
    tpoTotal: total,
    ibLow,
    ibHigh,
    extensionUp: ibHigh === null ? null : Math.max(0, high - ibHigh),
    extensionDown: ibLow === null ? null : Math.max(0, ibLow - low),
    tailHigh,
    tailLow,
    poorHigh: counts[nRows - 1]! >= 2,
    poorLow: counts[0]! >= 2,
    singlePrints,
    volumeTotal,
    volumePoc: volumeTotal > 0 ? price(pointOfControl(volume)) : null,
  };
}
