import { useSearchParams } from "react-router-dom";

/**
 * A history table's date range, kept in the page address (`?from=YYYY-MM-DD&to=YYYY-MM-DD`) so a
 * filtered view survives a reload and can be bookmarked or shared -- the same promise the module
 * frame makes for every page. Either side may be open. Written with `replace`, so stepping through
 * dates does not bury the back button under one history entry per keystroke.
 */

const ISO = /^\d{4}-\d{2}-\d{2}$/;

export function useUrlDateRange(): {
  from: string | null;
  to: string | null;
  setRange: (from: string | null, to: string | null) => void;
} {
  const [params, setParams] = useSearchParams();
  const read = (k: string) => {
    const v = params.get(k);
    return v !== null && ISO.test(v) ? v : null;
  };
  const setRange = (from: string | null, to: string | null) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of [
      ["from", from],
      ["to", to],
    ] as const) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };
  return { from: read("from"), to: read("to"), setRange };
}

/** Today's date in New York -- the suite's session calendar, whatever the viewer's timezone. */
export function etToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  return parts;
}

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The presets, each an inclusive [from, to] ending today (ET). Pure, for the tests. */
export function presetRange(preset: "week" | "month" | "30d", today: string): [string, string] {
  if (preset === "30d") return [shift(today, -29), today];
  if (preset === "month") return [`${today.slice(0, 8)}01`, today];
  const dow = new Date(`${today}T12:00:00Z`).getUTCDay(); // 0 Sunday
  return [shift(today, -((dow + 6) % 7)), today];
}

export function DateRangeBar() {
  const { from, to, setRange } = useUrlDateRange();
  const today = etToday();
  const presets: Array<["week" | "month" | "30d", string]> = [
    ["week", "this week"],
    ["month", "this month"],
    ["30d", "30 days"],
  ];
  const activePreset = presets.find(([p]) => {
    const [f, t] = presetRange(p, today);
    return from === f && to === t;
  })?.[0];
  return (
    <div className="date-range" role="group" aria-label="date range">
      <div className="mode-toggle">
        {presets.map(([p, label]) => (
          <button
            key={p}
            type="button"
            className={activePreset === p ? "mode-btn active" : "mode-btn"}
            onClick={() => setRange(...presetRange(p, today))}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          className={from === null && to === null ? "mode-btn active" : "mode-btn"}
          onClick={() => setRange(null, null)}
          title="every session in the page's era"
        >
          all
        </button>
      </div>
      <label className="muted">
        from
        <input
          className="text-input"
          type="date"
          value={from ?? ""}
          max={to ?? undefined}
          onChange={(e) => setRange(e.target.value || null, to)}
          aria-label="from date"
        />
      </label>
      <label className="muted">
        to
        <input
          className="text-input"
          type="date"
          value={to ?? ""}
          min={from ?? undefined}
          onChange={(e) => setRange(from, e.target.value || null)}
          aria-label="to date"
        />
      </label>
    </div>
  );
}
