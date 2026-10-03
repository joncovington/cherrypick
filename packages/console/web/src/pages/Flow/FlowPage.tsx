import type { FlowBirdseyeRow, FlowSpread, FlowTrade, FlowVolOi, OptionsFlowDay } from "@console/shared";
import { GridCard, StatTile } from "../../components/grid/GridCard";
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
const BAND_OPACITY: Record<string, number> = { "1": 0.25, "2-10": 0.45, "11-99": 0.7, "100+": 1 };

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
    <span className={sideTone(s.sentiment)} title={`${s.sentiment} — ${s.fill}${edge} (the site's classification)`}>
      {s.sentiment.toLowerCase()} <span className="muted">{s.fill.toLowerCase()}</span>
    </span>
  );
}

/** A magnitude bar: width in proportion to the largest value shown in the column, toned by side. */
function Bar({ value, max, tone, width = 72 }: { value: number | null; max: number; tone?: string; width?: number }) {
  const w = value === null || max <= 0 ? 0 : Math.max(1, (Math.abs(value) / max) * width);
  const fill = tone === "pnl-pos" ? "var(--ok)" : tone === "pnl-neg" ? "var(--err)" : "var(--text-muted)";
  return (
    <svg width={width} height={8} role="img" aria-label="relative size" className="flow-bar">
      <rect x={0} y={0} width={width} height={8} fill="var(--row-line)" />
      <rect x={0} y={0} width={w} height={8} fill={fill} />
    </svg>
  );
}

