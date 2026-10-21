import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { SkateBand, SkateConfig, SkatePayload, SkateSide } from "@console/shared";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { useSkate } from "../../lib/api";
import { SkateRecordSection } from "./SkateRecord";
import { beatsMonthly, otherBand, skateCounts, skateViews, type SkateView } from "./skateRows";

/**
 * The Skate tab (2026-10-19; docs/skate-plan.md step 3): covered calls above a name's recent range
 * and cash-secured puts below it, where the seller does not want assignment. Read-only, and it
 * decides nothing: `fetch_screener_greeks.py skate` quotes the band strikes in regular hours and
 * `technicals.skate` scores them -- the history and implied rates, the gap between them, the yields,
 * the hard and soft verdicts and each band's best expiry are all the run file's.
 *
 * As the plan's decisions set it (2026-10-18): the hard filters hide a row (open interest, the
 * spread; earnings are dropped upstream, by the band file), the soft thresholds are a marker with a
 * "passing only" toggle, and the default sort is history minus implied. A band selector picks which
 * band's strikes drive the rows; the other band's strike and premium a day sit beside them, quieter.
 */

const SIDES: { id: "calls" | "puts"; side: SkateSide; label: string }[] = [
  { id: "calls", side: "C", label: "Calls" },
  { id: "puts", side: "P", label: "Puts" },
];
const BAND_LABEL: Record<SkateBand, string> = { fixed: "fixed", byExpiry: "by expiry" };

const DASH = "—";

function pct(v: number | null, digits = 1): string {
  return v === null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

/** A rate difference in points: +4.2 pts. */
function points(v: number | null): string {
  if (v === null) return DASH;
  const p = v * 100;
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p).toFixed(1)} pts`;
}

function signedPct(v: number | null): string {
  if (v === null) return DASH;
  const p = v * 100;
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p).toFixed(1)}%`;
}

/** The strike against the spot at the quote, as a fraction: a display figure, judged by nothing here. */
function distance(v: SkateView): number | null {
  return v.row.spot === null || v.row.spot === 0 ? null : v.pick.strike / v.row.spot - 1;
}

