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
import { buildProfile, defaultRowSize, etClock, sessionDates, SESSION_CLOSE_MIN, type MarketProfile } from "../../lib/marketProfile";

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
  role: "poc" | "va" | "ib" | "prior-poc" | "prior-va";
}

/** The price lines a pair of profiles asks for, in a stable order. */
export function profileLines({ latest, prior }: SessionProfiles): ProfileLine[] {
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

export function lineStyleFor(role: ProfileLine["role"], c: OverlayColours) {
  switch (role) {
    case "poc":
      return { color: c.poc, lineStyle: LineStyle.Solid, lineWidth: 1 as const, axisLabelVisible: true };
    case "va":
      return { color: c.va, lineStyle: LineStyle.Dashed, lineWidth: 1 as const, axisLabelVisible: true };
    case "ib":
      return { color: c.muted, lineStyle: LineStyle.Dotted, lineWidth: 1 as const, axisLabelVisible: false };
    case "prior-poc":
      return { color: c.poc, lineStyle: LineStyle.Dotted, lineWidth: 1 as const, axisLabelVisible: false };
    case "prior-va":
      return { color: c.muted, lineStyle: LineStyle.LargeDashed, lineWidth: 1 as const, axisLabelVisible: false };
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
