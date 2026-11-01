import { Fragment, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { SetupRead, SetupReadLevel, TechnicalsWatchlistRow } from "@console/shared";
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
 *
 * Two lists share the page. The default is the setups' own rows; "Tested edge" swaps in the rows of
 * the historical study's confirmed rule (TESTED_RULE), which re-walks its setup with one change, so
 * the two overlap and are never shown together. Either is cut to options-tradable names by default
 * when the file carries the label (weekly options, and $100M a day in the stock: the user trades
 * options, so a liquid stock alone is not enough), with "All names" as the way out.
 */

type View = "signals" | "open";
type Event = "both" | "entry" | "exit";
type SideFilter = "both" | "long" | "short";
type Sort = "symbol" | "recent" | "price" | "last" | "move" | "t1m" | "t6m" | "rs" | "spy" | "dvol" | "support" | "resistance";
type Dir = "asc" | "desc";

/**
 * The study's one confirmed rule (packages/technicals docs/setups.md, "Round 2"): mean reversion
 * (long) taken only on names trading at least $300M a day.
 */
const TESTED_RULE = "mr-300m";

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

/** A median dollar volume, in millions under a billion and billions over. */
function dollars(v: number | null): string {
  if (v === null) return "—";
  return v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${Math.round(v / 1e6)}M`;
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

/* ------------------------------------------------------------------ the expanded row: the read */

function Card({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="md-card">
      <header>
        <h4>{title}</h4>
        {aside}
      </header>
      {children}
    </section>
  );
}

function Kv({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="md-kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function num(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

function signed(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
}

const VERDICT_TONE: Record<SetupRead["direction"]["verdict"], string> = {
  agrees: "pnl-pos",
  conflicts: "pnl-neg",
  mixed: "",
  unread: "muted",
};

function levelText(l: SetupReadLevel | null): ReactNode {
  if (l === null) return <span className="muted">—</span>;
  return (
    <>
      {money(l.value)} <span className="muted">({pct(l.pct)} · {signed(l.atr, 1)} ATR · {signed(l.sigma, 2)}σ)</span>
    </>
  );
}

/**
 * The read behind a row, as a lead line and a row of cards: the setup's rule, its record in the
 * historical study, the direction, the volatility, the levels and the structure lean. Every word and
 * figure is `setup_read.py`'s; this lays them out.
 */
export function ReadDetail({ r }: { r: TechnicalsWatchlistRow }) {
  const rd = r.read;
  if (rd === null) return <p className="muted">No read for this row yet: the next chart run writes one.</p>;
  const v = rd.volatility;
  const d = rd.direction;
  const rec = rd.record;
  return (
    <div className="md-detail">
      <p className="md-lead">
        {FAMILY_SHORT[r.family] ?? r.setupName} {r.side} · direction{" "}
        <span className={VERDICT_TONE[d.verdict]}>{d.verdict}</span> · IV {v.regime}
        {v.regime !== "unread" ? ` (rank ${num(v.ivRank, 0)}, percentile ${num(v.ivPct, 0)})` : ""} · lean:{" "}
        <strong>{rd.lean.structure ?? "undecided"}</strong>
        {rec ? <span className="muted"> · tested {rec.verdict}</span> : null}
      </p>
      <div className="md-cards">
        <Card title="the setup">
          <p style={{ margin: 0 }}>{rd.setup.rule ?? "—"}</p>
          {rd.setup.change && <p className="muted" style={{ margin: "4px 0 0" }}>Tested change: {rd.setup.change}</p>}
          <Kv
            rows={[
              ["typical hold", rd.setup.medianHold === null ? "—" : `${rd.setup.medianHold} sessions (median, every drawn trade)`],
              ["status", r.status === "open" ? `open since ${r.entryDate}` : `exited ${r.exitDate ?? "—"} · ${r.reason ?? ""}`],
              ["target", levelText(rd.levels.target)],
            ]}
          />
        </Card>
        <Card title="its record" aside={rec ? <span className={rec.edgeR !== null && rec.edgeR > 0 ? "chip" : "chip pf-out"}>{rec.verdict}</span> : undefined}>
          {rec === null ? (
            <p className="muted">not in the historical study</p>
          ) : (
            <Kv
              rows={[
                ["entries", rec.entries === null ? "—" : rec.entries.toLocaleString()],
                ["net per trade", `${signed(rec.netR, 3)} R`],
                ["vs random entry", `${signed(rec.edgeR, 3)} R (t ${num(rec.t, 1)})`],
                ...(rec.hit !== null ? ([["hit rate", `${Math.round(rec.hit * 100)}%`]] as Array<[string, ReactNode]>) : []),
              ]}
            />
          )}
          <p className="muted" style={{ margin: "4px 0 0" }}>
            2011–2026, R per ATR(14) net of costs, stock fills at the next open — not options.
          </p>
        </Card>
        <Card title="direction" aside={<span className={`chip ${VERDICT_TONE[d.verdict]}`}>{d.verdict}</span>}>
          <Kv
            rows={[
              ["1M / 6M trend", `${r.trend1mLabel ?? "—"} / ${r.trend6mLabel ?? "—"}`],
              [
                "trend vs side",
                d.counterTrend
                  ? "counter-trend by design (mean reversion fades the move); not counted"
                  : d.trendAgrees
                    ? "agrees"
                    : d.trendAgainst
                      ? "against"
                      : "split",
              ],
              ["RS rank", r.rs === null ? "—" : `${r.rs}/10`],
              ["1M vs SPY", r.vsSpy1m === null ? "—" : `${signed(r.vsSpy1m, 1)} pts`],
              [
                "signal board",
                d.board === null
                  ? "not on the board"
                  : d.board.side === null
                    ? "tied"
                    : `${d.board.side}, ${d.board.count ?? 0} methods (${d.board.bullish ?? 0} bull / ${d.board.bearish ?? 0} bear)`,
              ],
            ]}
          />
          {d.board && d.board.tags.length > 0 && (
            <p className="muted" style={{ margin: "4px 0 0" }}>
              {d.board.tags.join(" · ")}
            </p>
          )}
        </Card>
        <Card title="volatility" aside={<span className="chip">{v.regime}</span>}>
          <Kv
            rows={[
              ["implied / 30d historical", `${num(v.iv)}% / ${num(v.hv30)}%${v.ivHv === null ? "" : ` (×${num(v.ivHv, 2)})`}`],
              ["IV rank / percentile", `${num(v.ivRank, 0)} / ${num(v.ivPct, 0)}`],
              ["cycle monthly", v.cycle === null ? "—" : `${v.cycle.expiry} (${v.cycle.days} days)`],
              ["expected move", v.expectedMove === null ? "—" : `±${money(v.expectedMove.dollars)} (±${num(v.expectedMove.pct)}%)`],
              [
                "earnings",
                v.earnings === null ? (
                  "—"
                ) : (
                  <span className={v.earningsInCycle ? "pnl-neg" : undefined}>
                    {v.earnings}
                    {v.earningsInCycle ? " · inside the cycle" : ""}
                  </span>
                ),
              ],
              ["ex-dividend", v.exDividend ?? "—"],
            ]}
          />
        </Card>
        <Card title="levels" aside={<span className="muted">ATR {rd.levels.atr14 === null ? "—" : money(rd.levels.atr14)}</span>}>
          <Kv
            rows={[
              ["resistance", levelText(rd.levels.resistance)],
              ["support", levelText(rd.levels.support)],
            ]}
          />
          <p className="muted" style={{ margin: "4px 0 0" }}>σ is the expected move to the cycle monthly: a level past 1σ is outside the likely range.</p>
        </Card>
        <Card title="structure lean" aside={<span className="chip">a lean, not a trade</span>}>
          <p style={{ margin: 0 }}>
            <strong>{rd.lean.structure ?? "undecided"}</strong> — {rd.lean.why}
          </p>
          {rd.lean.legs.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: "1.1rem" }}>
              {rd.lean.legs.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          )}
          {rd.lean.cautions.length > 0 && (
            <ul className="pnl-neg" style={{ margin: "4px 0 0", paddingLeft: "1.1rem" }}>
              {rd.lean.cautions.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {rd.lean.expiry
              ? `Strikes listed for ${rd.lean.expiry}, from end-of-day data: re-quote before acting.`
              : "From end-of-day data: re-quote before acting. No contract is chosen here."}
          </p>
        </Card>
      </div>
    </div>
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

/** Each sortable column's value; Symbol sorts by name, below. */
const SORT_VALUE: Record<Sort, (l: Line) => number | null> = {
  symbol: () => null,
  recent: (l) => l.ago,
  // The line's own close: the entry's, or the exit's on an exit line.
  price: (l) => l.price,
  last: (l) => l.row.lastClose,
  rs: (l) => l.row.rs,
  move: (l) => l.row.movePct,
  spy: (l) => l.row.vsSpy1m,
  dvol: (l) => l.row.dollarVolume,
  t1m: (l) => l.row.trend1m,
  t6m: (l) => l.row.trend6m,
  // A level sorts by its distance from the last close, whichever side of it the level is.
  support: (l) => distance(l.row.support?.pct ?? null),
  resistance: (l) => distance(l.row.resistance?.pct ?? null),
};

function distance(pct: number | null): number | null {
  return pct === null ? null : Math.abs(pct);
}

/** The direction a column sorts on its first click: A-Z for Symbol, newest first for Ago, nearest first
 *  for a level, largest first for the rest. */
const FIRST_DIR: Record<Sort, Dir> = {
  symbol: "asc",
  recent: "asc",
  price: "desc",
  last: "desc",
  move: "desc",
  t1m: "desc",
  t6m: "desc",
  rs: "desc",
  spy: "desc",
  dvol: "desc",
  support: "asc",
  resistance: "asc",
};


/** Either direction keeps a missing value at the bottom; ties go by symbol. */
function compare(sort: Sort, dir: Dir) {
  const pick = SORT_VALUE[sort];
  if (sort === "symbol") {
    // Same-name lines (an entry and its exit) keep the newest first.
    return (a: Line, b: Line) =>
      (dir === "asc" ? a.row.symbol.localeCompare(b.row.symbol) : b.row.symbol.localeCompare(a.row.symbol)) ||
      (a.ago ?? Infinity) - (b.ago ?? Infinity);
  }
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

/** The row's columns, the expander included (2026-10-30: 10 of what were 18). */
const COLUMNS = 10;

export function SetupsPage() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useSetupsWatchlist();
  // The one row opened out into its read; a row's key, so it follows the row through a sort.
  const [open, setOpen] = useState<string | null>(null);

  const view: View = params.get("view") === "open" ? "open" : "signals";
  const windowParam = Number(params.get("window"));
  const win = (WINDOWS as readonly number[]).includes(windowParam) ? windowParam : 5;
  const eventParam = params.get("event");
  const event: Event = eventParam === "entry" || eventParam === "exit" ? eventParam : "both";
  const familyParam = params.get("setup") ?? "all";
  const family = (FAMILIES as readonly string[]).includes(familyParam) ? familyParam : "all";
  const sideParam = params.get("side");
  const side: SideFilter = sideParam === "long" || sideParam === "short" ? sideParam : "both";
  const tested = params.get("tested") === "1";
  const agree = params.get("agree") === "1";
  const strong = params.get("rs7") === "1";
  // Options-tradable names only, unless asked for all -- and only where the file carries a label:
  // with none, every row's flag is unknown and the cut would empty the page.
  const labelDay = data?.optionsLabelDay ?? null;
  const tradableOnly = labelDay !== null && params.get("names") !== "all";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const sortParam = params.get("sort");
  const sort: Sort = sortParam !== null && sortParam in SORT_VALUE ? (sortParam as Sort) : "recent";
  const dirParam = params.get("dir");
  const dir: Dir = dirParam === "asc" || dirParam === "desc" ? dirParam : FIRST_DIR[sort];

  // Every control is in the URL, so a filtered view is a link; a default is left out of it.
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  // The tested rule fixes its own setup and side, and Trend agrees is off for it: an oversold
  // entry never has a positive 1-month trend, so that filter would hide every one of its rows.
  const rows = (data?.rows ?? []).filter(
    (r) =>
      (tested ? r.tested === TESTED_RULE : r.tested === null) &&
      (tested || family === "all" || r.family === family) &&
      (tested || side === "both" || r.side === side) &&
      (tested || !agree || r.trendAgrees === true) &&
      (!tradableOnly || r.optionsTradable === true) &&
      (!strong || (r.rs !== null && r.rs >= 7)) &&
      (q === "" || r.symbol.includes(q)),
  );
  const agreeHidesReversion = !tested && agree && (family === "all" || family === "reversion") && side !== "short";
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
  const sortHead = (key: Sort, text: string, title?: string, num = true) => (
    <th className={num ? "num" : undefined} title={title} aria-sort={sort === key ? (dir === "asc" ? "ascending" : "descending") : undefined}>
      <button
        type="button"
        className="th-sort"
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
        <Flag
          on={tested}
          onChange={(on) => set("tested", on ? "1" : null)}
          title="Only the rule the historical study confirmed: mean reversion (long) on names trading at least $300M a day"
        >
          Tested edge
        </Flag>
        {!tested && (
          <>
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
          </>
        )}
        {labelDay !== null ? (
          <Flag
            on={tradableOnly}
            onChange={(on) => set("names", on ? null : "all")}
            title={`Only names that list weekly options (as of ${labelDay}) and whose stock trades at least $100M a day (50-session median). Off shows every name.`}
          >
            Options-tradable
          </Flag>
        ) : (
          <span className="chip muted" title="The watchlist carries no options-tradable label, so every name is shown">
            all names · no options label
          </span>
        )}
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

      {tested && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Mean reversion (long), taken only when the name trades at least $300M a day — the 50-session median of close ×
          volume, to the session before the entry. The one change to a setup the historical study confirmed on names it had
          not seen (2011–2026, stock fills at the next open, not options). Each symbol opens the mean-reversion chart, whose
          arrows include these.
        </p>
      )}
      {agreeHidesReversion && (
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Trend agrees hides every long mean-reversion signal: an oversold entry never has a positive 1-month trend score.
        </p>
      )}
      {isError && <p className="muted">Could not read the setups watchlist.</p>}
      {isLoading && <p className="muted">Reading the watchlist…</p>}
      {data && data.rows.length === 0 && <p className="muted">No watchlist yet; the technicals report job writes it with the chart files.</p>}
      {data && data.rows.length > 0 && lines.length === 0 && <p className="muted">Nothing matches these filters.</p>}

      {lines.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th aria-label="read" />
                {sortHead("symbol", "Symbol", undefined, false)}
                <th title="The setup, and its side">Setup</th>
                <th>{view === "open" ? "Status" : "Event"}</th>
                {sortHead("recent", view === "open" ? "Entered" : "Date", "The event's date, and sessions since it; sorts by sessions since", false)}
                {sortHead(
                  "move",
                  "Move",
                  "Close to close in the trade's direction: exit against entry, or the last close against entry while open; a fall is positive for a short. Not P&L: no fills, no costs.",
                )}
                {sortHead("t1m", "1M", "Our 1-month trend score and label", false)}
                {sortHead("rs", "RS", "Relative strength 1-10: a decile across the whole US market of half the 1-month return plus the 6-month return. Not a comparison with SPY.")}
                {tested && sortHead("dvol", "$ Vol", "The 50-session median of close × volume, to the session before the entry: what the tested rule filters on")}
                {sortHead("support", "Support", "The nearest of our own support levels (swing lows in our bars) below the last close; sorts nearest first")}
                {sortHead("resistance", "Resistance", "The nearest of our own resistance levels (swing highs in our bars) above the last close; sorts nearest first")}
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => {
                const r = l.row;
                const key = `${r.symbol}-${r.setup}-${r.tested ?? ""}-${r.entryDate}-${l.kind}`;
                const isOpen = open === key;
                return (
                  <Fragment key={key}>
                  <tr>
                    <td>
                      <button
                        type="button"
                        className="btn btn-quiet"
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? "Close" : "Open"} the read for ${r.symbol}`}
                        title="The read: why it fired, its record, direction, volatility, levels and a structure lean"
                        onClick={() => setOpen(isOpen ? null : key)}
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                    </td>
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
                      {!tradableOnly && r.optionsTradable === false && (
                        <span className="muted" title="Not options-tradable: no weekly options, or the stock trades under $100M a day">
                          {" "}
                          · no opts
                        </span>
                      )}
                    </td>
                    <td>
                      {FAMILY_SHORT[r.family] ?? r.setupName} <span className="muted">· {r.side}</span>
                    </td>
                    <td>{eventText(l)}</td>
                    <td>
                      {l.date} <span className="muted">{l.ago === null ? "" : `${String(l.ago)}d`}</span>
                    </td>
                    <td className={`num ${tone(r.movePct)}`}>{pct(r.movePct)}</td>
                    <TrendCell label={r.trend1mLabel} score={r.trend1m} />
                    <td className="num">{r.rs ?? "—"}</td>
                    {tested && <td className="num">{dollars(r.dollarVolume)}</td>}
                    <LevelCell near={r.support} />
                    <LevelCell near={r.resistance} />
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={COLUMNS + (tested ? 1 : 0)}>
                        {/* Out of the row since 2026-10-30 (docs/console-review-plan.md batch 3). */}
                        <table className="data-table secondary-cells" aria-label={`${r.symbol} prices and context`}>
                          <thead>
                            <tr>
                              <th className="num" title={view === "open" ? "The entry close" : "The close that fired it"}>
                                {view === "open" ? "Entry" : "Price"}
                              </th>
                              <th className="num" title="The last close">
                                Last
                              </th>
                              {view === "open" ? (
                                <th className="num" title="The pullback's target: the prior 20-session high (long) or low (short)">
                                  Target
                                </th>
                              ) : (
                                <th>Status</th>
                              )}
                              <th title="Our 6-month trend score and label">6M</th>
                              <th className="num" title="The 21-session return less SPY's over the same sessions, in points">
                                1M vs SPY
                              </th>
                              {!tested && (
                                <th className="num" title="The 50-session median of close × volume, to the session before the entry">
                                  $ Vol
                                </th>
                              )}
                            </tr>
                          </thead>
                          <tbody>
                            <tr>
                              <td className="num">{money(l.price)}</td>
                              <td className="num">{money(r.lastClose)}</td>
                              {view === "open" ? <td className="num">{money(r.target)}</td> : <td className="muted">{r.status}</td>}
                              <TrendCell label={r.trend6mLabel} score={r.trend6m} />
                              <td className={`num ${tone(r.vsSpy1m)}`}>
                                {r.vsSpy1m === null ? "—" : `${r.vsSpy1m > 0 ? "+" : r.vsSpy1m < 0 ? "−" : ""}${Math.abs(r.vsSpy1m).toFixed(1)} pts`}
                              </td>
                              {!tested && <td className="num">{dollars(r.dollarVolume)}</td>}
                            </tr>
                          </tbody>
                        </table>
                        <ReadDetail r={r} />
                      </td>
                    </tr>
                  )}
                  </Fragment>
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
        SPY — the 1M vs SPY column is. $ Vol is the 50-session median dollar volume before the entry. Support and
        resistance are our own swing levels. None of it is vendor data: the vendor only checks our trend scores and rank.
        Options-tradable means the name lists weekly options and its stock trades at least $100M a day.
      </p>
    </section>
  );
}
