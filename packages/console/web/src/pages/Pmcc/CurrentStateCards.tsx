import type { PmccBookCell, PmccKeltnerSeries, PmccOpenPosition, PmccPayload } from "@console/shared";
import { Card, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { SignedBar, AXIS_FONT, niceTicks } from "../../components/Charts";
import { fmtStrike } from "../../lib/optionFormat";

/** The three books whose identity the page knows. Anything else (an `advised:*` twin) rides along generically. */
const CORE_BOOKS = ["control", "keltner", "roll"];

function strikeAt(strike: number | null, expiration: string | null): string {
  if (strike === null) return "—";
  return `${fmtStrike(strike)}${expiration === null ? "" : ` @ ${expiration.slice(5)}`}`;
}

/**
 * Time value remaining against the threshold that closes the position.
 *
 * The whole trade is this number decaying to the threshold, so it gets the proximity tint. A null
 * is rendered as an em-dash with its reason on the title, never as $0.00 — a zero here would read
 * as "closing right now", which is the opposite of "we have no usable mark".
 */
function TvCell({ tv, threshold }: { tv: number | null; threshold: number | null }) {
  if (tv === null) {
    return (
      <span className="muted" title="no usable short mark recorded — not the same as zero time value">
        —
      </span>
    );
  }
  const near = threshold !== null && tv <= threshold * 2;
  const at = threshold !== null && tv <= threshold;
  return (
    <span className={at ? "pmcc-tv-at" : near ? "pmcc-tv-near" : ""}>
      {fmtMoney(tv)}
      {threshold !== null && <span className="muted"> → {fmtMoney(threshold)}</span>}
    </span>
  );
}

function PositionRows({ rows, params }: { rows: PmccOpenPosition[]; params: PmccPayload["params"] }) {
  return (
    <>
      {rows.map((p) => {
        const exposedShare = p.markedTicks > 0 ? (p.exposedTicks / p.markedTicks) * 100 : null;
        return (
          <tr key={p.positionId}>
            <td>
              {p.book}
              {p.status === "short_settled" && (
                <span
                  className="chip chip-warn pmcc-chip"
                  title="The short expired ITM and delivered shares. They are covered next session together with the long's sale — the position is not closed while they are outstanding."
                >
                  awaiting disposal
                </span>
              )}
            </td>
            <td>{strikeAt(p.longStrike, p.longExpiration)}</td>
            <td>
              {strikeAt(p.shortStrike, p.shortExpiration)}
              {p.rollCount !== null && p.rollCount > 0 && (
                <sup title={`rolled ${String(p.rollCount)}×`}>+{p.rollCount}</sup>
              )}
            </td>
            <td>
              <TvCell tv={p.currentShortTv} threshold={params.tvCloseThreshold} />
            </td>
            <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
            <td>{fmtPct(p.entryWeeklyYieldPct === null ? null : p.entryWeeklyYieldPct * 100, 2)}</td>
            <td>{fmtPct(p.downsideProtectionPct === null ? null : p.downsideProtectionPct * 100, 1)}</td>
            <td>
              {p.exposedTicks > 0 ? (
                <span
                  className="chip chip-warn pmcc-chip"
                  title={`${String(p.exposedTicks)} of ${String(p.markedTicks)} usable short marks sat under the assignment-exposure threshold. Telemetry only — it gates nothing.`}
                >
                  exposed {fmtPct(exposedShare, 0)}
                </span>
              ) : (
                <span className="muted">—</span>
              )}
            </td>
          </tr>
        );
      })}
    </>
  );
}

/**
 * One card per symbol, listing only the books actually holding a position.
 *
 * Deliberately not a fixed three-row grid. A book holding nothing is signal — the keltner book
 * refusing all week is the experiment working — and a grid with a permanently blank row invites the
 * reader to see a missing number instead of a taken decision. Where keltner is absent, the gate
 * sub-row below says why in its own words.
 */
export function SymbolCards({
  data,
  keltner,
  updatedAt,
}: {
  data: PmccPayload | undefined;
  keltner: PmccKeltnerSeries[];
  updatedAt?: number;
}) {
  const symbols = data?.params.symbols ?? [];
  if (data === undefined) return null;
  return (
    <>
      {symbols.map((symbol) => {
        const rows = data.openPositions.filter((p) => p.symbol === symbol);
        const readiness = data.integrity.keltner.find((k) => k.symbol === symbol);
        const gate = keltner.find((k) => k.symbol === symbol)?.gate ?? null;
        const keltnerHolds = rows.some((r) => r.book === "keltner");
        return (
          <Card key={symbol} title={symbol} collapseKey={`pmcc-symbol-${symbol}`} updatedAt={updatedAt}>
            <div className="table-scroll">
              <table className="data-table num-from-3">
                <thead>
                  <tr>
                    <th>book</th>
                    <th>long</th>
                    <th>short</th>
                    <th>time value</th>
                    <th>spot</th>
                    <th>weekly yield</th>
                    <th>protection</th>
                    <th>assignment</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="muted">
                        no open position on {symbol}
                      </td>
                    </tr>
                  ) : (
                    <PositionRows rows={rows} params={data.params} />
                  )}
                </tbody>
              </table>
            </div>
            {!keltnerHolds && (
              <div className="card-footer pmcc-gate-row">
                <span className="pmcc-sym">keltner</span>{" "}
                {readiness !== undefined && readiness.bars < readiness.required ? (
                  <>
                    cold start {readiness.bars}/{readiness.required} bars — refusing entries while the channel
                    accumulates
                  </>
                ) : gate !== null && gate.reason !== null ? (
                  <>
                    gate held: <span className="mono">{gate.reason}</span>{" "}
                    <span className="muted">({gate.occurrences}× today)</span>
                  </>
                ) : (
                  <span className="muted">no position and no gate refusal recorded on this session</span>
                )}
              </div>
            )}
          </Card>
        );
      })}
    </>
  );
}

