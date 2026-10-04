import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { TechnicalsWatchlistRow } from "@console/shared";
import { useSetupsWatchlist } from "../../lib/api";

/**
 * The setups watchlist: every entry and exit the technicals chart setups made lately, long and
 * short, and every position they still hold, across every charted name. Each symbol opens its chart
 * with that setup and side selected, so a row can be checked against its arrows.
 *
 * Everything shown is `packages/technicals`' setups-index.json, built from the chart files: the
 * positions, our trend scores and labels, the rank, the month against SPY and our own nearest levels
 * -- no vendor data; the vendor only checks the scores and the rank. The page filters, sorts and
 * splits a position into its entry and exit rows; it decides nothing. "Move" is close to close in the
 * trade's direction and is never called P&L.
 */

type View = "signals" | "open";
type Event = "both" | "entry" | "exit";
type SideFilter = "both" | "long" | "short";
type Sort = "recent" | "rs" | "move" | "spy";
type Dir = "asc" | "desc";

const WINDOWS = [1, 5, 20] as const;
const FAMILIES = ["all", "trend", "pullback", "reversion", "breakout"] as const;
const FAMILY_SHORT: Record<string, string> = {
  all: "All setups",
  trend: "Trend",
  pullback: "Pullback",
  reversion: "Mean rev",
  breakout: "Breakout",
};

/** One line of the table: a position's entry or exit (signals view), or the position (open view). */
interface Line {
  row: TechnicalsWatchlistRow;
  kind: "entry" | "exit" | "open";
  date: string;
  ago: number | null;
  price: number | null;
}

function money(v: number | null | undefined): string {
  return v === null || v === undefined
    ? "—"
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;
}

function tone(v: number | null | undefined): string {
  return v === null || v === undefined || v === 0 ? "" : v > 0 ? "pnl-pos" : "pnl-neg";
}

/** A short's entry is "▼ short" and its exit "▲ cover"; a long's are "▲ entry" and "▼ exit". */
function eventText(l: Line): string {
  const short = l.row.side === "short";
  if (l.kind === "open") return "open";
  if (l.kind === "entry") return short ? "▼ short" : "▲ entry";
  return `${short ? "▲ cover" : "▼ exit"} · ${l.row.reason ?? ""}`;
}

function chartLink(r: TechnicalsWatchlistRow): string {
  const side = r.side === "short" ? "&side=short" : "";
  return `/charts/technicals?symbol=${encodeURIComponent(r.symbol)}&setup=${r.family}${side}`;
}

function TrendCell({ label, score }: { label: string | null; score: number | null }) {
  if (label === null) return <td className="muted">—</td>;
  const cls = label.endsWith("Bullish") ? "pnl-pos" : label.endsWith("Bearish") ? "pnl-neg" : "";
  return (
    <td className={cls} title={`Our trend score ${score ?? "—"} on −4..+4, and our five-step label for it`}>
      {label} <span className="muted">{score === null ? "" : `${score > 0 ? "+" : ""}${score}`}</span>
    </td>
  );
}

function LevelCell({ near }: { near: TechnicalsWatchlistRow["support"] }) {
  if (near === null) return <td className="muted">—</td>;
  return (
    <td className="num">
      {money(near.value)} <span className="muted">({pct(near.pct)})</span>
    </td>
  );
}

function Toggle<T extends string | number>({
  value,
  options,
  onChange,
  label,
  render,
}: {
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
  render?: (v: T) => string;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label={label} style={{ marginLeft: 0 }}>
      {options.map((o) => (
        <button key={String(o)} type="button" className={value === o ? "mode-btn active" : "mode-btn"} onClick={() => onChange(o)}>
          {render ? render(o) : String(o)}
        </button>
      ))}
    </div>
  );
}

function Flag({ on, onChange, children, title }: { on: boolean; onChange: (on: boolean) => void; children: ReactNode; title: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      <button type="button" title={title} className={on ? "mode-btn active" : "mode-btn"} onClick={() => onChange(!on)}>
        {children}
      </button>
    </div>
  );
}

/** Each sortable column's value. */
const SORT_VALUE: Record<Sort, (l: Line) => number | null> = {
  recent: (l) => l.ago,
  rs: (l) => l.row.rs,
  move: (l) => l.row.movePct,
  spy: (l) => l.row.vsSpy1m,
};

/** The direction a column sorts on its first click: newest first for Ago, largest first for the rest. */
const FIRST_DIR: Record<Sort, Dir> = { recent: "asc", rs: "desc", move: "desc", spy: "desc" };

/** Either direction keeps a missing value at the bottom; ties go by symbol. */
function compare(sort: Sort, dir: Dir) {
  const pick = SORT_VALUE[sort];
  return (a: Line, b: Line) => {
    const x = pick(a);
    const y = pick(b);
    if (x === null || y === null) {
      if (x !== y) return x === null ? 1 : -1;
    } else if (x !== y) {
      return dir === "asc" ? x - y : y - x;
    }
    return a.row.symbol.localeCompare(b.row.symbol);
  };
}

