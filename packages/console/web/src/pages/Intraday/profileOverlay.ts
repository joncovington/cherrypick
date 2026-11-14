import {
  LineStyle,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesType,
  type Time,
} from "lightweight-charts";
import type { CandleBar } from "@console/shared";
import {
  buildProfile,
  defaultRowSize,
  etClock,
  OVERNIGHT_START_MIN,
  sessionDates,
  SESSION_CLOSE_MIN,
  type MarketComposite,
  type MarketProfile,
} from "../../lib/marketProfile";

/**
 * The Market Profile overlay on the Intraday chart: the newest regular session's TPO histogram behind
 * the candles, and price lines for its POC, value area and initial balance, plus the prior session's
 * POC and value area.
 *
 * Built from the 15-minute candles (7 days of history on subscribe, so the prior session is always
 * there; 15 divides the 30-minute period, so each period's high and low are exact) through
 * `lib/marketProfile`, the mirror of `cherrypick.core.marketprofile`. Chart only.
 */

export const PROFILE_BAR_SECONDS = 900;

export interface SessionProfiles {
  latest: MarketProfile | null;
  prior: MarketProfile | null;
  /** The latest session is today's and the regular session has not closed. */
  developing: boolean;
}

/**
 * The newest session's profile and the one before it. The row size comes from the oldest bar held,
 * so it stays fixed while the series is open (a row size that moved with the last price would
 * re-cut the histogram mid-session).
 */
export function sessionProfiles(bars: readonly CandleBar[], nowMs: number = Date.now()): SessionProfiles {
  const dates = sessionDates(bars);
  if (dates.length === 0 || bars.length === 0) return { latest: null, prior: null, developing: false };
  const rowSize = defaultRowSize(bars[0]!.c);
  const opts = { rowSize, barSeconds: PROFILE_BAR_SECONDS };
  const latest = buildProfile(bars, { ...opts, session: dates.at(-1)! });
  const prior = dates.length >= 2 ? buildProfile(bars, { ...opts, session: dates.at(-2)! }) : null;
  const now = etClock(nowMs / 1000);
  const developing = latest !== null && latest.session === now.date && now.seconds < SESSION_CLOSE_MIN * 60;
  return { latest, prior, developing };
}

export interface ProfileLine {
  key: string;
  price: number;
  title: string;
  role: "poc" | "va" | "ib" | "prior-poc" | "prior-va" | "untested-poc" | "overnight";
}

/**
 * The line groups a viewer can switch off, each under its key in the page's `?hide=` parameter. POC
 * and the value area are the profile itself and always drawn; these five are context, and with all
 * of them on the price axis carries eight labels.
 */
export const OPTIONAL_LINES: { role: ProfileLine["role"]; param: string }[] = [
  { role: "ib", param: "ib" },
  { role: "prior-poc", param: "ppoc" },
  { role: "prior-va", param: "pva" },
  { role: "untested-poc", param: "npoc" },
  { role: "overnight", param: "on" },
];

/** The roles a `?hide=` value switches off; unknown keys are ignored. */
export function hiddenRoles(hide: string | null): Set<ProfileLine["role"]> {
  const keys = new Set((hide ?? "").split(",").map((s) => s.trim()));
  return new Set(OPTIONAL_LINES.filter((o) => keys.has(o.param)).map((o) => o.role));
}

/** `hiddenRoles` back to its `?hide=` value, in a stable order; empty when nothing is hidden. */
export function hideParam(hidden: ReadonlySet<ProfileLine["role"]>): string {
  return OPTIONAL_LINES.filter((o) => hidden.has(o.role))
    .map((o) => o.param)
    .join(",");
}

