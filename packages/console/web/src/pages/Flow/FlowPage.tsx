import type {
  DerivedFlow,
  DerivedFlowChecks,
  DerivedFlowName,
  DerivedFlowRow,
  FlowBirdseyeRow,
  FlowSpread,
  FlowTrade,
  FlowVolOi,
  OptionsFlowDay,
} from "@console/shared";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { SortTh, sortRows, useSort, type SortState } from "../../components/DataTable";
import { fmtCount, fmtDollarsShort, fmtNum, fmtPct } from "../../lib/format";

/**
 * Options flow: QuikOptions' Hot Options Report for one session, as the capture saved it
 * (`scripts/fetch_quikoptions.py`, `docs/quikoptions-plan.md`).
 *
 * Everything shown is the site's cell or the capture's derivation — a spread's direction, which
 * trades print together, an outright's premium, the names across tables, the largest trade. The
 * page lays them out and decides nothing. Tone comes only from the site's own word on a trade
 * (Bullish/Bearish) or a spread's direction, never from a threshold here.
 *
 * Every card title carries the session date. The daily Discord series is pictures of these cards
 * (`tools/ui-check.mjs --card`), so a card still showing another day fails the capture rather than
 * posting under today's caption — and `--card` needs an SVG in the card, which the bars provide.
 * Renaming a card title breaks that capture.
 */

export const TABLE_LABEL: Record<string, string> = {
  birdseye: "birdseye",
  outrights: "outrights",
  sweeps: "sweeps",
  spreads: "spreads",
  voloi: "vol/OI",
  openings: "openings",
};

const BAND_ORDER = ["1", "2-10", "11-99", "100+"] as const;
const BAND_LABEL: Record<string, string> = { "1": "1 lot", "2-10": "2–10", "11-99": "11–99", "100+": "100+" };
// Lighter to darker: a single-lot trade is the faintest band, a block the strongest.
// A colour per band, not four shades of one: the 100+ band is a sliver (0.2-1% of trades on
// 2026-10-02) and a fourth shade of red vanished beside the third. Every band that has trades also
// gets MIN_BAND pixels, so the block trades are always visible.
const BAND_FILL: Record<string, string> = {
  "1": "var(--text-dim)",
  "2-10": "color-mix(in srgb, var(--neg) 50%, transparent)",
  "11-99": "var(--accent)",
  "100+": "var(--warn)",
};
const MIN_BAND = 3;

/** The four bands' colours, for a column header. */
function BandKey() {
  return (
    <span className="band-key">
      {BAND_ORDER.map((b) => (
        <span key={b}>
          <svg width={8} height={8} aria-hidden="true">
            <rect width={8} height={8} fill={BAND_FILL[b]} />
          </svg>{" "}
          {BAND_LABEL[b]}
        </span>
      ))}
    </span>
  );
}

/** `15 Jan 27 16C` from the capture's ISO expiry, strike and call/put. */
export function contractLabel(expires: string | null, strike: number | null, cp: "call" | "put" | null): string {
  const d = expires !== null ? new Date(`${expires}T12:00:00Z`) : null;
  const exp =
    d !== null && !Number.isNaN(d.getTime())
      ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" })
      : "—";
  const k = strike === null ? "—" : String(strike);
  return `${exp} ${k}${cp === "call" ? "C" : cp === "put" ? "P" : ""}`;
}

function sideTone(sentiment: string | undefined): string {
  return sentiment === "Bullish" ? "pnl-pos" : sentiment === "Bearish" ? "pnl-neg" : "muted";
}

/** The site's call on a trade, with where the fill sat and the edge in the tooltip. */
function SideCell({ trade }: { trade: FlowTrade }) {
  const s = trade.side;
  if (s === null) return <span className="muted">—</span>;
  const edge = s.edge === null ? "" : ` · edge ${fmtNum(s.edge)}`;
  return (
    <span className={sideTone(s.sentiment)} title={`${s.sentiment} — ${s.fill}${edge}`}>
      {s.sentiment.toLowerCase()} <span className="muted">{s.fill.toLowerCase()}</span>
    </span>
  );
}

/** A magnitude bar: width in proportion to the largest value shown in the column, toned by side. */
function Bar({ value, max, tone, width = 72 }: { value: number | null; max: number; tone?: string; width?: number }) {
  const w = value === null || max <= 0 ? 0 : Math.max(1, (Math.abs(value) / max) * width);
  const fill = tone === "pnl-pos" ? "var(--pos)" : tone === "pnl-neg" ? "var(--neg)" : "var(--text-muted)";
  return (
    <svg width={width} height={8} role="img" aria-label="relative size" className="flow-bar">
      <rect x={0} y={0} width={width} height={8} fill="var(--track)" />
      <rect x={0} y={0} width={w} height={8} fill={fill} />
    </svg>
  );
}