export function SetupsPage() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useSetupsWatchlist();

  const view: View = params.get("view") === "open" ? "open" : "signals";
  const windowParam = Number(params.get("window"));
  const win = (WINDOWS as readonly number[]).includes(windowParam) ? windowParam : 5;
  const eventParam = params.get("event");
  const event: Event = eventParam === "entry" || eventParam === "exit" ? eventParam : "both";
  const familyParam = params.get("setup") ?? "all";
  const family = (FAMILIES as readonly string[]).includes(familyParam) ? familyParam : "all";
  const sideParam = params.get("side");
  const side: SideFilter = sideParam === "long" || sideParam === "short" ? sideParam : "both";
  const agree = params.get("agree") === "1";
  const strong = params.get("rs7") === "1";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const sortParam = params.get("sort");
  const sort: Sort = sortParam === "rs" || sortParam === "move" || sortParam === "spy" ? sortParam : "recent";
  const dirParam = params.get("dir");
  const dir: Dir = dirParam === "asc" || dirParam === "desc" ? dirParam : FIRST_DIR[sort];

  // Every control is in the URL, so a filtered view is a link; a default is left out of it.
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const rows = (data?.rows ?? []).filter(
    (r) =>
      (family === "all" || r.family === family) &&
      (side === "both" || r.side === side) &&
      (!agree || r.trendAgrees === true) &&
      (!strong || (r.rs !== null && r.rs >= 7)) &&
      (q === "" || r.symbol.includes(q)),
  );
  const lines: Line[] =
    view === "open"
      ? rows
          .filter((r) => r.status === "open")
          .map((r) => ({ row: r, kind: "open", date: r.entryDate, ago: r.entryAgo, price: r.entryPrice }))
      : rows.flatMap((r) => {
          const out: Line[] = [];
          if (event !== "exit" && r.entryAgo !== null && r.entryAgo < win) {
            out.push({ row: r, kind: "entry", date: r.entryDate, ago: r.entryAgo, price: r.entryPrice });
          }
          if (event !== "entry" && r.exitDate !== null && r.exitAgo !== null && r.exitAgo < win) {
            out.push({ row: r, kind: "exit", date: r.exitDate, ago: r.exitAgo, price: r.exitPrice });
          }
          return out;
        });
  lines.sort(compare(sort, dir));
  const names = new Set(lines.map((l) => l.row.symbol)).size;

  // A header is a button, so the sort is reachable from the keyboard as well as the mouse.
  const sortHead = (key: Sort, text: string, title: string) => (
    <th className="num" title={title} aria-sort={sort === key ? (dir === "asc" ? "ascending" : "descending") : undefined}>
      <button
        type="button"
        style={{ background: "none", border: 0, padding: 0, color: "inherit", font: "inherit", cursor: "pointer" }}
        onClick={() => {
          // A second click on the column in use reverses it; another column starts its own way.
          const next = new URLSearchParams(params);
          const nextDir: Dir = key === sort ? (dir === "asc" ? "desc" : "asc") : FIRST_DIR[key];
          if (key === "recent") next.delete("sort");
          else next.set("sort", key);
          if (nextDir === FIRST_DIR[key]) next.delete("dir");
          else next.set("dir", nextDir);
          setParams(next, { replace: true });
        }}
      >
        {text}
        {sort === key ? (dir === "asc" ? " ▴" : " ▾") : ""}
      </button>
    </th>
  );

  return (
    <section className="card">
      <div className="card-head">
        <h2>Setup signals</h2>
        <span className="chip">record-only</span>
        {data?.session && <span className="card-asof">close of {data.session}</span>}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <Toggle
          value={view}
          options={["signals", "open"] as const}
          onChange={(v) => set("view", v === "signals" ? null : v)}
          label="view"
          render={(v) => (v === "signals" ? "Entries & exits" : "Open positions")}
        />
        {view === "signals" && (
          <>
            <Toggle value={win} options={WINDOWS} onChange={(v) => set("window", v === 5 ? null : String(v))} label="window" render={(v) => `${v}d`} />
            <Toggle
              value={event}
              options={["both", "entry", "exit"] as const}
              onChange={(v) => set("event", v === "both" ? null : v)}
              label="event"
              render={(v) => (v === "both" ? "Both" : v === "entry" ? "Entries" : "Exits")}
            />
          </>
        )}
        <Toggle value={family} options={FAMILIES} onChange={(v) => set("setup", v === "all" ? null : v)} label="setup" render={(v) => FAMILY_SHORT[v] ?? v} />
        <Toggle
          value={side}
          options={["both", "long", "short"] as const}
          onChange={(v) => set("side", v === "both" ? null : v)}
          label="side"
          render={(v) => (v === "both" ? "Long & short" : v === "long" ? "Long" : "Short")}
        />
        <Flag on={agree} onChange={(on) => set("agree", on ? "1" : null)} title="Only trades whose 1M and 6M trend scores are both on the trade's side of zero: above it for a long, below for a short">
          Trend agrees
        </Flag>
        <Flag on={strong} onChange={(on) => set("rs7", on ? "1" : null)} title="Only names with a relative-strength rank of 7 or more (the top 30% of the market)">
          RS ≥ 7
        </Flag>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip">
          {lines.length} {view === "open" ? "open" : lines.length === 1 ? "signal" : "signals"} · {names} {names === 1 ? "name" : "names"}
        </span>
      </div>

      {isError && <p className="muted">Could not read the setups watchlist.</p>}
      {isLoading && <p className="muted">Reading the watchlist…</p>}
      {data && data.rows.length === 0 && <p className="muted">No watchlist yet; the technicals report job writes it with the chart files.</p>}
      {data && data.rows.length > 0 && lines.length === 0 && <p className="muted">Nothing matches these filters.</p>}

      {lines.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Setup</th>
                <th>Side</th>
                <th>{view === "open" ? "Status" : "Event"}</th>
                <th>{view === "open" ? "Entered" : "Date"}</th>
                {sortHead("recent", "Ago", "Sessions since the event")}
                <th className="num" title={view === "open" ? "The entry close" : "The close that fired it"}>
                  {view === "open" ? "Entry" : "Price"}
                </th>
                <th className="num">Last</th>
                {sortHead(
                  "move",
                  "Move",
                  "Close to close in the trade's direction: exit against entry, or the last close against entry while open; a fall is positive for a short. Not P&L: no fills, no costs.",
                )}
                {view === "open" ? (
                  <th className="num" title="The pullback's target: the prior 20-session high (long) or low (short)">
                    Target
                  </th>
                ) : (
                  <th>Status</th>
                )}
                <th title="Our 1-month trend score and label">1M</th>
                <th title="Our 6-month trend score and label">6M</th>
                {sortHead("rs", "RS", "Relative strength 1-10: a decile across the whole US market of half the 1-month return plus the 6-month return. Not a comparison with SPY.")}
                {sortHead("spy", "1M vs SPY", "The 21-session return less SPY's over the same sessions, in points")}
                <th className="num" title="The nearest of our own support levels (swing lows in our bars) below the last close">
                  Support
                </th>
                <th className="num" title="The nearest of our own resistance levels (swing highs in our bars) above the last close">
                  Resistance
                </th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => {
                const r = l.row;
                return (
                  <tr key={`${r.symbol}-${r.setup}-${r.entryDate}-${l.kind}`}>
                    <td>
                      <Link className="module-link" to={chartLink(r)} title={`Open ${r.symbol}'s chart with ${r.setupName} selected`}>
                        {r.symbol}
                      </Link>
                      {data?.session && r.session !== data.session && (
                        <span className="muted" title="This name's bars end before the latest session">
                          {" "}
                          (to {r.session})
                        </span>
                      )}
                    </td>
                    <td>{FAMILY_SHORT[r.family] ?? r.setupName}</td>
                    <td className="muted">{r.side}</td>
                    <td>{eventText(l)}</td>
                    <td>{l.date}</td>
                    <td className="num">{l.ago ?? "—"}</td>
                    <td className="num">{money(l.price)}</td>
                    <td className="num">{money(r.lastClose)}</td>
                    <td className={`num ${tone(r.movePct)}`}>{pct(r.movePct)}</td>
                    {view === "open" ? <td className="num">{money(r.target)}</td> : <td className="muted">{r.status}</td>}
                    <TrendCell label={r.trend1mLabel} score={r.trend1m} />
                    <TrendCell label={r.trend6mLabel} score={r.trend6m} />
                    <td className="num">{r.rs ?? "—"}</td>
                    <td className={`num ${tone(r.vsSpy1m)}`}>
                      {r.vsSpy1m === null ? "—" : `${r.vsSpy1m > 0 ? "+" : r.vsSpy1m < 0 ? "−" : ""}${Math.abs(r.vsSpy1m).toFixed(1)} pts`}
                    </td>
                    <LevelCell near={r.support} />
                    <LevelCell near={r.resistance} />
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="muted" style={{ marginTop: 8 }}>
        Signals from the four chart setups and their short mirrors, judged on daily closes; each symbol opens its chart with
        the setup and side selected. Move is close to close in the trade's direction, not P&amp;L. 1M and 6M are our trend
        scores and labels; RS is our 1–10 relative-strength rank across the whole market, which is not a comparison with
        SPY — the 1M vs SPY column is. Support and resistance are our own swing levels. None of it is vendor data: the
        vendor only checks our trend scores and rank.
      </p>
    </section>
  );
}
