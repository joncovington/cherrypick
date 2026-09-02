/**
 * **Preserved evidence, 2026-08-31.** The research/screener section this backed was retired, but
 * this module and its test suite are kept: each of the 43 cases across `describe.test.ts` and
 * `narrative.test.ts` replays an observed reference-platform card, and together they are the
 * evidence that justified these formulae. That evidence is irreplaceable and costs nothing to keep,
 * where re-deriving it would mean re-observing a platform we no longer run against.
 *
 * Nothing in the console calls this now. It is a reference implementation, not a live path.
 */
/**
 * Port of scout's analytics/narrative.py: plain-language per-symbol analysis, generated from data
 * the console already computes — the two-paragraph shape a commercial research platform shows under
 * each chart (a scan-classification headline plus a "Price Action" observation), emulated from our
 * own candles/levels/trend/metrics rather than scraped.
 *
 * Pure functions, no I/O. Every sentence is generated from a *detected condition* with the numbers
 * inline, so a claim is checkable against the chart it sits under; nothing here free-writes text.
 * The trend wording rides the console's own provisional `priceMaCount` classifier (fitted on 25
 * labelled rows, pending re-validation), which is honest as "our trend read" and not as a
 * reproduction of anyone else's.
 *
 * Price Action picks ONE observation by priority — a concrete recent event beats a laundry list:
 * 200-day MA cross today > 50-day MA cross today > gap on high volume > level break > large
 * 3-session move > bounce off a nearby level > trend + S/R fallback. An earnings-timing suffix is
 * appended when metrics says the report is today or tomorrow.
 */
import type { Bar, Level } from "./levels.js";
import type { TrendGrade } from "./trend.js";

const BIG_MOVE_PCT = 0.05;
const GAP_VOLUME_RATIO = 1.5;
const BOUNCE_PROXIMITY = 0.01;
const DAY_MS = 86_400_000;

const SIDE: Record<TrendGrade, "bullish" | "neutral" | "bearish"> = {
  bullish: "bullish",
  mildly_bullish: "bullish",
  neutral: "neutral",
  mildly_bearish: "bearish",
  bearish: "bearish",
};

export interface EarningsInfo {
  expected_report_date?: string | null;
  time_of_day?: string | null;
}

export interface MetricsInfo {
  iv_30d?: number | null;
  hv_30d?: number | null;
  iv_rank?: number | string | null;
  dividend_ex_date?: string | null;
  dividend_next_date?: string | null;
  dividend_rate_per_share?: number | string | null;
}

export interface ScanHeadline {
  scan: string;
  text: string;
}

/** Today in UTC as an ISO date, so a caller can pin it and the tests are not clock-dependent. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Whole days from `a` to `b`, both ISO dates. Null when either is unparseable. */
function daysBetween(a: string, b: string): number | null {
  const ta = Date.parse(`${a}T00:00:00Z`);
  const tb = Date.parse(`${b}T00:00:00Z`);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return null;
  return Math.round((tb - ta) / DAY_MS);
}

function fixed(n: number, digits: number): string {
  return n.toFixed(digits);
}

function smaLast(closes: number[], period: number, endOffset = 0): number | null {
  const end = closes.length - endOffset;
  if (period <= 0 || end < period) return null;
  let sum = 0;
  for (let i = end - period; i < end; i++) sum += closes[i]!;
  return sum / period;
}

/** ("above"|"below", sma) when yesterday→today moved price across the SMA, else null. */
function smaCrossToday(closes: number[], period: number): ["above" | "below", number] | null {
  if (closes.length < period + 1) return null;
  const todaySma = smaLast(closes, period)!;
  const prevSma = smaLast(closes, period, 1)!;
  const prevDiff = closes[closes.length - 2]! - prevSma;
  const todayDiff = closes[closes.length - 1]! - todaySma;
  if (prevDiff <= 0 && todayDiff > 0) return ["above", todaySma];
  if (prevDiff >= 0 && todayDiff < 0) return ["below", todaySma];
  return null;
}

function gapOnVolume(bars: Bar[]): "up" | "down" | null {
  if (bars.length < 31) return null;
  const today = bars[bars.length - 1]!;
  const prev = bars[bars.length - 2]!;
  const volumes = bars.slice(-31, -1).map((b) => b.v).filter((v) => Boolean(v));
  if (volumes.length === 0 || !today.v) return null;
  const avg = volumes.reduce((a, b) => a + b, 0) / volumes.length;
  if (today.v < GAP_VOLUME_RATIO * avg) return null;
  if (today.l > prev.h) return "up";
  if (today.h < prev.l) return "down";
  return null;
}