/** Per-symbol Keltner channel: closes against the band the gate reads, plus today's verdict. */
function ChannelChart({ series }: { series: PmccKeltnerSeries }) {
  const pts = series.points.filter((p) => p.close !== null);
  if (pts.length < 2) return <p className="muted">not enough bars yet</p>;
  const width = 560;
  const height = 150;
  const m = { l: 44, r: 8, t: 8, b: 16 };
  const values = pts.flatMap((p) => [p.close, p.upper, p.lower].filter((v): v is number => v !== null));
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = (hi - lo || 1) * 0.08;
  const X = (i: number) => m.l + (i / (pts.length - 1)) * (width - m.l - m.r);
  const Y = (v: number) => m.t + ((hi + pad - v) / (hi - lo + pad * 2 || 1)) * (height - m.t - m.b);
  const line = (get: (p: (typeof pts)[number]) => number | null): string =>
    pts
      .map((p, i) => ({ v: get(p), i }))
      .filter((d): d is { v: number; i: number } => d.v !== null)
      .map((d) => `${X(d.i).toFixed(1)},${Y(d.v).toFixed(1)}`)
      .join(" ");
  const bandPts = pts.map((p, i) => ({ p, i })).filter((d) => d.p.upper !== null && d.p.lower !== null);
  const band =
    bandPts.length > 1
      ? `${bandPts.map((d) => `${X(d.i).toFixed(1)},${Y(d.p.upper!).toFixed(1)}`).join(" ")} ${[...bandPts]
          .reverse()
          .map((d) => `${X(d.i).toFixed(1)},${Y(d.p.lower!).toFixed(1)}`)
          .join(" ")}`
      : null;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${series.symbol} keltner channel`}
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      {niceTicks(lo, hi, 3).map((v) => (
        <g key={v}>
          <line x1={m.l} y1={Y(v)} x2={width - m.r} y2={Y(v)} stroke="#15181e" />
          <text x={4} y={Y(v) + 3} {...AXIS_FONT}>
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      {band !== null && <polygon points={band} fill="rgba(122,162,255,0.10)" />}
      <polyline points={line((p) => p.mid)} fill="none" stroke="#7aa2ff" strokeWidth={1} strokeDasharray="3 2" />
      <polyline points={line((p) => p.close)} fill="none" stroke="#eceff3" strokeWidth={1.4} />
      <text x={m.l} y={height - 4} {...AXIS_FONT}>
        {pts[0]!.date}
      </text>
      <text x={width - m.r} y={height - 4} textAnchor="end" {...AXIS_FONT}>
        {pts[pts.length - 1]!.date}
      </text>
    </svg>
  );
}

export function KeltnerCard({
  series,
  readiness,
  updatedAt,
}: {
  series: PmccKeltnerSeries[];
  readiness: PmccPayload["integrity"]["keltner"];
  updatedAt?: number;
}) {
  return (
    <Card
      title="keltner channel — the entry filter under test"
      collapseKey="pmcc-keltner"
      updatedAt={updatedAt}
      className="view-fade"
    >
      <p className="pmcc-note">
        Close against the 20-EMA midline and its ±1.5×ATR band. The keltner book enters only within
        0.5×ATR of the midline, above yesterday's close, and ≥0.25×ATR off the day's low — one failing
        condition is named at a time.
      </p>
      <div className="pmcc-chart-grid">
        {series.map((s) => {
          const r = readiness.find((k) => k.symbol === s.symbol);
          const cold = r !== undefined && r.bars < r.required;
          return (
            <section key={s.symbol}>
              <h3>
                {s.symbol}
                {cold ? (
                  <span className="chip chip-warn pmcc-chip">
                    cold start {r.bars}/{r.required}
                  </span>
                ) : s.gate !== null && s.gate.reason !== null ? (
                  <span className="chip chip-warn pmcc-chip" title={`${String(s.gate.occurrences)}× on this session`}>
                    {s.gate.reason}
                  </span>
                ) : (
                  <span className="chip chip-ok pmcc-chip">no gate refusal</span>
                )}
              </h3>
              <ChannelChart series={s} />
            </section>
          );
        })}
      </div>
    </Card>
  );
}

interface BookTotals {
  book: string;
  positions: number;
  net: number | null;
  rolls: number | null;
  wins: number;
}

function totalsByBook(books: PmccBookCell[]): BookTotals[] {
  const map = new Map<string, BookTotals>();
  for (const cell of books) {
    const t = map.get(cell.book) ?? { book: cell.book, positions: 0, net: null, rolls: null, wins: 0 };
    t.positions += cell.positions;
    if (cell.netPnl !== null) t.net = (t.net ?? 0) + cell.netPnl;
    if (cell.rolls !== null) t.rolls = (t.rolls ?? 0) + cell.rolls;
    if (cell.winRate !== null) t.wins += cell.winRate * cell.positions;
    map.set(cell.book, t);
  }
  // Core books first in their declared order, then anything else (advised twins) alphabetically.
  return [...map.values()].sort((a, b) => {
    const ia = CORE_BOOKS.indexOf(a.book);
    const ib = CORE_BOOKS.indexOf(b.book);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.book.localeCompare(b.book);
  });
}

/**
 * The book comparison, split into two sections that must never merge.
 *
 * control and roll enter from the same plan on the same tick — identical strikes, mids and modeled
 * costs — so a row-by-row delta between them is exactly the roll rule's effect and nothing else.
 * keltner enters on its own ticks, because its variable IS the entry tick; differencing it against
 * control would attribute a different set of fills to a management rule neither book changed. The
 * module's CLAUDE.md states it plainly: read surfaces must not treat the three as a fully paired
 * grid. Hence two sections, one delta column, and no way to read across the seam.
 */
export function BookComparison({ data, updatedAt }: { data: PmccPayload | undefined; updatedAt?: number }) {
  const books = data?.books ?? [];
  const totals = totalsByBook(books);
  const symbols = [...new Set(books.map((b) => b.symbol))].sort();
  const cell = (book: string, symbol: string): PmccBookCell | undefined =>
    books.find((b) => b.book === book && b.symbol === symbol);
  const keltner = totals.find((t) => t.book === "keltner");
  const others = totals.filter((t) => !CORE_BOOKS.includes(t.book));
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.net ?? 0)));
  const hasClosed = books.length > 0;

  return (
    <Card title="book comparison" collapseKey="pmcc-books" updatedAt={updatedAt}>
      {!hasClosed ? (
        <p className="muted">
          no completed cycles yet — per-book results fill in as positions close
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
        <>
          <section className="pmcc-compare">
            <h3>control vs roll — exactly paired</h3>
            <p className="pmcc-note">
              Both books enter from the same plan on the same tick, with identical strikes, mids and modeled
              costs. The delta is the roll rule's whole effect.
            </p>
            <div className="table-scroll">
              <table className="data-table num-from-1">
                <thead>
                  <tr>
                    <th>symbol</th>
                    <th>control net</th>
                    <th>roll net</th>
                    <th>delta</th>
                    <th>control cycles</th>
                    <th>roll cycles</th>
                    <th>rolls taken</th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((symbol) => {
                    const c = cell("control", symbol);
                    const r = cell("roll", symbol);
                    const delta =
                      c?.netPnl === undefined || c.netPnl === null || r?.netPnl === undefined || r.netPnl === null
                        ? null
                        : r.netPnl - c.netPnl;
                    return (
                      <tr key={symbol}>
                        <td>{symbol}</td>
                        <td>
                          <PnlCell v={c?.netPnl ?? null} />
                        </td>
                        <td>
                          <PnlCell v={r?.netPnl ?? null} />
                        </td>
                        <td>
                          <PnlCell v={delta} />
                        </td>
                        <td>{c?.positions ?? 0}</td>
                        <td>{r?.positions ?? 0}</td>
                        <td>{r?.rolls ?? 0}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="pmcc-compare">
            <h3>keltner — not row-comparable</h3>
            <p className="pmcc-note">
              Gated entry means keltner's fill set differs from control's by construction. Compare aggregates
              over time, never cycle by cycle — there is deliberately no delta column here.
            </p>
            {keltner === undefined || keltner.positions === 0 ? (
              <p className="muted">no completed keltner cycles — the gate has not admitted an entry that closed yet</p>
            ) : (
              <p>
                {keltner.positions} cycle{keltner.positions === 1 ? "" : "s"} · net{" "}
                <PnlCell v={keltner.net} /> · win rate{" "}
                {fmtPct(keltner.positions > 0 ? (keltner.wins / keltner.positions) * 100 : null, 0)}
              </p>
            )}
          </section>

          {others.length > 0 && (
            <section className="pmcc-compare">
              <h3>advised books</h3>
              <p className="pmcc-note">
                The advisor's synthetic twin runs its admitted params beside a base book. It is excluded from the
                pairing above — its entries are its own.
              </p>
              <ul className="pmcc-plain-list">
                {others.map((t) => (
                  <li key={t.book}>
                    <span className="mono">{t.book}</span> · {t.positions} cycle{t.positions === 1 ? "" : "s"} · net{" "}
                    <PnlCell v={t.net} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="pmcc-compare">
            <h3>net by book</h3>
            <table className="data-table num-from-1">
              <tbody>
                {totals.map((t) => (
                  <tr key={t.book}>
                    <td>{t.book}</td>
                    <td style={{ width: "50%" }}>
                      <SignedBar value={t.net ?? 0} maxAbs={maxAbs} compact />
                    </td>
                    <td>
                      <PnlCell v={t.net} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="pmcc-note">
              Every figure here is net of the modeled fee and slippage stack — and is still an upper bound while
              early assignment sits unmodelled.
            </p>
          </section>
        </>
      )}
    </Card>
  );
}