/** Birdseye's four size bands as one stacked bar: how the day's trades split by size. */
function BandsBar({ row, width = 160 }: { row: FlowBirdseyeRow; width?: number }) {
  const total = BAND_ORDER.reduce((sum, b) => sum + (row.bands[b] ?? 0), 0);
  if (total <= 0) return <span className="muted">—</span>;
  // Widths in proportion, with every band that has trades at least MIN_BAND wide; the rest shrink to fit.
  const raw = BAND_ORDER.map((b) => ((row.bands[b] ?? 0) / total) * width);
  const floored = raw.map((w) => (w > 0 ? Math.max(w, MIN_BAND) : 0));
  const excess = floored.reduce((a, w) => a + w, 0) - width;
  const roomy = floored.reduce((a, w) => a + (w > MIN_BAND ? w : 0), 0);
  const widths = floored.map((w) => (w > MIN_BAND && roomy > 0 ? w - (excess * w) / roomy : w));
  let x = 0;
  const title = BAND_ORDER.map((b) => `${BAND_LABEL[b]}: ${fmtPct(((row.bands[b] ?? 0) / total) * 100, 1)}`).join(" · ");
  return (
    <svg width={width} height={10} role="img" aria-label={`trades by size: ${title}`} className="flow-bar">
      <title>{title}</title>
      {BAND_ORDER.map((b, i) => {
        const w = widths[i] ?? 0;
        const rect = <rect key={b} x={x} y={0} width={w} height={10} fill={BAND_FILL[b]} />;
        x += w;
        return rect;
      })}
    </svg>
  );
}

function maxOf(values: (number | null)[]): number {
  return values.reduce<number>((m, v) => (v === null ? m : Math.max(m, Math.abs(v))), 0);
}

function asOf(day: OptionsFlowDay): string {
  return day.session;
}

// ----------------------------------------------------------------------------------- tables

function BirdseyeSummaryTable({ rows }: { rows: FlowBirdseyeRow[] }) {
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th className="num">Trades</th>
          <th className="num">Calls</th>
          <th>
            Trade size <BandKey />
          </th>
          <th className="num">100+ lots</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol}>
            <td title={r.name ?? undefined}>{r.symbol}</td>
            <td className="num" title="as reported">{r.shown["total"] ?? fmtCount(r.total)}</td>
            <td className="num">{r.callShare === null ? "—" : fmtPct(r.callShare * 100)}</td>
            <td>
              <BandsBar row={r} />
            </td>
            <td className="num">{fmtCount(r.bands["100+"] ?? null)}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

/**
 * A flow-table header: sortable when the table is shown in full, a plain cell when it is a `limit`
 * preview. A preview is the top of the site's own ranking, cut to N rows; re-sorting those N would
 * read as a ranking the capture never made, so a preview keeps its order and its headers stay still.
 */
function FlowTh({ label, k, sort, className }: { label: string; k: string; sort: SortState | null; className?: string }) {
  return sort === null ? <th className={className}>{label}</th> : <SortTh label={label} k={k} sort={sort} className={className} />;
}