function dollars(v: number | null, digits = 2): string {
  return v === null ? DASH : `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** "2026-11-13" -> "Nov 13". */
function shortDate(iso: string | null): string {
  if (iso === null) return DASH;
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** A UTC ISO stamp as New York wall time: "2026-10-19 15:05 ET". */
function etStamp(iso: string | null): string | null {
  if (iso === null) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} ET`;
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

/** The haircut schedule in words, from the run's own config. */
function bandsLine(c: SkateConfig | null): string {
  if (c === null || c.haircut === null) return "";
  const steps = c.byExpiry.map(([days, cut]) => `${cut} at ${days} days or fewer`).join(", ");
  return `Fixed is a ${c.haircut} haircut at every expiry; by expiry is ${steps || "the same"}${steps ? `, ${c.haircut} beyond` : ""}.`;
}

function hardLine(c: SkateConfig | null): string {
  if (c === null) return "open interest and the bid-ask spread";
  const h = c.hard;
  return (
    `open interest of ${h.minOpenInterest ?? DASH} or more, and a spread within ${h.maxSpreadPct === null ? DASH : `${Math.round(h.maxSpreadPct * 100)}%`} ` +
    `of mid or ${h.maxSpreadAbs === null ? DASH : dollars(h.maxSpreadAbs)} a share`
  );
}

function softLine(c: SkateConfig | null): string {
  if (c === null) return "the skate rate over 11 years and 3, and skate yield";
  const s = c.soft;
  return `skate rate ${pct(s.skate, 0)} or more over 11 years and ${pct(s.skateRecent, 0)} over 3, skate yield on the bid ${pct(s.yield, 0)} or more`;
}

function SoftCell({ v, c }: { v: SkateView; c: SkateConfig | null }) {
  const s = v.pick.soft;
  const missed = [
    s.skate ? null : `11-year skate rate under ${pct(c?.soft.skate ?? null, 0)}`,
    s.skateRecent ? null : `3-year skate rate under ${pct(c?.soft.skateRecent ?? null, 0)}`,
    s.yield ? null : `skate yield under ${pct(c?.soft.yield ?? null, 0)}`,
  ].filter((x): x is string => x !== null);
  return s.all ? (
    <td title="Passes every soft threshold">
      <span className="chip chip-ok">pass</span>
    </td>
  ) : (
    <td title={`A marker, not a filter. Misses: ${missed.join("; ")}`}>
      <span className="chip">miss</span>
    </td>
  );
}

function ExpiryCell({ v }: { v: SkateView }) {
  const beats = beatsMonthly(v.best);
  const title =
    `${v.best.expiry}, ${v.pick.days} days. The band's best expiry by premium a day on the bid among picks passing the hard filters. ` +
    (v.row.stage === 2
      ? "Every weekly to 63 days was quoted for this name-side."
      : "Only one expiry was quoted for this name-side (the cycle monthly, or the last before a report), so it is the best by default.") +
    (v.best.monthly ? ` Monthly: ${v.best.monthly}.` : " No cycle monthly before the next report.");
  return (
    <td title={title} style={beats ? { fontWeight: 700 } : undefined}>
      {shortDate(v.best.expiry)} <span className="muted">{v.pick.days}d</span>
    </td>
  );
}

function OtherCell({ v }: { v: SkateView }) {
  const o = v.other;
  const name = BAND_LABEL[otherBand(v.band)];
  if (o.best === null) {
    return (
      <td className="muted" title={`No pick on the ${name} band passes the hard filters`}>
        {DASH}
      </td>
    );
  }
  const same = o.best.expiry === v.best.expiry && o.best.strike === v.best.strike;
  return (
    <td className="muted num" title={`The ${name} band's best: ${o.best.strike} on ${o.best.expiry}, ${dollars(o.best.perDayBid)} a day per contract on the bid`}>
      {same ? "same" : `${o.best.strike}${o.best.expiry !== v.best.expiry ? ` · ${shortDate(o.best.expiry)}` : ""} · ${dollars(o.best.perDayBid)}/d`}
    </td>
  );
}

const SORTS: Record<string, (v: SkateView) => number | string | null> = {
  symbol: (v) => v.row.symbol,
  spot: (v) => v.row.spot,
  expiry: (v) => v.pick.days,
  vsMonthly: (v) => v.best.vsMonthly,
  strike: (v) => v.pick.strike,
  distance: (v) => distance(v),
  bid: (v) => v.pick.quote.bid,
  perDay: (v) => v.pick.perDayBid,
  gap: (v) => v.pick.gap,
  skate: (v) => v.pick.skate,
  skateRecent: (v) => v.pick.skateRecent,
  implied: (v) => v.pick.impliedSkate,
  yield: (v) => v.pick.yieldBid,
  delta: (v) => (v.pick.delta === null ? null : Math.abs(v.pick.delta)),
  oi: (v) => v.pick.quote.openInterest,
  tested: (v) => v.pick.testedPerMonth,
  earnings: (v) => v.row.earnings,
};

function SkateTable({ views, data }: { views: SkateView[]; data: SkatePayload }) {
  const sort = useSort();
  const rows = sortRows(views, sort, SORTS);
  const other = views[0] ? BAND_LABEL[otherBand(views[0].band)] : "other";
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="Symbol" k="symbol" sort={sort} />
            <SortTh label="Spot" k="spot" sort={sort} className="num" title="Per share, when quoted" />
            <SortTh label="Expiry" k="expiry" sort={sort} title="The band's best expiry; bold when it pays more a day than the monthly" />
            <SortTh label="vs monthly" k="vsMonthly" sort={sort} className="num" title="Premium a day on the bid over the monthly pick's; a dash when the monthly pick does not pass the hard filters, or there is no monthly" />
            <SortTh label="Strike" k="strike" sort={sort} className="num" title="The band's pick: the listed strike at or past the band" />
            <SortTh
              label="vs spot"
              k="distance"
              sort={sort}
              className="num"
              title="The strike against the spot at the quote, not against the band: the band is set on the band day's close, so a name that has moved since can have its pick at or through the money"
            />
            <SortTh label="Bid / share" k="bid" sort={sort} className="num" title="The one per-share figure: the bid, and the ask beside it" />
            <th className="num" title="Premium received per contract at the bid (bid × 100), a credit">
              Premium
            </th>
            <SortTh label="$ / day" k="perDay" sort={sort} className="num" title="Premium per contract per calendar day, on the bid" />
            <SortTh label="Hist − implied" k="gap" sort={sort} className="num" title="The 11-year skate rate minus the model's (1 − N(d2)): the default sort, widest first. A history estimate until the forward record exists." />
            <SortTh label="Skate 11y" k="skate" sort={sort} className="num" title="Share of past days on which the band, at this strike's offset, held to expiry, over 11 years" />
            <SortTh label="3y" k="skateRecent" sort={sort} className="num" title="The same over the last 3 years" />
            <SortTh label="Implied" k="implied" sort={sort} className="num" title="The model's chance of finishing out of the money" />
            <SortTh label="Yield" k="yield" sort={sort} className="num" title="Skate yield on the bid: premium ÷ capital × 365 ÷ days, simple (capital: the shares for a call, the strike for a put)" />
            <SortTh label="Δ" k="delta" sort={sort} className="num" title="Delta from the IV solved at the mid" />
            <SortTh label="OI" k="oi" sort={sort} className="num" title="Open interest at the strike" />
            <SortTh label="Tested / mo" k="tested" sort={sort} className="num" title="Chance of being tested at least once over a month of cycles at this expiry, from the 11-year rate" />
            <th title="The soft thresholds: a marker, never a filter">Soft</th>
            <th className="num" title={`The ${other} band's best strike (its expiry when different) and premium a day per contract`}>
              {other} band
            </th>
            <SortTh label="Earnings" k="earnings" sort={sort} title="The next announcement; expiries spanning it are dropped before quoting" />
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => (
            <tr key={`${v.row.symbol}-${v.row.side}`}>
              <td>
                <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(v.row.symbol)}`} title={`Open ${v.row.symbol}'s chart`}>
                  {v.row.symbol}
                </Link>
                {v.row.fund && (
                  <span className="chip" style={{ marginLeft: 4 }} title="A fund: no earnings">
                    fund
                  </span>
                )}
              </td>
              <td className="num">{v.row.spot === null ? DASH : v.row.spot.toFixed(2)}</td>
              <ExpiryCell v={v} />
              <td className="num">{v.best.vsMonthly === null ? DASH : `${v.best.vsMonthly.toFixed(2)}×`}</td>
              <td className="num" title={`${v.pick.option}; ${v.pick.offset === null ? "" : `${(v.pick.offset * 100).toFixed(2)}% past the band`}${v.pick.outsideGrid ? ", past the history grid's end" : ""}`}>
                {v.pick.strike}
                {v.row.side}
              </td>
              <td className="num">{signedPct(distance(v))}</td>
              <td className="num">
                {v.pick.quote.bid === null ? DASH : `${v.pick.quote.bid.toFixed(2)} bid`}
                <span className="muted"> / {v.pick.quote.ask === null ? DASH : v.pick.quote.ask.toFixed(2)}</span>
              </td>
              <td className="num">{v.pick.quote.bid === null ? DASH : `+${dollars(v.pick.quote.bid * 100, 0)}`}</td>
              <td className="num">{dollars(v.pick.perDayBid)}</td>
              <td className={`num ${v.pick.gap === null ? "" : v.pick.gap > 0 ? "pnl-pos" : v.pick.gap < 0 ? "pnl-neg" : ""}`}>{points(v.pick.gap)}</td>
              <td className="num">{pct(v.pick.skate)}</td>
              <td className="num">{pct(v.pick.skateRecent)}</td>
              <td className="num">{pct(v.pick.impliedSkate)}</td>
              <td className="num">{pct(v.pick.yieldBid)}</td>
              <td className="num">{v.pick.delta === null ? DASH : v.pick.delta.toFixed(2)}</td>
              <td className="num">{v.pick.quote.openInterest === null ? DASH : v.pick.quote.openInterest.toLocaleString("en-US")}</td>
              <td className="num">{pct(v.pick.testedPerMonth, 0)}</td>
              <SoftCell v={v} c={data.config} />
              <OtherCell v={v} />
              <td>{v.row.fund ? <span className="muted">{DASH}</span> : shortDate(v.row.earnings)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SkateTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useSkate(params.get("day"));
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const sideId = params.get("side") === "puts" ? "puts" : "calls";
  const side = SIDES.find((s) => s.id === sideId)!;
  const band: SkateBand = params.get("band") === "by-expiry" ? "byExpiry" : "fixed";
  const passingOnly = params.get("passing") === "1";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const latest = data?.days[0] ?? null;

  const all = data?.rows ?? [];
  const views = skateViews(all, side.side, band, passingOnly).filter((v) => q === "" || v.row.symbol.includes(q));
  const counts = skateCounts(all, side.side, band);
  const measured = etStamp(data?.measuredAt ?? null);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Skate</h2>
        <span className="chip">read-only</span>
        {data?.day && (
          <span className="card-asof">
            session {data.day}
            {measured ? ` · measured ${measured}` : ""}
            {data.bandDay ? ` · bands on the ${data.bandDay} close` : ""}
            {data.runs.length > 1 ? ` · newest of ${data.runs.length} runs` : ""}
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="Skate session"
            value={data.day ?? ""}
            onChange={(e) => set("day", e.target.value === latest ? null : e.target.value)}
          >
            {data.days.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}
        <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="Side">
          {SIDES.map((s) => (
            <button
              key={s.id}
              type="button"
              className={s.id === sideId ? "mode-btn active" : "mode-btn"}
              title={s.side === "C" ? "Covered calls above the range band" : "Cash-secured puts below the range band"}
              onClick={() => set("side", s.id === "calls" ? null : s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="mode-toggle" style={{ marginLeft: 0 }} role="group" aria-label="Band">
          {(["fixed", "byExpiry"] as const).map((b) => (
            <button
              key={b}
              type="button"
              className={b === band ? "mode-btn active" : "mode-btn"}
              title={`Rows and ranking from the ${BAND_LABEL[b]} band's strikes. ${bandsLine(data?.config ?? null)}`}
              onClick={() => set("band", b === "fixed" ? null : "by-expiry")}
            >
              {BAND_LABEL[b]}
            </button>
          ))}
        </div>
        <Flag
          on={passingOnly}
          onChange={(on) => set("passing", on ? "1" : null)}
          title={`Only rows passing every soft threshold: ${softLine(data?.config ?? null)}. Off, they are marked, not hidden.`}
        >
          Passing only
        </Flag>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        {data?.status === "ok" && (
          <span className="chip" style={{ whiteSpace: "normal" }}>
            {views.length} shown · {counts.read} {side.label.toLowerCase()} read · {counts.read - counts.tradable} with no pick passing the hard
            filters hidden · {counts.passing} of {counts.tradable} pass the soft thresholds
          </span>
        )}
      </div>

      {isError && <p className="muted">Could not read the skate screen.</p>}
      {isLoading && <p className="muted">Reading the skate run…</p>}
      {data && data.status === "none" && (
        <p className="muted">No skate run written{data.day ? ` for ${data.day}` : " yet"}: the quotes are taken at 12:00 and 15:05 ET on a session day.</p>
      )}
      {data && data.status === "unreadable" && (
        <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
          The {data.run} run for {data.day} could not be read, so nothing is shown. This is a failed read, not an empty screen.
        </p>
      )}
      {data && data.status === "ok" && !data.complete && (
        <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
          This run did not complete: the rows are what it scored before it stopped, not the whole screen.
        </p>
      )}
      {data && data.status === "ok" && data.problem !== null && (
        <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
          The run reports a problem: {data.problem}
        </p>
      )}
      {data && data.status === "ok" && all.length === 0 && <p className="muted">The run scored no name-sides.</p>}
      {data && data.status === "ok" && all.length > 0 && views.length === 0 && (
        <p className="muted">
          {passingOnly
            ? `No ${side.label.toLowerCase()} on the ${BAND_LABEL[band]} band pass the soft thresholds${q ? ` matching ${q}` : ""}.`
            : `No ${side.label.toLowerCase()} on the ${BAND_LABEL[band]} band pass the hard filters${q ? ` matching ${q}` : ""}.`}
        </p>
      )}

      {data && views.length > 0 && <SkateTable views={views} data={data} />}

      <p className="muted" style={{ marginTop: 8 }}>
        Each row is one name-side: the band's own strike on its best expiry, the one paying the most a day on the bid among
        strikes that pass the hard filters ({hardLine(data?.config ?? null)}); expiries spanning an earnings report are dropped
        before quoting. A row whose pick fails them is hidden. The soft thresholds ({softLine(data?.config ?? null)}) are a
        marker, never a filter, unless “passing only” is on. The expiry is bold where it pays more a day than the monthly.{" "}
        {bandsLine(data?.config ?? null)} Premium and premium a day are per contract (100 shares) at the bid; the bid and ask
        are the one per-share figure.
      </p>
      <p className="muted">
        History minus implied is a history estimate: the share of past days the band held, against the model's odds today. It is
        not yet a tested edge; the forward record below will say whether rows with a wide gap skate more than priced, once
        it is decision-grade. Closer strikes tend to top that sort, so skate yield and the skate rate stay beside it.
      </p>

      <SkateRecordSection />
    </section>
  );
}