/** A close crossing a clustered S/R level between yesterday and today. */
function levelBreak(bars: Bar[], levels: Level[]): ["above" | "below", number] | null {
  if (bars.length < 2) return null;
  const prevClose = bars[bars.length - 2]!.c;
  const close = bars[bars.length - 1]!.c;
  for (const level of levels) {
    const price = level.price;
    if (prevClose < price && price <= close) return ["above", price];
    if (prevClose > price && price >= close) return ["below", price];
  }
  return null;
}

function bigMove(closes: number[]): number | null {
  if (closes.length < 4) return null;
  const base = closes[closes.length - 4]!;
  const pct = (closes[closes.length - 1]! - base) / base;
  return Math.abs(pct) >= BIG_MOVE_PCT ? pct : null;
}

function bounce(bars: Bar[], levels: Level[]): Level | null {
  if (bars.length === 0) return null;
  const close = bars[bars.length - 1]!.c;
  return levels.find((lv) => Math.abs(lv.price - close) / close <= BOUNCE_PROXIMITY) ?? null;
}

function earningsSuffix(earnings: EarningsInfo | null | undefined, today: string): string {
  const raw = earnings?.expected_report_date;
  if (!raw) return "";
  const days = daysBetween(today, String(raw));
  if (days === null) return "";
  const timing = String(earnings?.time_of_day ?? "");
  const when = timing === "BTO" ? " before the open" : timing === "AMC" ? " after the close" : "";
  if (days === 0) return ` and reports earnings today${when}`;
  if (days === 1) return ` and reports earnings tomorrow${when}`;
  return "";
}

/** One concrete, checkable observation about recent price behaviour, priority-ordered. */
export function priceAction(
  name: string,
  bars: Bar[],
  levels: Level[],
  trend6m: TrendGrade | null,
  earnings: EarningsInfo | null = null,
  today: string = todayIso(),
): string {
  const closes = bars.map((b) => b.c);
  const suffix = earningsSuffix(earnings, today);
  const last = closes.length > 0 ? closes[closes.length - 1]! : null;
  const supports =
    last === null ? [] : levels.filter((lv) => lv.kind === "support" && lv.price < last);
  const resistances =
    last === null ? [] : levels.filter((lv) => lv.kind === "resistance" && lv.price > last);

  for (const period of [200, 50]) {
    const cross = smaCrossToday(closes, period);
    if (cross) {
      const [dir, value] = cross;
      return `${name} crossed ${dir} its ${period}-day moving average at ${fixed(value, 2)} today${suffix}.`;
    }
  }

  const gap = gapOnVolume(bars);
  if (gap) {
    const day = new Date(bars[bars.length - 1]!.t * 1000).toISOString().slice(0, 10);
    return `${name} gapped ${gap} on high volume on ${day}${suffix}.`;
  }

  const brk = levelBreak(bars, levels);
  if (brk) {
    const [dir, price] = brk;
    const role =
      dir === "above"
        ? "resistance, which now becomes support"
        : "support, which now becomes resistance";
    return `${name} broke ${dir} its ${fixed(price, 2)} ${role}${suffix}.`;
  }

  const move = bigMove(closes);
  if (move !== null) {
    const word = move > 0 ? "higher" : "lower";
    return `${name} moved ${fixed(Math.abs(move) * 100, 2)}% ${word} over the last 3 sessions${suffix}.`;
  }

  const bounced = bounce(bars, levels);
  if (bounced) {
    return `${name} is trading at its ${fixed(bounced.price, 2)} ${bounced.kind} level${suffix}.`;
  }

  const side = trend6m ? SIDE[trend6m] : "neutral";
  const parts = [`${name} is in a ${side} trend`];
  if (supports.length > 0) {
    parts.push(`with support at ${fixed(Math.max(...supports.map((s) => s.price)), 2)}`);
  }
  if (resistances.length > 0) {
    const joiner = supports.length > 0 ? "and resistance" : "with resistance";
    parts.push(`${joiner} at ${fixed(Math.min(...resistances.map((r) => r.price)), 2)}`);
  }
  return parts.join(" ") + suffix + ".";
}

// ------------------------------------------------------------------ secondary detectors

/**
 * Commodity Channel Index — the indicator behind the reference platform's "CCI Trend" scan chip.
 * Standard formulation: (typical price − its SMA) / (0.015 × mean absolute deviation).
 */
export function cci(bars: Bar[], period = 20): number | null {
  if (bars.length < period) return null;
  const typical = bars.slice(-period).map((b) => (b.h + b.l + b.c) / 3);
  const mean = typical.reduce((a, b) => a + b, 0) / period;
  const deviation = typical.reduce((a, t) => a + Math.abs(t - mean), 0) / period;
  if (deviation === 0) return 0;
  return (typical[typical.length - 1]! - mean) / (0.015 * deviation);
}