/** Birdseye's four size bands as one stacked bar: how the day's trades split by size. */
function BandsBar({ row, width = 160 }: { row: FlowBirdseyeRow; width?: number }) {
  const total = BAND_ORDER.reduce((sum, b) => sum + (row.bands[b] ?? 0), 0);
  if (total <= 0) return <span className="muted">—</span>;
  let x = 0;
  const title = BAND_ORDER.map((b) => `${BAND_LABEL[b]}: ${fmtPct(((row.bands[b] ?? 0) / total) * 100, 1)}`).join(" · ");
  return (
    <svg width={width} height={10} role="img" aria-label={`trades by size: ${title}`} className="flow-bar">
      <title>{title}</title>
      {BAND_ORDER.map((b) => {
        const w = ((row.bands[b] ?? 0) / total) * width;
        const rect = <rect key={b} x={x} y={0} width={w} height={10} fill="var(--accent)" opacity={BAND_OPACITY[b]} />;
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
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th className="num">Trades</th>
          <th className="num">Calls</th>
          <th>Trade size (1 · 2–10 · 11–99 · 100+)</th>
          <th className="num">100+ lots</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol}>
            <td title={r.name ?? undefined}>{r.symbol}</td>
            <td className="num" title="as the site printed it">{r.shown["total"] ?? fmtCount(r.total)}</td>
            <td className="num">{r.callShare === null ? "—" : fmtPct(r.callShare * 100)}</td>
            <td>
              <BandsBar row={r} />
            </td>
            <td className="num">{fmtCount(r.bands["100+"] ?? null)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TradesTable({ rows, limit, showTime }: { rows: FlowTrade[]; limit?: number; showTime: boolean }) {
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  // Scaled to the rows on show: one huge trade below the cut would flatten every bar above it.
  const max = maxOf(shown.map((r) => r.premium));
  return (
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Symbol</th>
          {showTime && <th>Time ET</th>}
          <th>Contract</th>
          <th className="num">Size</th>
          <th className="num">Price</th>
          <th className="num">Premium</th>
          <th />
          <th>Side (site)</th>
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
            <td className="num" title={r.premiumDerived ? "size × price × 100 (the site prints no premium for outrights)" : undefined}>
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
  );
}

function directionTone(d: FlowSpread["direction"]): string {
  return d === "bought" ? "pnl-pos" : d === "sold" ? "pnl-neg" : "muted";
}

function SpreadsTable({ rows, limit, full }: { rows: FlowSpread[]; limit?: number; full: boolean }) {
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  // Scaled to the rows on show: one huge trade below the cut would flatten every bar above it.
  const max = maxOf(shown.map((r) => r.premium));
  return (
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Time ET</th>
          <th>Spread</th>
          <th className="num">Size</th>
          <th>Direction</th>
          <th className="num">Price</th>
          {full && <th className="num">Delta</th>}
          <th className="num">Premium</th>
          <th />
          {full && <th className="num">Underlying</th>}
          {full && <th>Exch</th>}
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => {
          const linked = r.group !== null;
          const u = r.underlying;
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
              <td className="num">{fmtCount(r.size)}</td>
              <td className={directionTone(r.direction)} title="from the site's signs: price and delta agree">
                {r.direction ?? "—"}
              </td>
              <td className="num">{r.price === null ? "—" : fmtNum(Math.abs(r.price))}</td>
              {full && <td className="num">{fmtNum(r.delta)}</td>}
              <td className="num">{fmtDollarsShort(r.premium)}</td>
              <td>
                <Bar value={r.premium} max={max} tone={directionTone(r.direction)} />
              </td>
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
  );
}

function VolOiTable({ rows, limit }: { rows: FlowVolOi[]; limit?: number }) {
  const shown = limit === undefined ? rows : rows.slice(0, limit);
  const max = maxOf(shown.map((r) => r.vOi));
  return (
    <table className="data-table flow-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Contract</th>
          <th className="num">Volume</th>
          <th className="num">OI</th>
          <th className="num">V/OI</th>
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
        <table className="data-table flow-table">
          <tbody>
            {day.names.map((n) => (
              <tr key={n.symbol}>
                <td title={n.name ?? undefined}>{n.symbol}</td>
                <td>
                  <svg width={6 * 9} height={8} role="img" aria-label={`${String(n.tables.length)} of 6 tables`}>
                    {Object.keys(TABLE_LABEL).map((t, i) => (
                      <rect key={t} x={i * 9} y={0} width={7} height={8} fill={n.tables.includes(t) ? "var(--accent)" : "var(--row-line)"} />
                    ))}
                  </svg>
                </td>
                <td className="muted">{n.tables.map((t) => TABLE_LABEL[t] ?? t).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
        to="/flow/birdseye"
      />
      <StatTile
        label="Largest trade"
        value={big !== null ? fmtDollarsShort(big.premium) : null}
        span={2}
        foot={big !== null ? `${big.symbol ?? "?"} · ${TABLE_LABEL[big.table] ?? big.table}` : "no premium read"}
        to={big?.table === "spreads" ? "/flow/spreads" : "/flow/trades"}
      />
      <StatTile
        label="Bullish premium"
        value={bull !== null ? fmtDollarsShort(bull) : null}
        tone="pos"
        span={2}
        foot={`${String(nBull)} trades, the site's call`}
        to="/flow/trades"
      />
      <StatTile
        label="Bearish premium"
        value={bear !== null ? fmtDollarsShort(bear) : null}
        tone="neg"
        span={2}
        foot={`${String(nBear)} trades · neutral ${fmtDollarsShort(neutral)} (${String(nNeutral)})`}
        to="/flow/trades"
      />
      <StatTile
        label="Names across tables"
        value={String(day.names.length)}
        span={2}
        foot={day.names.slice(0, 3).map((n) => n.symbol).join(" · ") || "none"}
      />

      <GridCard
        label={`Birdseye — ${asOf(day)}`}
        span={8}
        h={304}
        to="/flow/birdseye"
        toLabel="the full Birdseye table"
        foot="trades (not contracts), the site's ten most traded; counts as the site printed them"
      >
        <BirdseyeSummaryTable rows={day.birdseye} />
      </GridCard>
      <NamesCard day={day} />

      <GridCard
        label={`Largest outrights — ${asOf(day)}`}
        span={6}
        h={304}
        to="/flow/trades"
        foot="* premium derived: size × price × 100"
      >
        <TradesTable rows={day.outrights} limit={7} showTime={false} />
      </GridCard>
      <GridCard label={`Top sweeps — ${asOf(day)}`} span={6} h={304} to="/flow/trades" foot="side is the site's classification">
        <TradesTable rows={day.sweeps} limit={7} showTime={false} />
      </GridCard>

      <GridCard
        label={`Top spreads — ${asOf(day)}`}
        span={6}
        h={304}
        to="/flow/spreads"
        foot="⛓ printed together (same time and size); direction from the site's signs"
      >
        <SpreadsTable rows={day.spreads} limit={7} full={false} />
      </GridCard>
      <GridCard label={`Vol / OI — ${asOf(day)}`} span={6} h={304} to="/flow/voloi" foot="volume against open interest; OI > 100">
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
        <h2>Birdseye — trades by size</h2>
        <span className="card-asof">QuikOptions, {day.session}</span>
      </div>
      <p className="muted">
        The site's table as it printed it: the number of trades (not contracts) at each trade size, for its ten most
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
                      <td key={k} className="num" style={{ background: `rgba(210, 63, 87, ${a.toFixed(3)})` }}>
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
    <div className="cards-pairs">
      <section className="card view-fade">
        <div className="card-head">
          <h2>Largest outrights</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <TradesTable rows={day.outrights} showTime />
        <p className="muted">
          The site's largest single-leg trades by size. * Premium is derived (size × price × 100): the site prints none
          for outrights. Side is the site's classification, with where the fill sat.
        </p>
      </section>
      <section className="card view-fade">
        <div className="card-head">
          <h2>Top sweeps</h2>
          <span className="card-asof">QuikOptions, {day.session}</span>
        </div>
        <TradesTable rows={day.sweeps} showTime={false} />
        <p className="muted">The site's largest sweeps by premium. It prints no time for a sweep.</p>
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
      <SpreadsTable rows={day.spreads} full />
      <p className="muted">
        Direction is read from the site's own signs — price and delta agree on every spread seen, positive for a spread
        bought and negative for one sold — and is a dash where they do not. ⛓ marks rows printed together (same time and
        size), such as a roll. Price is the net per share; premium is whole-position dollars.
      </p>
    </section>
  );
}

export function FlowVolOi({ day }: { day: OptionsFlowDay }) {
  return (
    <div className="cards-pairs">
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