/** The price lines a pair of profiles asks for, in a stable order, less any hidden group. */
export function profileLines(
  profiles: SessionProfiles,
  hidden: ReadonlySet<ProfileLine["role"]> = new Set(),
  untested: ReadonlyArray<readonly [string, number]> = [],
  overnight: MarketComposite | null = null,
): ProfileLine[] {
  const row = profiles.latest?.rowSize ?? 0;
  const npoc: ProfileLine[] = untested.map(([session, poc]) => ({
    key: `npoc-${session}`,
    price: poc + row / 2,
    title: `nPOC ${shortDay(session)}`,
    role: "untested-poc",
  }));
  const on: ProfileLine[] =
    overnight === null
      ? []
      : [
          { key: "onh", price: overnight.high, title: "ONH", role: "overnight" },
          { key: "onpoc", price: overnight.poc + overnight.rowSize / 2, title: "ON POC", role: "overnight" },
          { key: "onl", price: overnight.low, title: "ONL", role: "overnight" },
        ];
  return [...allLines(profiles), ...npoc, ...on].filter((l) => !hidden.has(l.role));
}

/**
 * Which overnight a future's chart shows: the one in progress, or the one before the newest session.
 * From 18:00 ET the overnight under way belongs to the next calendar day's session (Sunday evening's to
 * Monday); before then, to the newest bar's own date (its overnight, before 09:30, already ended or
 * still running). A weekend reads Friday's.
 */
