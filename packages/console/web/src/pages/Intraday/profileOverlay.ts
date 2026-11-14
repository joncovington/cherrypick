import {
  LineStyle,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesType,
  type Time,
} from "lightweight-charts";
import type { CandleBar, ProfileBarsPayload } from "@console/shared";
import { buildView } from "../Profile/profileView";
import {
  buildProfile,
  rowSizeFor,
  etClock,
  OVERNIGHT_START_MIN,
  sessionDates,
  SESSION_CLOSE_MIN,
  SESSION_OPEN_MIN,
  type MarketComposite,
  type MarketProfile,
} from "../../lib/marketProfile";

/**
 * The Market Profile overlay on the Intraday chart: the newest few regular sessions' TPO histograms
 * behind the candles, each from its own open, and price lines for the newest session's POC, value
 * area and initial balance, plus the prior session's POC and value area.
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
 * The newest session's profile and the one before it. The row size is the symbol's fixed one
 * (`rowSizeFor`, the same as the Profile tab and the morning pack); a symbol without one takes it from
 * the oldest bar held, so it stays put while the series is open (a row size that moved with the last
 * price would re-cut the histogram mid-session). `symbol` is the profile store's name ("SPX", "/ES").
 */
export function sessionProfiles(
  bars: readonly CandleBar[],
  nowMs: number = Date.now(),
  symbol = "",
): SessionProfiles {
  const dates = sessionDates(bars);
  if (dates.length === 0 || bars.length === 0) return { latest: null, prior: null, developing: false };
  const rowSize = rowSizeFor(symbol, bars[0]!.c);
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
  role: "poc" | "va" | "ib" | "prior-poc" | "prior-va" | "untested-poc" | "overnight" | "composite-poc" | "composite-va";
}

/**
 * The line groups a viewer can switch, each under a key. POC and the value area are the profile
 * itself and always drawn; the rest are context, and with all of them on the price axis carries a
 * dozen labels. Most are on until named in `?hide=`; the composite (`defaultOn: false`) is off until
 * named in `?show=`. Roles sharing a key switch together (the composite's POC and value area).
 */
export const OPTIONAL_LINES: { role: ProfileLine["role"]; param: string; defaultOn: boolean }[] = [
  { role: "ib", param: "ib", defaultOn: true },
  { role: "prior-poc", param: "ppoc", defaultOn: true },
  { role: "prior-va", param: "pva", defaultOn: true },
  { role: "untested-poc", param: "npoc", defaultOn: true },
  { role: "overnight", param: "on", defaultOn: true },
  { role: "composite-poc", param: "comp", defaultOn: false },
  { role: "composite-va", param: "comp", defaultOn: false },
];

const keysOf = (raw: string | null): Set<string> => new Set((raw ?? "").split(",").map((s) => s.trim()));

/** The roles hidden by a `?hide=` and a `?show=` value: a default-on group named in `hide`, and a
 *  default-off group not named in `show`. Unknown keys are ignored. */
export function hiddenRoles(hide: string | null, show: string | null = null): Set<ProfileLine["role"]> {
  const h = keysOf(hide);
  const s = keysOf(show);
  return new Set(OPTIONAL_LINES.filter((o) => (o.defaultOn ? h.has(o.param) : !s.has(o.param))).map((o) => o.role));
}

const paramsWhere = (pick: (o: (typeof OPTIONAL_LINES)[number]) => boolean): string =>
  [...new Set(OPTIONAL_LINES.filter(pick).map((o) => o.param))].join(",");

/** `hiddenRoles` back to its `?hide=` value (the default-on groups hidden), in a stable order. */
export function hideParam(hidden: ReadonlySet<ProfileLine["role"]>): string {
  return paramsWhere((o) => o.defaultOn && hidden.has(o.role));
}

/** `hiddenRoles` back to its `?show=` value (the default-off groups shown), in a stable order. */
export function showParam(hidden: ReadonlySet<ProfileLine["role"]>): string {
  return paramsWhere((o) => !o.defaultOn && !hidden.has(o.role));
}

/** How many sessions the intraday chart's composite merges. */
export const COMPOSITE_SESSIONS = 5;

/** The sessions the chart's composite merges: the newest `n` recorded ones, never today's while it is
 *  still trading (a composite of finished sessions holds still through the day). */