function TradesTable({ rows, limit, showTime }: { rows: FlowTrade[]; limit?: number; showTime: boolean }) {
  const sortState = useSort();
  const sort = limit === undefined ? sortState : null;
  const shown =
    sort === null
      ? rows.slice(0, limit)
      : sortRows(rows, sort, {
          symbol: (r) => r.symbol,
          time: (r) => r.timeEt,
          size: (r) => r.size,
          price: (r) => r.price,
          premium: (r) => r.premium,
        });
  // Scaled to the rows on show: one huge trade below the cut would flatten every bar above it.
  const max = maxOf(shown.map((r) => r.premium));
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <thead>
        <tr>
          <FlowTh label="Symbol" k="symbol" sort={sort} />
          {showTime && <FlowTh label="Time ET" k="time" sort={sort} />}
          <th>Contract</th>
          <FlowTh label="Size" k="size" sort={sort} className="num" />
          <FlowTh label="Price" k="price" sort={sort} className="num" />
          <FlowTh label="Premium" k="premium" sort={sort} className="num" />
          <th />
          <th>Side</th>
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => (
          <tr key={`${r.symbol}-${String(i)}`}>
            <td title={r.name ?? undefined}>{r.symbol}</td>
            {showTime && <td className="muted">{r.timeEt?.slice(0, 8) ?? "—"}</td>}
            <td>{contractLabel(r.expires, r.strike, r.cp)}</td>
            <td className="num">{fmtCount(r.size)}</td>
            <td className="num">{fmtNum(r.price)}</td>
            <td className="num" title={r.premiumDerived ? "size × price × 100 (no premium is reported for outrights)" : undefined}>
              {fmtDollarsShort(r.premium)}
              {r.premiumDerived ? <span className="muted">*</span> : null}
            </td>
            <td>
              <Bar value={r.premium} max={max} tone={sideTone(r.side?.sentiment)} />
            </td>
            <td>
              <SideCell trade={r} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

function directionTone(d: FlowSpread["direction"]): string {
  return d === "bought" ? "pnl-pos" : d === "sold" ? "pnl-neg" : "muted";
}

const SIDE_WORD: Record<string, string> = { call: "call", put: "put", mixed: "call + put" };

/** The derived flow a capture spread became, matched on symbol, print time and size. */
function spreadReads(derived: DerivedFlow | null | undefined): Map<string, DerivedFlowRow> {
  const out = new Map<string, DerivedFlowRow>();
  for (const f of [...(derived?.flows ?? []), ...(derived?.unread ?? [])]) {
    if (f.kind === "spread") out.set(`${f.symbol}|${f.timeEt ?? ""}|${String(f.size)}`, f);
  }
  return out;
}

/** The structure in words with its side: the derived flow's label, or the site's code with the
 *  capture's side before the day is scored. A mixed structure says both legs. */
export function spreadKind(r: FlowSpread, d: DerivedFlowRow | undefined): string {
  const side = r.cp === null ? null : SIDE_WORD[r.cp];
  const label = d?.kindLabel ?? (side ? `${side} (${r.type ?? "spread"})` : (r.type ?? "spread"));
  return r.cp === "mixed" && !label.includes("call") ? `${label} (call + put)` : label;
}

/** The read: bullish / bearish from the derived flow, a volatility lean, or unread with its reason. */
export function spreadRead(d: DerivedFlowRow | undefined): { text: string; tone: string; why?: string } {
  if (d === undefined) return { text: "—", tone: "muted", why: "not scored yet (quikoptions_flow.py score)" };
  if (d.view !== null) return { text: d.view, tone: d.view === "bullish" ? "pnl-pos" : "pnl-neg" };
  if (d.lean !== null) return { text: d.lean, tone: "muted", why: d.unreadWhy ?? undefined };
  return { text: "unread", tone: "muted", why: d.unreadWhy ?? undefined };
}

function SpreadsTable({
  rows,
  limit,
  full,
  derived,
}: {
  rows: FlowSpread[];
  limit?: number;
  full: boolean;
  derived?: DerivedFlow | null;
}) {
  const reads = spreadReads(derived);
  const readOf = (r: FlowSpread) => reads.get(`${r.symbol}|${r.timeEt ?? ""}|${String(r.size)}`);
  const sortState = useSort();
  const sort = limit === undefined ? sortState : null;
  // Linked (⛓) prints sit next to each other in the capture's order; a sort can pull them apart.
  // The mark stays on each row, so the pair is still findable, and clearing the sort restores it.
  const shown =
    sort === null
      ? rows.slice(0, limit)
      : sortRows(rows, sort, {
          symbol: (r) => r.symbol,
          time: (r) => r.timeEt,
          size: (r) => r.size,
          direction: (r) => r.direction,
          kind: (r) => spreadKind(r, readOf(r)),
          read: (r) => spreadRead(readOf(r)).text,
          price: (r) => (r.price === null ? null : Math.abs(r.price)),
          delta: (r) => r.delta,
          premium: (r) => r.premium,
          underlying: (r) => r.underlying?.last ?? null,
          exch: (r) => r.exchange,
        });
  // Scaled to the rows on show: one huge trade below the cut would flatten every bar above it.
  // An unverified premium is the site's figure, not a checked size: it neither scales nor draws a bar.
  const max = maxOf(shown.map((r) => (r.premiumUnverified ? null : r.premium)));
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <thead>
        <tr>
          <FlowTh label="Symbol" k="symbol" sort={sort} />
          <FlowTh label="Time ET" k="time" sort={sort} />
          <th>Spread</th>
          <FlowTh label="Kind" k="kind" sort={sort} />
          <FlowTh label="Size" k="size" sort={sort} className="num" />
          <FlowTh label="Direction" k="direction" sort={sort} />
          <FlowTh label="Read" k="read" sort={sort} />
          <FlowTh label="Price" k="price" sort={sort} className="num" />
          {full && <FlowTh label="Delta" k="delta" sort={sort} className="num" />}
          <FlowTh label="Premium" k="premium" sort={sort} className="num" />
          <th />
          {full && <FlowTh label="Underlying" k="underlying" sort={sort} className="num" />}
          {full && <FlowTh label="Exch" k="exch" sort={sort} />}
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => {
          const linked = r.group !== null;
          const u = r.underlying;
          const d = readOf(r);
          const read = spreadRead(d);
          return (
            <tr key={`${r.symbol}-${String(i)}`} className={linked ? "flow-linked" : undefined}>
              <td title={r.name ?? undefined}>
                {r.symbol}
                {linked && (
                  <span className="muted" title="printed together with another row: same time and size (e.g. a roll)">
                    {" "}⛓
                  </span>
                )}
              </td>
              <td className="muted">{r.timeEt?.slice(0, 8) ?? "—"}</td>
              <td title={r.type ?? undefined}>{r.spread ?? "—"}</td>
              <td>{spreadKind(r, d)}</td>
              <td className="num">{fmtCount(r.size)}</td>
              <td className={directionTone(r.direction)} title="price sign for the side (debit bought, credit sold); delta checked against it">
                {r.direction ?? "—"}
              </td>
              <td className={read.tone} title={read.why}>
                {read.text}
              </td>
              <td className="num">{r.price === null ? "—" : fmtNum(Math.abs(r.price))}</td>
              {full && <td className="num">{fmtNum(r.delta)}</td>}
              <td
                className="num"
                title={
                  r.premiumUnverified
                    ? "the site's figure for a structure with a call and a put leg; it does not equal size × price × 100, so it is unverified"
                    : undefined
                }
              >
                {fmtDollarsShort(r.premium)}
                {r.premiumUnverified ? <span className="muted">?</span> : null}
              </td>
              <td>{r.premiumUnverified ? null : <Bar value={r.premium} max={max} tone={directionTone(r.direction)} />}</td>
              {full && (
                <td className="num" title={u !== null && u.bid !== null ? `bid ${fmtNum(u.bid)} / ask ${fmtNum(u.ask)}` : undefined}>
                  {u === null ? "—" : fmtNum(u.last)}
                </td>
              )}
              {full && <td className="muted">{r.exchange ?? "—"}</td>}
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}

function VolOiTable({ rows, limit }: { rows: FlowVolOi[]; limit?: number }) {
  const sortState = useSort();
  const sort = limit === undefined ? sortState : null;
  const shown =
    sort === null
      ? rows.slice(0, limit)
      : sortRows(rows, sort, {
          symbol: (r) => r.symbol,
          volume: (r) => r.volume,
          oi: (r) => r.oi,
          voi: (r) => r.vOi,
        });
  const max = maxOf(shown.map((r) => r.vOi));
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <thead>
        <tr>
          <FlowTh label="Symbol" k="symbol" sort={sort} />
          <th>Contract</th>
          <FlowTh label="Volume" k="volume" sort={sort} className="num" />
          <FlowTh label="OI" k="oi" sort={sort} className="num" />
          <FlowTh label="V/OI" k="voi" sort={sort} className="num" />
          <th />
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => (
          <tr key={`${r.symbol}-${String(i)}`}>
            <td title={r.name ?? undefined}>{r.symbol}</td>
            <td>{contractLabel(r.expires, r.strike, r.cp)}</td>
            <td className="num">{fmtCount(r.volume)}</td>
            <td className="num">{fmtCount(r.oi)}</td>
            <td className="num">{fmtNum(r.vOi)}</td>
            <td>
              <Bar value={r.vOi} max={max} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

// ----------------------------------------------------------------------------------- derived flow

/**
 * What each flag on a derived flow means, in one line. One home, read by the derived flow tab and
 * the post page's key, so a flag cannot mean one thing in the console and another in a post. A flag
 * the scorer adds without a line here shows in the key as itself, never silently dropped.
 */
export const FLAG_KEY: Record<string, string> = {
  sweep: "one order filled across several exchanges at once — urgency",
  opening: "on the day's openings list: no open interest before the session",
  "volume over OI": "the day's volume passed the open interest the contract started with",
  "≤7d": "expires within 7 days",
  "deep ITM": "|delta| 0.85 or more — mostly stock replacement, little view",
  lottery: "|delta| 0.10 or less — a cheap long shot",
  "near max": "a spread priced at 90% or more of its width — most likely being closed",
  roll: "printed with an opposite spread at the same time and size — one position moved",
  linked: "printed with another spread at the same time and size",
  "paired prints": "two prints at the same millisecond — one order",
  "paired prints, opposite": "two prints at the same millisecond with opposite views — a structure, not a bet",
  "earnings event": "expires within 30 days after the next earnings report — a bet on the event",
  "before ex-dividend": "a deep in-the-money call before an ex-dividend date — a dividend trade",
  "sentiment opposite": "the reported sentiment is the opposite of the read — conviction halved",
  "sentiment neutral": "the reported sentiment is neutral — conviction reduced",
  "delta check": "the model's delta differs from the broker's by more than 0.10",
};

/**
 * Each flag as it shows in a table (2026-10-03): a name of four characters or fewer as itself, a
 * longer one as an abbreviation, keyed under the table by `FlagKey`, so a row's flags fit a narrow
 * column in a phone-sized picture. A flag with no entry here shows as its name rather than vanishing.
 */
export const FLAG_ABBR: Record<string, string> = {
  sweep: "SWP",
  opening: "OPEN",
  "volume over OI": "V>OI",
  "≤7d": "≤7d",
  "deep ITM": "DITM",
  lottery: "LOT",
  "near max": "NMAX",
  roll: "roll",
  linked: "LNK",
  "paired prints": "PAIR",
  "paired prints, opposite": "OPP",
  "earnings event": "EARN",
  "before ex-dividend": "XDIV",
  "sentiment opposite": "ANTI",
  "sentiment neutral": "NEUT",
  "delta check": "ΔCHK",
};

/** A row's flags, abbreviated, each with its full name as the tooltip; "—" for none. */
export function FlagCell({ r }: { r: { flags: string[]; kindLabel?: string } }) {
  const flags = shownFlags(r);
  if (flags.length === 0) return <span className="muted">—</span>;
  return (
    <span className="flag-abbr">
      {flags.map((f) => (
        <span key={f} title={f}>
          {FLAG_ABBR[f] ?? f}
        </span>
      ))}
    </span>
  );
}

/** A row's flags without one that only repeats its kind (a sweep's "sweep"). */
export function shownFlags(r: { flags: string[]; kindLabel?: string }): string[] {
  return r.flags.filter((f) => f !== r.kindLabel);
}

/** The key to the flags these rows show, in the order the key lists them. */
export function FlagKey({ rows }: { rows: { flags: string[]; kindLabel?: string }[] }) {
  const present = new Set(rows.flatMap(shownFlags));
  if (present.size === 0) return null;
  const known = Object.keys(FLAG_KEY).filter((f) => present.has(f));
  const unknown = [...present].filter((f) => !(f in FLAG_KEY)).sort();
  return (
    <dl className="flag-key">
      {[...known, ...unknown].map((f) => (
        <div key={f}>
          <dt>
            <span className="flag-abbr">{FLAG_ABBR[f] ?? f}</span>
            {(FLAG_ABBR[f] ?? f) !== f ? ` ${f}` : ""}
          </dt>
          <dd>{FLAG_KEY[f] ?? ""}</dd>
        </div>
      ))}
    </dl>
  );
}

function viewTone(view: DerivedFlowRow["view"]): string {
  return view === "bullish" ? "pnl-pos" : view === "bearish" ? "pnl-neg" : "muted";
}

/** A score as a bar from the middle: right and green for bullish, left and red for bearish. */
function ScoreBar({ score, width = 80 }: { score: number | null; width?: number }) {
  const half = width / 2;
  const w = score === null ? 0 : (Math.min(Math.abs(score), 100) / 100) * half;
  const x = score !== null && score < 0 ? half - w : half;
  const fill = score === null ? "var(--text-muted)" : score >= 0 ? "var(--pos)" : "var(--neg)";
  return (
    <svg width={width} height={8} role="img" aria-label="score" className="flow-bar">
      <rect x={0} y={0} width={width} height={8} fill="var(--track)" />
      <rect x={half - 0.5} y={0} width={1} height={8} fill="var(--text-muted)" />
      <rect x={x} y={0} width={w} height={8} fill={fill} />
    </svg>
  );
}

function shownScore(r: DerivedFlowRow): number | null {
  return r.confirmedScore ?? r.score;
}

/** `full` is the derived flow tab (every column); `post` is the hidden post page (the Discord
 *  capture: no factors, no confirmation column); neither is the today card. */
export function DerivedTable({
  rows,
  limit,
  full,
  post = false,
}: {
  rows: DerivedFlowRow[];
  limit?: number;
  full: boolean;
  post?: boolean;
}) {
  const wide = full || post;
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th className="num">#</th>
          <th>Flow</th>
          <th>Read</th>
          <th className="num">Δ$</th>
          {wide && <th className="num">Premium</th>}
          {wide && <th className="num">DTE</th>}
          <th className="num">Score</th>
          <th />
          {full && <th title="size · conviction · purity · opening">S · C · P · O</th>}
          {full && <th>Opened?</th>}
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => {
          const s = shownScore(r);
          const f = r.factors;
          return (
            <tr key={`${r.symbol}-${String(i)}`}>
              <td className="num muted">{i + 1}</td>
              <td title={r.size !== null ? `${fmtCount(r.size)} contracts` : undefined}>
                {r.symbol} {r.what ?? ""} <span className="muted">{r.kindLabel}</span>
              </td>
              <td className={viewTone(r.view)}>
                {r.direction ?? "unread"}
                {r.view !== null ? `, ${r.view}` : ""}
              </td>
              <td className="num" title="contracts × 100 × |delta| × the close">
                {fmtDollarsShort(r.deltaDollars)}
              </td>
              {wide && <td className="num">{fmtDollarsShort(r.premium)}</td>}
              {wide && <td className="num">{r.days ?? "—"}</td>}
              <td
                className={`num ${s === null ? "muted" : s >= 0 ? "pnl-pos" : "pnl-neg"}`}
                title={r.confirmedScore !== null ? `confirmed (first ${fmtNum(r.score, 0)})` : "before the open-interest check"}
              >
                {s === null ? "—" : `${s > 0 ? "+" : ""}${s.toFixed(0)}`}
              </td>
              <td>
                <ScoreBar score={s} />
              </td>
              {full && (
                <td className="muted">
                  {f === null
                    ? "—"
                    : `${f.size.toFixed(2)} · ${f.conviction.toFixed(2)} · ${f.purity.toFixed(2)} · ${f.opening.toFixed(2)}`}
                </td>
              )}
              {full && <td className="muted">{r.confirmed ?? "not yet"}</td>}
              <td>
                <FlagCell r={r} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}

export function NetByName({ names, each }: { names: DerivedFlowName[]; each: number }) {
  const bullish = names.filter((n) => n.net > 0).sort((a, b) => b.net - a.net).slice(0, each);
  const bearish = names.filter((n) => n.net < 0).sort((a, b) => a.net - b.net).slice(0, each);
  const rows = [...bullish, ...bearish];
  const max = maxOf(rows.map((n) => n.net));
  return (
    <div className="table-scroll">
    <table className="data-table flow-table">
      <tbody>
        {rows.map((n) => (
          <tr key={n.symbol}>
            <td>{n.symbol}</td>
            <td className={`num ${n.net >= 0 ? "pnl-pos" : "pnl-neg"}`}>
              {n.net >= 0 ? "+" : "−"}
              {fmtDollarsShort(n.net)}
            </td>
            <td>
              <Bar value={n.net} max={max} tone={n.net >= 0 ? "pnl-pos" : "pnl-neg"} width={60} />
            </td>
            <td className="muted">{`${String(n.flows)} flow${n.flows === 1 ? "" : "s"}`}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

function DerivedCards({ day }: { day: OptionsFlowDay }) {
  const d = day.derived;
  if (d === null) {
    return (
      <GridCard label={`Derived flow — ${asOf(day)}`} span={12} h={96} foot="scored by scripts/quikoptions_flow.py score">
        <p className="muted">Not scored yet for this session.</p>
        <svg width={0} height={0} aria-hidden="true" />
      </GridCard>
    );
  }
  return (
    <>
      <GridCard
        label={`Derived flow — ${asOf(day)}`}
        span={8}
        h={304}
        to="/flow/derived"
        toLabel="every derived flow, scored"
        foot="ranked by score: size · conviction · purity · opening; Δ$ is the stock-equivalent exposure"
      >
        <DerivedTable rows={d.flows} limit={8} full={false} />
      </GridCard>
      <GridCard label={`Net by name — ${asOf(day)}`} span={4} h={304} to="/flow/derived" foot="read flows' Δ$, weighted by purity">
        <NetByName names={d.names} each={4} />
      </GridCard>
    </>
  );
}

/** What verifies the table: the day's own checks and the running ones, laid out as recorded. */
function ChecksCard({ checks, session }: { checks: DerivedFlowChecks; session: string }) {
  const v = checks.siteVote;
  const d = checks.delta;
  const c = checks.close;
  const a = checks.audit;
  const r = checks.review;
  return (
    <section className="card view-fade">
      <div className="card-head">
        <h2>Checks</h2>
        <span className="card-asof">{session}</span>
      </div>
      <div className="table-scroll">
      <table className="data-table flow-table">
        <tbody>
          <tr>
            <td>Read against the reported sentiment</td>
            <td>{v === null ? "—" : `${String(v.agrees)} agree · ${String(v.neutral)} neutral · ${String(v.opposite)} opposite`}</td>
          </tr>
          <tr>
            <td>Delta: the broker&apos;s against the model&apos;s</td>
            <td>
              {d === null
                ? "—"
                : `${String(d.broker)} of ${String(d.singles)} from the broker; ${d.off.length === 0 ? "the model within 0.10 on all" : `the model off by more than 0.10 on ${String(d.off.length)}: ${d.off.join("; ")}`}`}
            </td>
          </tr>
          <tr>
            <td>Closes: the broker&apos;s against Dolt&apos;s</td>
            <td>
              {c === null
                ? "the next morning"
                : c.note !== null
                  ? c.note
                  : `${String(c.compared)} of ${String(c.of)} compared; ${c.off.length === 0 ? "all within 0.5%" : c.off.join("; ")}`}
            </td>
          </tr>
          <tr>
            <td>Hand checks against Time &amp; Sales (all sessions)</td>
            <td>
              {a === null || a.checked === 0
                ? "none yet — scripts/quikoptions_flow.py audit --rank N --tape bought|sold|middle"
                : `${String(a.agree)} of ${String(a.checked)} agree${a.rate === null ? "" : ` (${fmtPct(a.rate * 100)})`}`}
            </td>
          </tr>
          <tr>
            <td>The fixed test of the score</td>
            <td>
              {r === null
                ? "not run yet — scripts/quikoptions_flow.py review"
                : r.sessions < r.needed
                  ? `${String(r.sessions)} of ${String(r.needed)} sessions with a 5-session outcome — too few to judge`
                  : r.passed
                    ? "passed: the strong scores beat every simpler read"
                    : "did not pass: the score does not beat the simpler reads"}
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </section>
  );
}

export function FlowDerived({ day }: { day: OptionsFlowDay }) {
  const d = day.derived;
  if (d === null) {
    return (
      <section className="card view-fade">
        <div className="card-head">
          <h2>Derived flow</h2>
          <span className="card-asof">{day.session}</span>
        </div>
        <p className="muted">Not scored yet for this session (scripts/quikoptions_flow.py score).</p>
      </section>
    );
  }
  return (
    <>
      <section className="card view-fade">
        <div className="card-head">
          <h2>Derived flow — every order, read and scored</h2>
          <span className="card-asof">
            {day.session}
            {d.confirmedAt !== null ? " · open interest checked" : " · open interest not checked yet"}
          </span>
        </div>
        <p className="muted">
          One row per order: an outright, a sweep or a spread, with prints at one timestamp grouped and rolls marked.
          Ranked by score, 0–100 and signed by the view: <strong>size</strong> (Δ$ on a fixed $100K–$50M log scale) ×{" "}
          <strong>conviction</strong> (how far the fill sat toward bid or ask; halved where the reported
          sentiment is the opposite) × <strong>purity</strong> (1 for a mid-delta bet, lower for deep in the money, lottery tickets,
          rolls, near-max spreads and sold options) × <strong>opening</strong> (new positioning: from the next
          morning&apos;s open interest once checked). The constants are provisional, to be judged against the outcome
          record.
        </p>
        <DerivedTable rows={d.flows} full />
        <FlagKey rows={d.flows} />
      </section>
      <ChecksCard checks={d.checks} session={day.session} />
      <div className="cards cards-pairs">
        <section className="card view-fade">
          <div className="card-head">
            <h2>Net by name</h2>
            <span className="card-asof">{day.session}</span>
          </div>
          <NetByName names={d.names} each={10} />
        </section>
        <section className="card view-fade">
          <div className="card-head">
            <h2>Unread — the fill sat too near the middle</h2>
            <span className="card-asof">{day.session}</span>
          </div>
          {d.unread.length === 0 ? (
            <p className="muted">Every flow read.</p>
          ) : (
            <DerivedTable rows={d.unread} full={false} />
          )}
          <p className="muted">Listed for their size, never ranked: no read means no view.</p>
        </section>
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------------- slides

function NamesCard({ day }: { day: OptionsFlowDay }) {
  return (
    <GridCard
      label={`Names across tables — ${asOf(day)}`}
      span={4}
      h={304}
      foot="a name in two or more of the six tables"
    >
      {day.names.length === 0 ? (
        <p className="muted">No name is in more than one table.</p>
      ) : (
        <div className="table-scroll">
        <table className="data-table flow-table">
          <tbody>
            {day.names.map((n) => (
              <tr key={n.symbol}>
                <td title={n.name ?? undefined}>{n.symbol}</td>
                <td>
                  <svg width={6 * 9} height={8} role="img" aria-label={`${String(n.tables.length)} of 6 tables`}>
                    {Object.keys(TABLE_LABEL).map((t, i) => (
                      <rect key={t} x={i * 9} y={0} width={7} height={8} fill={n.tables.includes(t) ? "var(--accent)" : "var(--track)"} />
                    ))}
                  </svg>
                </td>
                <td className="muted">{n.tables.map((t) => TABLE_LABEL[t] ?? t).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </GridCard>
  );
}

export function FlowToday({ day }: { day: OptionsFlowDay }) {
  const top = day.birdseye[0];
  const big = day.largestTrade;
  const bull = day.premiumBySide["Bullish"] ?? null;
  const bear = day.premiumBySide["Bearish"] ?? null;
  const neutral = day.premiumBySide["Neutral"] ?? null;
  const nBull = day.tradesBySide["Bullish"] ?? 0;
  const nBear = day.tradesBySide["Bearish"] ?? 0;
  const nNeutral = day.tradesBySide["Neutral"] ?? 0;
  const saved = day.savedAt !== null ? new Date(day.savedAt) : null;
  const savedEt =
    saved !== null && !Number.isNaN(saved.getTime())
      ? saved.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false })
      : null;
  return (
    <div className="grid-12">
      <StatTile label="Session" value={day.session} span={2} foot={savedEt !== null ? `captured ${savedEt} ET` : "capture time not recorded"} />
      <StatTile
        label="Most traded"
        value={top !== undefined ? top.symbol : null}
        span={2}
        foot={top !== undefined ? `${top.shown["total"] ?? fmtCount(top.total)} trades` : "no Birdseye rows"}
        to="/flow/tables?table=birdseye"
      />
      <StatTile
        label="Largest trade"
        value={big !== null ? fmtDollarsShort(big.premium) : null}
        span={2}
        foot={big !== null ? `${big.symbol ?? "?"} · ${TABLE_LABEL[big.table] ?? big.table}` : "no premium read"}
        to={big?.table === "spreads" ? "/flow/tables?table=spreads" : "/flow/tables?table=trades"}
      />
      <StatTile
        label="Bullish premium"
        value={bull !== null ? fmtDollarsShort(bull) : null}
        tone="pos"
        span={2}
        foot={`${String(nBull)} trades, by the reported side`}
        to="/flow/tables?table=trades"
      />
      <StatTile
        label="Bearish premium"
        value={bear !== null ? fmtDollarsShort(bear) : null}
        tone="neg"
        span={2}
        foot={`${String(nBear)} trades · neutral ${fmtDollarsShort(neutral)} (${String(nNeutral)})`}
        to="/flow/tables?table=trades"
      />
      <StatTile
        label="Names across tables"
        value={String(day.names.length)}
        span={2}
        foot={day.names.slice(0, 3).map((n) => n.symbol).join(" · ") || "none"}
      />

      <DerivedCards day={day} />

      <GridCard
        label={`Trades — ${asOf(day)}`}
        span={8}
        h={304}
        to="/flow/tables?table=birdseye"
        toLabel="the full Birdseye table"
        foot="trades (not contracts), the ten most traded names; counts as reported"
      >
        <BirdseyeSummaryTable rows={day.birdseye} />
      </GridCard>
      <NamesCard day={day} />

      <GridCard
        label={`Largest by contracts — ${asOf(day)}`}
        span={6}
        h={304}
        to="/flow/tables?table=trades"
        foot="single-leg trades, ranked by contracts · * premium derived"
      >
        <TradesTable rows={day.outrights} limit={7} showTime={false} />
      </GridCard>
      <GridCard label={`Top sweeps — ${asOf(day)}`} span={6} h={304} to="/flow/tables?table=trades" foot="side as reported">
        <TradesTable rows={day.sweeps} limit={7} showTime={false} />
      </GridCard>

      <GridCard
        label={`Top spreads — ${asOf(day)}`}
        span={6}
        h={304}
        to="/flow/tables?table=spreads"
        foot="⛓ printed together (same time and size); direction from the price sign, checked against delta"
      >
        <SpreadsTable rows={day.spreads} limit={7} full={false} derived={day.derived} />
      </GridCard>
      <GridCard label={`Vol / OI — ${asOf(day)}`} span={6} h={304} to="/flow/tables?table=voloi" foot="volume against open interest; OI > 100">
        <VolOiTable rows={day.voloi} limit={7} />
      </GridCard>
    </div>
  );
}

export function FlowBirdseye({ day }: { day: OptionsFlowDay }) {
  const keys = day.birdseye[0] !== undefined ? Object.keys(day.birdseye[0].buckets) : [];
  const rowMax = (r: FlowBirdseyeRow) => maxOf(keys.map((k) => r.buckets[k] ?? null));
  return (
    <section className="card view-fade">
      <div className="card-head">
        <h2>Trades by size</h2>
        <span className="card-asof">QuikOptions, {day.session}</span>
      </div>
      <p className="muted">
        As reported: the number of trades (not contracts) at each trade size, for its ten most
        traded names. Each cell is shaded by its share of its own row.
      </p>
      <div className="table-scroll">
        <table className="data-table flow-table">
          <thead>
            <tr>
              <th>Symbol</th>
              {keys.map((k) => (
                <th key={k} className="num">
                  {k}
                </th>
              ))}
              <th className="num">Calls</th>
              <th className="num">Puts</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {day.birdseye.map((r) => {
              const m = rowMax(r);
              return (
                <tr key={r.symbol}>
                  <td title={r.name ?? undefined}>{r.symbol}</td>
                  {keys.map((k) => {
                    const v = r.buckets[k] ?? null;
                    const a = v === null || m <= 0 ? 0 : 0.08 + 0.5 * (v / m);
                    return (
                      <td key={k} className="num" style={{ background: `color-mix(in srgb, var(--neg) ${Math.round(a * 100)}%, transparent)` }}>
                        {r.shown[k] ?? fmtCount(v)}
                      </td>
                    );
                  })}
                  <td className="num">{r.shown["calls"] ?? fmtCount(r.calls)}</td>
                  <td className="num">{r.shown["puts"] ?? fmtCount(r.puts)}</td>
                  <td className="num">{r.shown["total"] ?? fmtCount(r.total)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function FlowTrades({ day }: { day: OptionsFlowDay }) {
  return (
    <div className="cards cards-pairs">
      <section className="card view-fade">
        <div className="card-head">
          <h2>Largest by contracts</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <TradesTable rows={day.outrights} showTime />
        <p className="muted">
          The largest outrights — single-leg trades — ranked by number of contracts, not by premium. * Premium is
          derived (size × price × 100): none is reported for outrights. Side as reported, with where the fill sat.
        </p>
      </section>
      <section className="card view-fade">
        <div className="card-head">
          <h2>Top sweeps</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <TradesTable rows={day.sweeps} showTime={false} />
        <p className="muted">The largest sweeps by premium. A sweep carries no time.</p>
      </section>
    </div>
  );
}

export function FlowSpreads({ day }: { day: OptionsFlowDay }) {
  return (
    <section className="card view-fade">
      <div className="card-head">
        <h2>Top spreads</h2>
        <span className="card-asof">QuikOptions, {day.session}</span>
      </div>
      <SpreadsTable rows={day.spreads} full derived={day.derived} />
      <p className="muted">
        Direction is read from the price's sign — a debit is a spread bought, a credit one sold — and checked against the
        delta's: a call spread bought is long delta, a put spread bought short it. Where the signs do not fit, direction is a
        dash. ⛓ marks rows printed together (same time and
        size), such as a roll. Price is the net per share; premium is whole-position dollars.
      </p>
    </section>
  );
}

export function FlowVolOiTables({ day }: { day: OptionsFlowDay }) {
  return (
    <div className="cards cards-pairs">
      <section className="card view-fade">
        <div className="card-head">
          <h2>Vol / OI — open interest over 100</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <VolOiTable rows={day.voloi} />
      </section>
      <section className="card view-fade">
        <div className="card-head">
          <h2>Vol / OI — openings</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <VolOiTable rows={day.openings} />
        <p className="muted">New strikes: no open interest before the session, so V/OI is the volume itself.</p>
      </section>
    </div>
  );
}

/** The four site tables, one at a time (2026-10-30: they were four tabs). The table rides `?table=`. */
export const FLOW_TABLES = [
  { id: "birdseye", label: "by size" },
  { id: "trades", label: "trades" },
  { id: "spreads", label: "spreads" },
  { id: "voloi", label: "vol / OI" },
] as const;

export type FlowTableId = (typeof FLOW_TABLES)[number]["id"];

export function flowTableId(v: string | null | undefined): FlowTableId {
  return FLOW_TABLES.find((t) => t.id === v)?.id ?? "birdseye";
}

export function FlowTables({ day, table, onTable }: { day: OptionsFlowDay; table: FlowTableId; onTable: (t: FlowTableId) => void }) {
  return (
    <>
      <div className="mode-toggle" role="group" aria-label="table" style={{ marginBottom: 12 }}>
        {FLOW_TABLES.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`mode-btn ${t.id === table ? "active" : ""}`}
            aria-pressed={t.id === table}
            onClick={() => onTable(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {table === "birdseye" && <FlowBirdseye day={day} />}
      {table === "trades" && <FlowTrades day={day} />}
      {table === "spreads" && <FlowSpreads day={day} />}
      {table === "voloi" && <FlowVolOiTables day={day} />}
    </>
  );
}