/** The 50-day SMA crossing the 200-day SMA between yesterday and today. */
function goldenDeathCrossToday(closes: number[]): "golden" | "death" | null {
  if (closes.length < 201) return null;
  const fNow = smaLast(closes, 50)!;
  const sNow = smaLast(closes, 200)!;
  const fPrev = smaLast(closes, 50, 1)!;
  const sPrev = smaLast(closes, 200, 1)!;
  if (fPrev <= sPrev && fNow > sNow) return "golden";
  if (fPrev >= sPrev && fNow < sNow) return "death";
  return null;
}

function week52(closes: number[]): string | null {
  if (closes.length < 60) return null;
  const high = Math.max(...closes);
  const low = Math.min(...closes);
  const last = closes[closes.length - 1]!;
  if (last >= high) return "made a new 52-week closing high today";
  if (last <= low) return "made a new 52-week closing low today";
  if (last >= 0.98 * high) return `is within ${fixed(((high - last) / high) * 100, 1)}% of its 52-week high`;
  if (last <= 1.02 * low) return `is within ${fixed(((last - low) / low) * 100, 1)}% of its 52-week low`;
  return null;
}

function streak(closes: number[]): string | null {
  if (closes.length < 7) return null;
  let up = 0;
  let down = 0;
  for (let i = closes.length - 1; i > 0; i--) {
    const cur = closes[i]!;
    const prev = closes[i - 1]!;
    if (cur > prev && down === 0) up += 1;
    else if (cur < prev && up === 0) down += 1;
    else break;
  }
  if (up >= 5) return `has closed higher ${up} sessions in a row`;
  if (down >= 5) return `has closed lower ${down} sessions in a row`;
  return null;
}

/**
 * True when the current `window`-day high-low range is the narrowest such range in `lookback` bars —
 * a coiling read without needing full Bollinger/Keltner machinery.
 */
function squeeze(bars: Bar[], window = 20, lookback = 126): boolean {
  if (bars.length < lookback) return false;
  const ranges: number[] = [];
  for (let end = window; end <= bars.length; end++) {
    const chunk = bars.slice(end - window, end);
    ranges.push(Math.max(...chunk.map((b) => b.h)) - Math.min(...chunk.map((b) => b.l)));
  }
  const tail = ranges.slice(-(lookback - window + 1));
  return ranges[ranges.length - 1]! <= Math.min(...tail);
}

function extension(closes: number[]): string | null {
  if (closes.length < 50) return null;
  const sma50 = smaLast(closes, 50)!;
  const pct = (closes[closes.length - 1]! - sma50) / sma50;
  if (pct >= 0.12) return `is stretched ${fixed(pct * 100, 0)}% above its 50-day moving average`;
  if (pct <= -0.12) return `is stretched ${fixed(Math.abs(pct) * 100, 0)}% below its 50-day moving average`;
  return null;
}

/**
 * The strongest secondary technical observation, or null. Priority mirrors specificity: a 50/200
 * cross is rarer and stronger than a 52-week note, which beats a streak, and so on.
 */
export function technicalBullet(name: string, bars: Bar[]): string | null {
  const closes = bars.map((b) => b.c);
  const cross = goldenDeathCrossToday(closes);
  if (cross === "golden") {
    return `${name}'s 50-day moving average crossed above its 200-day today (a golden cross).`;
  }
  if (cross === "death") {
    return `${name}'s 50-day moving average crossed below its 200-day today (a death cross).`;
  }
  const w52 = week52(closes);
  if (w52) return `${name} ${w52}.`;
  const s = streak(closes);
  if (s) return `${name} ${s}.`;
  if (squeeze(bars)) return `${name} is coiling in its tightest 20-day range of the past six months.`;
  const ext = extension(closes);
  if (ext) return `${name} ${ext}.`;
  return null;
}

/**
 * The strongest options/market-context observation from the metrics fields — the layer a price-only
 * narrative lacks, and the one an options tool should lead with.
 */
export function optionsBullet(
  name: string,
  info: MetricsInfo | null,
  skewEdge: number | null = null,
): string | null {
  const i = info ?? {};
  const iv = i.iv_30d;
  const hv = i.hv_30d;
  if (iv && hv) {
    const ratio = iv / hv;
    const detail = `(IV ${fixed(iv * 100, 0)}% vs realized ${fixed(hv * 100, 0)}%)`;
    if (ratio >= 1.25) {
      return `${name}'s options trade at ${fixed(ratio, 1)}x realized volatility ${detail} -- premium is rich.`;
    }
    if (ratio <= 0.8) {
      return `${name}'s options trade at ${fixed(ratio, 1)}x realized volatility ${detail} -- premium is cheap.`;
    }
  }
  const rawRank = i.iv_rank;
  const ivRank = rawRank === null || rawRank === undefined ? null : Number(rawRank);
  if (ivRank !== null && Number.isFinite(ivRank)) {
    if (ivRank >= 0.7) {
      return `${name}'s IV rank is ${fixed(ivRank * 100, 0)}/100 -- near its richest of the year.`;
    }
    if (ivRank <= 0.2) {
      return `${name}'s IV rank is ${fixed(ivRank * 100, 0)}/100 -- options are cheap by its own history.`;
    }
  }
  if (skewEdge !== null && Math.abs(skewEdge) > 0) {
    const lean =
      skewEdge > 0 ? "calls pricing richer than puts" : "puts pricing richer than calls";
    return `${name}'s option chain shows ${lean} at matched distances from spot.`;
  }
  return null;
}

