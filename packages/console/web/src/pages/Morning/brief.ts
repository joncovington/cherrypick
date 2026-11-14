import type { MorningCalendar, MorningGate, MorningSectorRow, MorningVolCurvePoint } from "@console/shared";

/**
 * The morning report's layout helpers: which tab is showing, and the Brief tab's small re-shapings of
 * the pack (grouping, ordering, counting). Nothing here decides anything — the phase, the gate verdicts
 * and every figure stay the pack's; these only choose what goes where on the page.
 */

export type MorningView = "brief" | "risk" | "tape" | "charts" | "options" | "calendar" | "note";

export const MORNING_VIEWS: { id: MorningView; label: string }[] = [
  { id: "brief", label: "Brief" },
  { id: "risk", label: "Risk" },
  { id: "tape", label: "Tape" },
  { id: "charts", label: "Charts" },
  { id: "options", label: "Options" },
  { id: "calendar", label: "Calendar" },
  { id: "note", label: "Note" },
];

/** The tabs this suite shows: Charts needs technicals on, Note needs the narrative on. */
export function morningViews(opts: { technicals: boolean; narrative: boolean }): { id: MorningView; label: string }[] {
  return MORNING_VIEWS.filter((v) => (v.id === "charts" ? opts.technicals : v.id === "note" ? opts.narrative : true));
}

/** The `?view=` value, read against the tabs on show: anything else is the Brief. */
export function viewFrom(param: string | null, views: { id: MorningView }[]): MorningView {
  const hit = views.find((v) => v.id === param);
  return hit ? hit.id : "brief";
}

export type GateMark = { id: string; label: string; detail: string | null; mark: "✓" | "✗" | "?"; tone: "ok" | "warn" | "missing" };

/** One chip a gate, in the pack's order. Anything but met/not_met reads as unknown, never as met. */
export function gateMarks(gates: MorningGate[]): GateMark[] {
  return gates.map((g) => {
    if (g.status === "met") return { id: g.id, label: g.label, detail: g.detail, mark: "✓", tone: "ok" };
    if (g.status === "not_met") return { id: g.id, label: g.label, detail: g.detail, mark: "✗", tone: "warn" };
    return { id: g.id, label: g.label, detail: g.detail, mark: "?", tone: "missing" };
  });
}

export interface UpcomingDay {
  date: string;
  today: boolean;
  releases: string[];
  /** Ordered by implied move, largest first; an unpriced row keeps its place at the end. */
  earnings: { symbol: string; movePct: number | null; when: string | null }[];
}

/**
 * The calendar's next few dates that hold anything, from the pack's session on: releases and earnings
 * grouped by date. The rows are the pack's; this only groups them.
 */
export function upcomingDays(calendar: MorningCalendar | null, session: string, days = 3): UpcomingDay[] {
  if (calendar === null) return [];
  const byDate = new Map<string, UpcomingDay>();
  const day = (date: string): UpcomingDay => {
    let d = byDate.get(date);
    if (d === undefined) {
      d = { date, today: date === session, releases: [], earnings: [] };
      byDate.set(date, d);
    }
    return d;
  };
  for (const r of calendar.releases ?? []) if (r.date >= session) day(r.date).releases.push(r.name);
  for (const e of calendar.earnings?.rows ?? []) {
    if (e.date >= session) day(e.date).earnings.push({ symbol: e.symbol, movePct: e.expectedMovePct, when: e.when });
  }
  for (const d of byDate.values()) {
    d.earnings.sort((a, b) => {
      if (a.movePct === null && b.movePct === null) return 0;
      if (a.movePct === null) return 1;
      if (b.movePct === null) return -1;
      return b.movePct - a.movePct;
    });
  }
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)).slice(0, days);
}

/** "Mon 16" for a date that is not the session; the session itself reads "today". */
export function dayLabel(date: string, today: boolean): string {
  if (today) return "today";
  const d = new Date(`${date}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return date;
  const wd = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()];
  return `${wd} ${String(d.getUTCDate())}`;
}

/** Up, down and unmeasured counts across the board; a flat sector is neither up nor down. */
export function sectorCounts(board: MorningSectorRow[]): { up: number; down: number; unmeasured: number } {
  let up = 0;
  let down = 0;
  let unmeasured = 0;
  for (const r of board) {
    if (r.changePct === null) unmeasured += 1;
    else if (r.changePct > 0) up += 1;
    else if (r.changePct < 0) down += 1;
  }
  return { up, down, unmeasured };
}

/** The measured curve as polyline points across a w × h box, evenly spaced by position (as the full
 *  curve is drawn). Fewer than two measured points draw nothing. */
export function sparkPoints(curve: MorningVolCurvePoint[], w: number, h: number, pad = 4): { x: number; y: number; symbol: string }[] {
  const m = curve.filter((p) => p.value !== null);
  if (m.length < 2) return [];
  const ys = m.map((p) => p.value as number);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  return m.map((p, i) => ({
    x: pad + (i / (m.length - 1)) * (w - 2 * pad),
    y: pad + ((hi - (p.value as number)) / span) * (h - 2 * pad),
    symbol: p.symbol,
  }));
}
