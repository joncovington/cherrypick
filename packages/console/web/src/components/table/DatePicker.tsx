import { useEffect, useRef, useState } from "react";

/**
 * A history table's date-range picker (2026-09-26): one button showing the range, opening a month
 * calendar -- the first click picks the start, the second the end (either order), a click on a
 * finished range starts a new one. It replaced a pair of native `<input type="date">` fields, which
 * made you type MM/DD/YYYY behind an icon the dark theme all but hid.
 *
 * A panel, not a dialog, on the columns menu's terms: the table stays live behind it, and a click
 * outside or Escape closes it. Weeks run Monday to Sunday with the weekend dimmed, because every
 * date here is a session. Days after today (New York) cannot be picked -- nothing has happened there.
 */

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${String(y)}-${pad(m + 1)}-${pad(d)}`;

/** One month as weeks of ISO dates, Monday first; null pads the days outside the month. Pure. */
export function monthGrid(year: number, month: number): Array<Array<string | null>> {
  const first = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: Array<string | null> = [...Array<null>(first).fill(null)];
  for (let d = 1; d <= days; d++) cells.push(iso(year, month, d));
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/**
 * What a click on `day` does to the range. With no start, or a finished range, it starts a new one
 * (open-ended until the second click); with only a start, it closes the range, in either order. Pure.
 */
export function pickDay(from: string | null, to: string | null, day: string): { from: string; to: string | null; done: boolean } {
  if (from === null || to !== null) return { from: day, to: null, done: false };
  return day < from ? { from: day, to: from, done: true } : { from, to: day, done: true };
}

/** "Sep 21 – Sep 25", "from Sep 21", "to Sep 25", or null for no range. */
export function rangeLabel(from: string | null, to: string | null): string | null {
  const short = (d: string) => `${MONTHS[Number(d.slice(5, 7)) - 1]!.slice(0, 3)} ${String(Number(d.slice(8, 10)))}${d.slice(0, 4) === String(new Date().getFullYear()) ? "" : ` ${d.slice(0, 4)}`}`;
  if (from !== null && to !== null) return from === to ? short(from) : `${short(from)} – ${short(to)}`;
  if (from !== null) return `from ${short(from)}`;
  if (to !== null) return `to ${short(to)}`;
  return null;
}

export function DateRangePicker({
  from,
  to,
  today,
  onChange,
}: {
  from: string | null;
  to: string | null;
  /** ISO, New York: the last day that can be picked. */
  today: string;
  onChange: (from: string | null, to: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  // The range being picked: separate from the URL's until the second click, so a half-picked range
  // does not refetch the table on the first.
  const [draft, setDraft] = useState<{ from: string | null; to: string | null }>({ from, to });
  const [hover, setHover] = useState<string | null>(null);
  const anchor = to ?? from ?? today;
  const [view, setView] = useState({ y: Number(anchor.slice(0, 4)), m: Number(anchor.slice(5, 7)) - 1 });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current !== null && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (!open) {
      setDraft({ from, to });
      const a = to ?? from ?? today;
      setView({ y: Number(a.slice(0, 4)), m: Number(a.slice(5, 7)) - 1 });
    }
    setOpen((v) => !v);
  };
  const step = (n: number) => {
    setView(({ y, m }) => {
      const t = y * 12 + m + n;
      return { y: Math.floor(t / 12), m: ((t % 12) + 12) % 12 };
    });
  };
  const click = (day: string) => {
    const next = pickDay(draft.from, draft.to, day);
    setDraft({ from: next.from, to: next.to });
    if (next.done) {
      onChange(next.from, next.to);
      setOpen(false);
    }
  };

  // The span to paint: the draft, or the draft's start to the hovered day while picking the end.
  const lo = draft.from !== null && draft.to === null && hover !== null ? (hover < draft.from ? hover : draft.from) : draft.from;
  const hi = draft.from !== null && draft.to === null && hover !== null ? (hover < draft.from ? draft.from : hover) : draft.to;
  const label = rangeLabel(from, to);
  const atLatest = view.y * 12 + view.m >= Number(today.slice(0, 4)) * 12 + Number(today.slice(5, 7)) - 1;

  return (
    <div className="dp" ref={ref}>
      <button
        type="button"
        className={`mode-btn dp-button ${open ? "active" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={toggle}
        title="pick a start and an end day"
      >
        <span aria-hidden="true">▦</span> {label ?? "pick dates"}
      </button>
      {open && (
        <div className="dp-panel" role="group" aria-label="date range calendar">
          <div className="dp-head">
            <button type="button" className="dp-nav" aria-label="previous month" onClick={() => step(-1)}>
              ‹
            </button>
            <span className="dp-month">
              {MONTHS[view.m]} {view.y}
            </span>
            <button type="button" className="dp-nav" aria-label="next month" disabled={atLatest} onClick={() => step(1)}>
              ›
            </button>
          </div>
          <table className="dp-grid" onMouseLeave={() => setHover(null)}>
            <thead>
              <tr>
                {WEEKDAYS.map((w, i) => (
                  <th key={w} className={i >= 5 ? "dp-weekend" : undefined}>
                    {w}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {monthGrid(view.y, view.m).map((week, wi) => (
                <tr key={wi}>
                  {week.map((day, di) => {
                    if (day === null) return <td key={di} />;
                    const future = day > today;
                    const edge = day === lo || day === hi;
                    const inside = lo !== null && hi !== null && day > lo && day < hi;
                    const cls = [
                      "dp-day",
                      di >= 5 ? "dp-weekend" : "",
                      edge ? "dp-edge" : "",
                      inside ? "dp-inside" : "",
                      day === today ? "dp-today" : "",
                    ].join(" ");
                    return (
                      <td key={di}>
                        <button
                          type="button"
                          className={cls}
                          disabled={future}
                          aria-pressed={edge}
                          aria-label={day}
                          onClick={() => click(day)}
                          onMouseEnter={() => setHover(day)}
                        >
                          {Number(day.slice(8, 10))}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dp-foot">
            <span className="muted">
              {draft.from !== null && draft.to === null ? "now pick the end" : "pick the start"}
            </span>
            <button
              type="button"
              className="mode-btn"
              disabled={from === null && to === null}
              onClick={() => {
                onChange(null, null);
                setDraft({ from: null, to: null });
                setOpen(false);
              }}
            >
              clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