/**
 * True relative performance vs a benchmark (SPX) over ~3 months — unlike the reference platform's
 * "Relative Strength" score, which its own docs describe as a trend composite.
 */
export function relativeStrengthBullet(
  name: string,
  closes: number[],
  benchmarkCloses: number[] | null,
): string | null {
  if (!benchmarkCloses || closes.length < 64 || benchmarkCloses.length < 64) return null;
  const symRet = closes[closes.length - 1]! / closes[closes.length - 64]! - 1;
  const benchRet = benchmarkCloses[benchmarkCloses.length - 1]! / benchmarkCloses[benchmarkCloses.length - 64]! - 1;
  const diff = symRet - benchRet;
  if (Math.abs(diff) < 0.08) return null;
  const word = diff > 0 ? "outperformed" : "underperformed";
  return `${name} has ${word} the S&P 500 by ${fixed(Math.abs(diff) * 100, 0)}% over the past three months.`;
}

/**
 * Builder-facing warnings: events landing inside a chosen expiration that change a ticket's risk
 * character. Returns [] when nothing applies — absence of a warning is a real claim, so an
 * unparseable date contributes nothing rather than a guessed warning.
 */
export function eventWarnings(
  expiration: string,
  earnings: EarningsInfo | null,
  info: MetricsInfo | null,
  today: string = todayIso(),
): string[] {
  const warnings: string[] = [];
  const inWindow = (iso: string): boolean => {
    const fromToday = daysBetween(today, iso);
    const toExpiry = daysBetween(iso, expiration);
    return fromToday !== null && toExpiry !== null && fromToday >= 0 && toExpiry >= 0;
  };

  const report = earnings?.expected_report_date;
  if (report && inWindow(String(report))) {
    warnings.push(
      `An earnings report (${String(report)}) lands inside this expiration -- gap and IV-crush risk apply.`,
    );
  }
  for (const field of ["dividend_ex_date", "dividend_next_date"] as const) {
    const raw = info?.[field];
    if (!raw) continue;
    if (!inWindow(String(raw))) continue;
    const rate = info?.dividend_rate_per_share;
    const amount = rate ? ` ($${Number(rate).toFixed(2)}/share)` : "";
    warnings.push(
      `Goes ex-dividend ${String(raw)}${amount} before this expiration -- ` +
        "short in-the-money calls carry early-assignment risk.",
    );
    break;
  }
  return warnings;
}

/**
 * The scan classification: a CCI dip/rally within an established trend (the more specific setup,
 * checked first) or a longer-term trend with a short-term counter-move. Counter-trend reversal scans
 * remain a follow-up — absent a match this returns null and the UI omits the headline.
 */
export function scanHeadline(
  name: string,
  trend1m: TrendGrade | null,
  trend6m: TrendGrade | null,
  bars: Bar[] | null = null,
): ScanHeadline | null {
  const side6m = trend6m ? SIDE[trend6m] : null;
  const side1m = trend1m ? SIDE[trend1m] : null;
  const cciNow = bars ? cci(bars) : null;

  if (cciNow !== null && side6m === "bullish" && cciNow <= -100) {
    return {
      scan: "CCI Dip in Bullish Trend",
      text:
        `${name} is in a bullish trend and recently experienced a short-term pullback ` +
        `(CCI ${fixed(cciNow, 0)}), which may provide a buying opportunity.`,
    };
  }
  if (cciNow !== null && side6m === "bearish" && cciNow >= 100) {
    return {
      scan: "CCI Rally in Bearish Trend",
      text:
        `${name} is in a bearish trend and recently experienced a short-term rally ` +
        `(CCI ${fixed(cciNow, 0)}), which may provide a selling opportunity.`,
    };
  }
  if (side6m === "bullish" && (side1m === "bearish" || side1m === "neutral")) {
    return {
      scan: "Bullish Trend Following",
      text:
        `${name} has recently pulled back within a longer-term bullish trend, ` +
        "which may offer a favorable risk/reward for a bullish trade.",
    };
  }
  if (side6m === "bearish" && (side1m === "bullish" || side1m === "neutral")) {
    return {
      scan: "Bearish Trend Following",
      text:
        `${name} has recently rallied within a longer-term bearish trend, ` +
        "which may offer a favorable risk/reward for a bearish trade.",
    };
  }
  return null;
}