export function compositeSessions(recorded: readonly string[], profiles: SessionProfiles, n = COMPOSITE_SESSIONS): string[] {
  const today = profiles.developing ? profiles.latest?.session : undefined;
  return recorded.filter((s) => s !== today).slice(-n);
}

/** Switch a role's whole group: every role sharing its key turns on or off with it. */
export function toggleRole(hidden: ReadonlySet<ProfileLine["role"]>, role: ProfileLine["role"]): Set<ProfileLine["role"]> {
  const param = OPTIONAL_LINES.find((o) => o.role === role)?.param;
  const group = OPTIONAL_LINES.filter((o) => o.param === param).map((o) => o.role);
  const next = new Set(hidden);
  const turnOn = hidden.has(role);
  for (const r of group) {
    if (turnOn) next.delete(r);
    else next.add(r);
  }
  return next;
}

/** The price lines a pair of profiles asks for, in a stable order, less any hidden group. */
export function profileLines(
  profiles: SessionProfiles,
  hidden: ReadonlySet<ProfileLine["role"]> = new Set(),
  untested: ReadonlyArray<readonly [string, number]> = [],
  overnight: MarketComposite | null = null,
  composite: MarketComposite | null = null,
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
  const comp: ProfileLine[] =
    composite === null
      ? []
      : [
          { key: "cpoc", price: composite.poc + composite.rowSize / 2, title: `cPOC ${composite.sessions.length}d`, role: "composite-poc" },
          { key: "cvah", price: composite.vah + composite.rowSize, title: `cVAH ${composite.sessions.length}d`, role: "composite-va" },
          { key: "cval", price: composite.val, title: `cVAL ${composite.sessions.length}d`, role: "composite-va" },
        ];
  return [...allLines(profiles), ...npoc, ...on, ...comp].filter((l) => !hidden.has(l.role));
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
    case "composite-poc":
      return { ...base, color: c.poc, lineStyle: LineStyle.SparseDotted, lineWidth: 2 as const };
    case "composite-va":
      return { ...base, color: c.va, lineStyle: LineStyle.SparseDotted, lineWidth: 2 as const };
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
  {
    role: "composite-poc",
    label: "cPOC",
    meaning:
      "the composite of the five newest finished sessions (today excluded while it trades): its point of control; off by default, switched with cVAH / cVAL (recorded symbols only)",
  },
  {
    role: "composite-va",
    label: "cVAH / cVAL",
    meaning: "the same composite's value area; off by default, switched with cPOC",
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

/** How many sessions' histograms the chart draws (`?sessions=`), newest included. */
export const SESSION_CHOICES = ["1", "5", "10"] as const;
export type SessionChoice = (typeof SESSION_CHOICES)[number];
export const DEFAULT_SESSIONS: SessionChoice = "5";

/** `?sessions=` read back: one of the choices, else the default. */
export function sessionChoice(raw: string | null): SessionChoice {
  return (SESSION_CHOICES as readonly string[]).includes(raw ?? "") ? (raw as SessionChoice) : DEFAULT_SESSIONS;
}

/**
 * The profiles the histograms draw: the newest `n` sessions, oldest first. The newest is the one the
 * lines read (`profiles.latest`), so histogram and lines never disagree. The older ones come from the
 * recorded bars where the store keeps the symbol, by the Profile tab's own rule (`buildView`: the
 * recorded bars, then live ones only after the newest of them), else from the live 15-minute series
 * (seven days). Every one is on the newest's row grid; one that is not is left out rather than drawn
 * on a different scale.
 */
export function recentProfiles(
  profiles: SessionProfiles,
  live: readonly CandleBar[],
  recorded: ProfileBarsPayload | undefined,
  n: number,
): MarketProfile[] {
  const latest = profiles.latest;
  if (latest === null || n < 1) return [];
  if (n === 1) return [latest];
  const opts = { rowSize: latest.rowSize, barSeconds: PROFILE_BAR_SECONDS };
  const all =
    recorded !== undefined && recorded.available
      ? (buildView(recorded, 0, live)?.profiles ?? [])
      : sessionDates(live)
          .map((session) => buildProfile(live, { ...opts, session }))
          .filter((p): p is MarketProfile => p !== null);
  const older = all.filter((p) => p.session < latest.session && p.rowSize === latest.rowSize);
  return [...older.slice(-(n - 1)), latest];
}

/**
 * Where each session's histogram starts: the time of its first regular-session bar among the bars the
 * chart draws. A session the chart holds no bar of (a 1-minute chart loads one day) is absent, and its
 * histogram is not drawn.
 */
export function sessionAnchors(bars: readonly CandleBar[], sessions: readonly string[]): Map<string, number> {
  const want = new Set(sessions);
  const out = new Map<string, number>();
  for (const b of bars) {
    const { date, seconds } = etClock(b.t);
    if (!want.has(date) || out.has(date)) continue;
    if (seconds >= SESSION_OPEN_MIN * 60 && seconds < SESSION_CLOSE_MIN * 60) out.set(date, b.t);
  }
  return out;
}

/** Share of a full session's width on the chart that the longest row takes. */
const SESSION_FILL = 0.6;
const SESSION_SECONDS = (SESSION_CLOSE_MIN - SESSION_OPEN_MIN) * 60;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/**
 * The histograms: each session's rows growing rightward from its own first regular-session bar, behind
 * the candles. One count scale serves every session drawn, so their widths compare; a full session's
 * longest row spans `SESSION_FILL` of that session's width at the current zoom.
 */
export class ProfileHistogram implements ISeriesPrimitive<Time> {
  private profiles: MarketProfile[] = [];
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;
  private anchorsKey = "";
  private anchors = new Map<string, number>();

  /** `bars` reads the series the chart draws; `barSeconds` is its candle width. */
  constructor(
    private readonly colours: OverlayColours,
    private readonly bars: () => readonly CandleBar[],
    private readonly barSeconds: number,
  ) {
    const renderer: IPrimitivePaneRenderer = { draw: () => {}, drawBackground: (t) => this.draw(t) };
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setProfiles(ps: MarketProfile[]): void {
    this.profiles = ps;
    this.requestUpdate?.();
  }

  /** The anchors, recomputed only when the drawn bars or the sessions change (a draw runs every frame
   *  of a scroll). */
  private anchorsFor(): Map<string, number> {
    const bars = this.bars();
    const sessions = this.profiles.map((p) => p.session);
    const key = `${bars.length}:${bars[0]?.t ?? ""}:${sessions.join(",")}`;
    if (key !== this.anchorsKey) {
      this.anchorsKey = key;
      this.anchors = sessionAnchors(bars, sessions);
    }
    return this.anchors;
  }

  private draw(target: DrawTarget): void {
    const series = this.series;
    const chart = this.chart;
    if (this.profiles.length === 0 || series === null || chart === null) return;
    const maxTpo = Math.max(0, ...this.profiles.flatMap((p) => p.rows.map((r) => r.letters.length)));
    if (maxTpo === 0) return;
    const anchors = this.anchorsFor();
    const ts = chart.timeScale();
    const spacing = ts.options().barSpacing;
    const fullWidth = (SESSION_SECONDS / this.barSeconds) * spacing * SESSION_FILL;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      for (const p of this.profiles) {
        const t = anchors.get(p.session);
        if (t === undefined) continue;
        const x = ts.timeToCoordinate(t as Time);
        if (x === null) continue;
        const left = Math.round((x - spacing / 2) * hr);
        for (const r of p.rows) {
          const n = r.letters.length;
          if (n === 0) continue;
          const yTop = series.priceToCoordinate(r.price + p.rowSize);
          const yBot = series.priceToCoordinate(r.price);
          if (yTop === null || yBot === null) continue;
          const top = Math.round(Math.min(yTop, yBot) * vr);
          // Leave a hairline between rows once they are tall enough to show one.
          const h = Math.max(1, Math.round(Math.abs(yBot - yTop) * vr) - (Math.abs(yBot - yTop) >= 4 ? Math.round(vr) : 0));
          const w = Math.max(Math.round(hr), Math.round((n / maxTpo) * fullWidth * hr));
          ctx.fillStyle =
            r.price === p.poc ? this.colours.pocFill : r.price >= p.val && r.price <= p.vah ? this.colours.vaFill : this.colours.rowFill;
          ctx.fillRect(left, top, w, h);
        }
      }
    });
  }
}