export function overnightSession(bars: readonly CandleBar[]): string | null {
  const last = bars.at(-1);
  if (last === undefined) return null;
  const { date, seconds } = etClock(last.t);
  if (seconds < OVERNIGHT_START_MIN * 60) return date;
  const [y, m, d] = date.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

const SHORT_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const shortDay = (iso: string): string => SHORT_DAY.format(Date.parse(`${iso}T12:00:00Z`));

/** Sessions the untested-POC lines may name: the recorded ones, less the two the chart already draws a
 *  POC for (the newest and the prior, from the live candles). */
export function untestedCandidates(recordedSessions: readonly string[], profiles: SessionProfiles): string[] {
  const drawn = new Set([profiles.latest?.session, profiles.prior?.session]);
  return recordedSessions.filter((s) => !drawn.has(s));
}

/**
 * The bars the untested-POC rule reads: the recorded ones (extended hours) and, after the newest of
 * them, the live candles, so a touch since the 17:15 recording counts at once. Live bars from before
 * that point are dropped rather than mixed with the recorded minutes of the same time.
 */
export function untestedBars(recorded: readonly CandleBar[], live: readonly CandleBar[]): CandleBar[] {
  const last = recorded.at(-1)?.t ?? -Infinity;
  return [...recorded, ...live.filter((b) => b.t > last)];
}

function allLines({ latest, prior }: SessionProfiles): ProfileLine[] {
  const out: ProfileLine[] = [];
  if (latest !== null) {
    const half = latest.rowSize / 2;
    // A row's price is its lower edge; a line is drawn through the row's middle, except the value
    // area's, which bound it: VAH at the top of its row, VAL at the bottom of its.
    out.push({ key: "poc", price: latest.poc + half, title: "POC", role: "poc" });
    out.push({ key: "vah", price: latest.vah + latest.rowSize, title: "VAH", role: "va" });
    out.push({ key: "val", price: latest.val, title: "VAL", role: "va" });
    if (latest.ibHigh !== null) out.push({ key: "ibh", price: latest.ibHigh, title: "IBH", role: "ib" });
    if (latest.ibLow !== null) out.push({ key: "ibl", price: latest.ibLow, title: "IBL", role: "ib" });
  }
  if (prior !== null) {
    out.push({ key: "ppoc", price: prior.poc + prior.rowSize / 2, title: "pPOC", role: "prior-poc" });
    out.push({ key: "pvah", price: prior.vah + prior.rowSize, title: "pVAH", role: "prior-va" });
    out.push({ key: "pval", price: prior.val, title: "pVAL", role: "prior-va" });
  }
  return out;
}

export interface OverlayColours {
  poc: string;
  va: string;
  muted: string;
  /** Histogram fills. */
  rowFill: string;
  vaFill: string;
  pocFill: string;
}

/**
 * How each line is drawn. Every line carries its name on the price axis (an unlabelled line was a
 * line nobody could read). Colour says what the level is (amber POC, accent value area, grey initial
 * balance); the stroke says whose it is: today's solid or dashed, the prior session's dotted.
 */
export function lineStyleFor(role: ProfileLine["role"], c: OverlayColours) {
  const base = { lineWidth: 1 as const, axisLabelVisible: true };
  switch (role) {
    case "poc":
      return { ...base, color: c.poc, lineStyle: LineStyle.Solid };
    case "va":
      return { ...base, color: c.va, lineStyle: LineStyle.Dashed };
    case "ib":
      return { ...base, color: c.muted, lineStyle: LineStyle.LargeDashed };
    case "prior-poc":
      return { ...base, color: c.poc, lineStyle: LineStyle.Dotted };
    case "prior-va":
      return { ...base, color: c.va, lineStyle: LineStyle.Dotted };
    case "untested-poc":
      return { ...base, color: c.poc, lineStyle: LineStyle.LargeDashed };
    case "overnight":
      return { ...base, color: c.muted, lineStyle: LineStyle.Solid };
  }
}

/** The legend under the chart (and the help box's list), one entry per line kind, in drawing order. */
export const LINE_LEGEND: { role: ProfileLine["role"]; label: string; meaning: string }[] = [
  { role: "poc", label: "POC", meaning: "point of control: the price traded at longest today" },
  { role: "va", label: "VAH / VAL", meaning: "value area high and low: where 70% of today's time was spent" },
  { role: "ib", label: "IBH / IBL", meaning: "initial balance: the high and low of the first hour (09:30–10:30)" },
  { role: "prior-poc", label: "pPOC", meaning: "the prior session's point of control" },
  { role: "prior-va", label: "pVAH / pVAL", meaning: "the prior session's value area" },
  {
    role: "untested-poc",
    label: "nPOC",
    meaning:
      "untested POCs: earlier sessions' points of control that no later trade, overnight included, has reached (recorded symbols only: SPX, SPY, NDX, QQQ, /ES, /NQ)",
  },
  {
    role: "overnight",
    label: "ONH / ON POC / ONL",
    meaning:
      "a future's overnight session (18:00 the evening before to 09:30 ET): its high, its point of control and its low (futures only)",
  },
];

/** An SVG stroke-dasharray matching a line style, for the legend's swatches. */
export function dashFor(style: LineStyle): string {
  switch (style) {
    case LineStyle.Dotted:
      return "1 3";
    case LineStyle.Dashed:
      return "5 3";
    case LineStyle.LargeDashed:
      return "9 4";
    case LineStyle.SparseDotted:
      return "1 5";
    default:
      return "none";
  }
}

/** Share of the pane's width the longest row takes. */
const HIST_WIDTH = 0.22;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** The histogram: one bar per row, growing leftward from the pane's right edge, behind the candles. */
export class ProfileHistogram implements ISeriesPrimitive<Time> {
  private profile: MarketProfile | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;

  constructor(private readonly colours: OverlayColours) {
    const renderer: IPrimitivePaneRenderer = { draw: () => {}, drawBackground: (t) => this.draw(t) };
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setProfile(p: MarketProfile | null): void {
    this.profile = p;
    this.requestUpdate?.();
  }

  private draw(target: DrawTarget): void {
    const p = this.profile;
    const series = this.series;
    if (p === null || series === null) return;
    const maxTpo = Math.max(...p.rows.map((r) => r.letters.length));
    if (maxTpo === 0) return;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const right = bitmapSize.width;
      const span = bitmapSize.width * HIST_WIDTH;
      for (const r of p.rows) {
        const n = r.letters.length;
        if (n === 0) continue;
        const yTop = series.priceToCoordinate(r.price + p.rowSize);
        const yBot = series.priceToCoordinate(r.price);
        if (yTop === null || yBot === null) continue;
        const top = Math.round(Math.min(yTop, yBot) * vr);
        // Leave a hairline between rows once they are tall enough to show one.
        const h = Math.max(1, Math.round(Math.abs(yBot - yTop) * vr) - (Math.abs(yBot - yTop) >= 4 ? Math.round(vr) : 0));
        const w = Math.max(Math.round(hr), Math.round((n / maxTpo) * span));
        ctx.fillStyle =
          r.price === p.poc ? this.colours.pocFill : r.price >= p.val && r.price <= p.vah ? this.colours.vaFill : this.colours.rowFill;
        ctx.fillRect(right - w, top, w, h);
      }
    });
  }
}
