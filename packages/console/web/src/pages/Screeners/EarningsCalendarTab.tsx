import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { EarningsCalendarRow, EarningsSuggestion, EarningsTrade, EarningsTradeLeg } from "@console/shared";
import { useEarningsCalendar } from "../../lib/api";

/**
 * The earnings calendar (2026-10-17; docs/earnings-screener-plan.md): every announcement in the
 * next 30 days, laid out like the vendor's own calendar, grouped by day. Read-only, and it decides
 * nothing: `python -m cherrypick.technicals earnings-calendar` joins every figure each evening.
 *
 * The direction read, strength and IV rank are the vendor's own values from its security list,
 * which describe the PREVIOUS session's close. The expected move is one standard deviation to the
 * first expiry after the reaction (the broker's IV for that expiry x price x the root of its time),
 * which matched the vendor's figures to under half a point. Strong reads also carry the
 * earnings-navigator spread as target strikes -- and the page says, every time, that the 2020-26
 * replay of that spread lost money and found the direction read no better than a coin on the
 * reaction.
 *
 * Grouped rows keep their order (the console's sorting rule): by day, then symbol.
 */

type Win = "today" | "7" | "30";
const WINDOWS: { key: Win; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7", label: "Next 7 days" },
  { key: "30", label: "Next 30 days" },
];
const STRONG_BULL = "sb";
const STRONG_BEAR = "sbe";
const DEFAULT_DIRS = [STRONG_BULL, STRONG_BEAR];

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** "2026-10-20" -> "Tuesday, Oct 20". */
function dayHeading(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
}

function shortDate(iso: string | null): string {
  if (iso === null) return "—";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function addDays(iso: string, n: number): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);
}

/** Today in New York, as an ISO date. */
function todayNy(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
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

function TimeCell({ r }: { r: EarningsCalendarRow }) {
  if (r.timing === null && r.usualTiming !== null) {
    return (
      <td
        className="muted"
        title={`Not confirmed: no source gives this date's time. ${r.usualTiming === "bmo" ? "Before the open" : "After the close"} is when the company reported last quarter, which held 97% of the time historically. No entry session is worked out from it.`}
      >
        {r.usualTiming.toUpperCase()} est.
      </td>
    );
  }
  if (r.timing === null) {
    return (
      <td className="muted" title="No source gives the time of day for this date, and no earlier report to estimate it from; never assumed">
        —
      </td>
    );
  }
  const entry = r.entrySession ? `enter by the ${shortDate(r.entrySession)} close` : "";
  const source =
    r.timingSource === "broker" ? "the broker's" : r.timingSource === "nasdaq" ? "Nasdaq's" : r.timingSource === "dolt" ? "Dolt's" : "unknown";
  return (
    <td
      title={`${r.timing === "bmo" ? "Before the open" : "After the close"} (${source} time of day); ${entry}; the market reacts ${shortDate(r.reactionDay)}`}
    >
      <span className="chip">{r.timing.toUpperCase()}</span>
    </td>
  );
}

function DateFlags({ r }: { r: EarningsCalendarRow }) {
  const flags: ReactNode[] = [];
  if (r.dateConfirmed === false) {
    flags.push(
      <span key="est" className="chip chip-warn" title="The broker marks this date estimated, and Dolt has none">
        est.
      </span>,
    );
  }
  if (r.dateGapDays !== null && r.dateGapDays > 1) {
    flags.push(
      <span key="gap" className="chip chip-warn" title={`The broker's date and Dolt's are ${r.dateGapDays} days apart; the ${r.dateSource === "broker" ? "broker's" : "Dolt's"} is shown`}>
        dates differ
      </span>,
    );
  }
  return flags.length ? <> {flags}</> : null;
}

function EdgeCell({ r }: { r: EarningsCalendarRow }) {
  if (r.edgeLabel === null) return <td className="muted">—</td>;
  const cls = r.edge === STRONG_BULL ? "chip chip-ok" : r.edge === STRONG_BEAR ? "chip chip-bad" : "chip";
  return (
    <td title="The vendor's direction read, from its security list: it describes the previous session's close">
      <span className={cls}>{r.edgeLabel}</span>
    </td>
  );
}

function StrengthCell({ v }: { v: number | null }) {
  if (v === null) return <td className="muted">—</td>;
  const colour = v >= 7 ? "var(--pos)" : v <= 4 ? "var(--neg)" : "var(--amber-line)";
  return (
    <td title={`Strength ${v}/10 (the security list's rank)`} style={{ whiteSpace: "nowrap" }}>
      <span aria-hidden style={{ display: "inline-flex", gap: 1, verticalAlign: "middle" }}>
        {Array.from({ length: 10 }, (_, i) => (
          <span key={i} style={{ width: 5, height: 10, background: i < v ? colour : "var(--chart-grid-mid)" }} />
        ))}
      </span>{" "}
      <span className="muted">{v}/10</span>
    </td>
  );
}

/** The broker's own rule for an earnings move: 0.85 × the at-the-money straddle on the first expiry
 *  after the reaction, shown as "6.5% (±$23.40)". One standard deviation is in the tooltip. */
function MoveCell({ r }: { r: EarningsCalendarRow }) {
  if (r.emMovePct === null || r.emMove === null) {
    return (
      <td className="muted num" title="No implied vol held for this name's event expiry">
        —
      </td>
    );
  }
  const title =
    `Expected move to ${r.eventExpiry}: 0.85 × the at-the-money straddle (the broker's rule for an earnings move), ` +
    `the straddle read off that expiry's IV of ${r.eventIv === null ? "—" : (100 * r.eventIv).toFixed(1)}%. ` +
    `One standard deviation is ±$${money(r.sdMove)} (${r.sdMovePct === null ? "—" : r.sdMovePct.toFixed(1)}%), about 1.5 times this.`;
  return (
    <td className="num" title={title}>
      {r.emMovePct.toFixed(1)}% <span className="muted">(±${money(r.emMove)})</span>
    </td>
  );
}

function legLine(l: EarningsTradeLeg, right: string): string {
  return (
    `${l.role} ${l.strike} ${right}: ${money(l.bid)} × ${money(l.ask)}, OI ${l.openInterest ?? "—"}, volume ${l.volume ?? "—"}` +
    (l.passes === null ? " — unquoted" : l.passes ? " — passes" : " — fails")
  );
}

/** The spread as read in regular hours: listed strikes, price, share of the width, max loss, and
 *  whether every leg passes the bars. */
function TradeCell({ t }: { t: EarningsTrade }) {
  const right = t.right === "C" ? "call" : "put";
  const [first, second] = t.kind === "credit" ? [t.legs[1], t.legs[0]] : [t.legs[0], t.legs[1]];
  const price = t.mid === null ? "unpriced" : `${t.mid.toFixed(2)} ${t.kind === "credit" ? "cr" : "db"}`;
  const title =
    `${t.kind === "credit" ? "Sell" : "Buy"} the ${first?.strike} ${right}, ${t.kind === "credit" ? "buy" : "sell"} the ` +
    `${second?.strike} ${right}, ${t.expiry}; read ${t.readingAt ?? "—"}.\n` +
    `At mid ${money(t.mid)}, at the natural fill ${money(t.natural)}; ${t.shareOfWidth === null ? "—" : `${(100 * t.shareOfWidth).toFixed(0)}%`} ` +
    `of the ${t.width} width; max loss $${money(t.maxLoss)} a spread.\n` +
    t.legs.map((l) => legLine(l, right)).join("\n") +
    "\nHistory: no edge (the note below the table).";
  // The strikes lead: the column is the table's last, and whatever comes first is what shows.
  return (
    <td title={title}>
      <span className="num">
        {first?.strike}/{second?.strike} {t.right}
      </span>{" "}
      <span className="muted">{shortDate(t.expiry)}</span> <span className="num">{price}</span>
      <br />
      <span className={t.kind === "credit" ? "chip chip-credit" : "chip chip-debit"}>
        {t.kind} {right}
      </span>
      {t.shareOfWidth !== null && <span className="muted"> {(100 * t.shareOfWidth).toFixed(0)}% of width</span>}
      {t.maxLoss !== null && <span className="muted"> · max loss ${money(t.maxLoss)}</span>}
      {t.passes === false && (
        <>
          {" "}
          <span className="chip chip-warn" title="A leg fails the liquidity bars (10% of mid or a nickel, 100 open, 10 traded)">
            thin
          </span>
        </>
      )}
      {t.passes === true && (
        <>
          {" "}
          <span className="chip chip-ok" title="Both legs pass the liquidity bars">
            liquid
          </span>
        </>
      )}
    </td>
  );
}

/** Three states, each labelled so a target price is never read as a strike: read in regular hours
 *  (listed strikes and a price), listed (strikes from the evening's chain listing, not priced yet),
 *  or targets (prices the rule aims at, not strikes). */
function SpreadCell({
  s,
  t,
  listed,
}: {
  s: EarningsSuggestion | null;
  t: EarningsTrade | null;
  listed: { expiry: string; strikes: number[] } | null;
}) {
  if (s === null) return <td className="muted">—</td>;
  if (t !== null) return <TradeCell t={t} />;
  const right = s.right === "C" ? "call" : "put";
  const kindChip = (
    <span className={s.kind === "credit" ? "chip chip-credit" : "chip chip-debit"}>
      {s.kind} {right}
    </span>
  );
  if (listed !== null) {
    // [long, short]; a credit spread reads short first, a debit long first.
    const [long, short] = listed.strikes;
    const shown = s.kind === "credit" ? `${short}/${long}` : `${long}/${short}`;
    const legs =
      s.kind === "credit" ? `sell the ${short} ${right}, buy the ${long} ${right}` : `buy the ${long} ${right}, sell the ${short} ${right}`;
    return (
      <td
        title={`Listed strikes on ${listed.expiry}, from the evening's chain listing: ${legs}. The price and liquidity show once read, at 11:30 and 14:30 ET. History: no edge (the note below the table).`}
      >
        <span className="num">
          {shown} {s.right}
        </span>{" "}
        <span className="muted">{shortDate(listed.expiry)}</span>
        <br />
        {kindChip}
      </td>
    );
  }
  const legs =
    s.kind === "credit"
      ? `sell near ${money(s.short)}, buy at or beyond ${money(s.long)}`
      : `buy near ${money(s.long)}, sell near ${money(s.short)}`;
  const title =
    `TARGET PRICES, not strikes: the rule's ${s.kind} ${right} spread would ${legs}, ${s.expiry ?? "no expiry in range"}. ` +
    "They are matched to listed strikes the evening the calendar is built, for strong reads reporting within the week. " +
    "History: no edge (the note below the table).";
  return (
    <td title={title}>
      <span className="muted num">
        ≈{money(s.kind === "credit" ? s.short : s.long)} / ≈{money(s.kind === "credit" ? s.long : s.short)}
      </span>
      <br />
      {kindChip}{" "}
      <span className="chip" title="Target prices, not listed strikes">
        target
      </span>
    </td>
  );
}

export function EarningsCalendarTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useEarningsCalendar(params.get("day"));
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const win = (params.get("win") as Win | null) ?? "7";
  const time = params.get("time"); // null | "bmo" | "amc"
  const dirParam = params.get("dir");
  const dirs = dirParam === "all" ? null : dirParam ? dirParam.split(",") : DEFAULT_DIRS;
  const allLiquidity = params.get("liq") === "all";
  const somewhat = params.get("liq") === "1,2";
  const allOptions = params.get("opt") === "all";
  const allRegions = params.get("region") === "all";
  const allTrades = params.get("trade") === "all";
  const sector = params.get("sector");
  const q = (params.get("q") ?? "").trim().toUpperCase();

  const all = data?.rows ?? [];
  const latest = data?.days[0] ?? null;
  // Windows count from today (New York) on the newest calendar; from its own session on an older one.
  const ref = data?.day && data.day !== latest ? data.day : todayNy();
  const inWindow = (r: EarningsCalendarRow, w: Win) =>
    r.date >= ref && (w === "30" || r.date <= (w === "today" ? ref : addDays(ref, 7)));
  const passes = (r: EarningsCalendarRow) =>
    (dirs === null || (r.edge !== null && dirs.includes(r.edge))) &&
    (allLiquidity || r.liquidityRank === 1 || (somewhat && r.liquidityRank === 2)) &&
    (allOptions || r.optionable === true) &&
    (allRegions || r.region === "US") &&
    // A spread read as thin is hidden; one not read yet stays, its targets shown.
    (allTrades || r.trade?.passes !== false) &&
    (sector === null || r.sector === sector) &&
    (q === "" || r.symbol.includes(q));
  const filtered = all.filter(passes);
  const shown = filtered.filter((r) => inWindow(r, win) && (time === null || (r.timing ?? r.usualTiming) === time));
  const groups = new Map<string, EarningsCalendarRow[]>();
  for (const r of shown) groups.set(r.date, [...(groups.get(r.date) ?? []), r]);
  const sectors = [...new Set(all.map((r) => r.sector).filter((s): s is string => s !== null))].sort();
  const toggleDir = (code: string) => {
    const cur = new Set(dirs ?? []);
    if (cur.has(code)) cur.delete(code);
    else cur.add(code);
    const list = [...cur].sort();
    set("dir", list.length === 0 ? "all" : list.join(",") === [...DEFAULT_DIRS].sort().join(",") ? null : list.join(","));
  };
  const othersOn = dirs === null;

  return (
    <section className="card">
      <div className="card-head">
        <h2>Earnings calendar</h2>
        <span className="chip">read-only</span>
        {data?.day && (
          <span className="card-asof">
            built {data.day} · direction, strength and IV rank of {data.inputs.securityList ?? "—"} · moves from the {data.inputs.expiryIv ?? "—"} IVs
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        {data && data.days.length > 0 && (
          <select
            className="chip review-session-select"
            aria-label="Calendar day"
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
        <div className="mode-toggle" style={{ marginLeft: 0 }}>
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              type="button"
              className={win === w.key ? "mode-btn active" : "mode-btn"}
              onClick={() => set("win", w.key === "7" ? null : w.key)}
            >
              {w.label} ({all.filter((r) => inWindow(r, w.key)).length})
            </button>
          ))}
        </div>
        <div className="mode-toggle" style={{ marginLeft: 0 }}>
          {(["bmo", "amc"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className={time === t ? "mode-btn active" : "mode-btn"}
              onClick={() => set("time", time === t ? null : t)}
              title={t === "bmo" ? "Before the open" : "After the close"}
            >
              {t === "bmo" ? "Before market" : "After market"} ({filtered.filter((r) => inWindow(r, win) && (r.timing ?? r.usualTiming) === t).length})
            </button>
          ))}
        </div>
        <Flag on={othersOn ? false : (dirs ?? []).includes(STRONG_BULL)} onChange={() => toggleDir(STRONG_BULL)} title="Strong bull direction reads">
          Strong bull
        </Flag>
        <Flag on={othersOn ? false : (dirs ?? []).includes(STRONG_BEAR)} onChange={() => toggleDir(STRONG_BEAR)} title="Strong bear direction reads">
          Strong bear
        </Flag>
        <Flag on={othersOn} onChange={(on) => set("dir", on ? "all" : null)} title="Every direction read, the mixed ones included">
          All directions
        </Flag>
        <Flag
          on={!allLiquidity}
          onChange={(on) => set("liq", on ? null : "all")}
          title="Very liquid options only (the security list's liquidity rank 1); off shows every rating"
        >
          Very liquid
        </Flag>
        {!allLiquidity && (
          <Flag on={somewhat} onChange={(on) => set("liq", on ? "1,2" : null)} title="Also the names rated somewhat liquid (rank 2)">
            + somewhat
          </Flag>
        )}
        <Flag on={!allOptions} onChange={(on) => set("opt", on ? null : "all")} title="Optionable names only">
          Optionable
        </Flag>
        <Flag on={!allRegions} onChange={(on) => set("region", on ? null : "all")} title="US listings only: depository receipts left out, as the vendor's US region filter does">
          US
        </Flag>
        <Flag
          on={!allTrades}
          onChange={(on) => set("trade", on ? null : "all")}
          title="Hide names whose suggested spread was read in regular hours and a leg failed the liquidity bars (10% of mid or a nickel, 100 open, 10 traded). A spread not read yet stays shown, with its target strikes."
        >
          Liquid trade
        </Flag>
        <select className="chip review-session-select" aria-label="Sector" value={sector ?? ""} onChange={(e) => set("sector", e.target.value || null)}>
          <option value="">all sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <input
          className="chip review-session-select"
          value={params.get("q") ?? ""}
          placeholder="symbol"
          aria-label="Filter by symbol"
          onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
        />
        <span className="chip">
          {shown.length} of {all.length} announcements
        </span>
      </div>

      {isError && <p className="muted">Could not read the earnings calendar.</p>}
      {isLoading && <p className="muted">Reading the calendar…</p>}
      {data && data.day === null && <p className="muted">No calendar written yet: the earnings-calendar job runs at 19:10 ET on trading days.</p>}

      {shown.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Company</th>
                <th title="Before the open or after the close; a dash when no source says">Time</th>
                <th>Direction</th>
                <th>Strength</th>
                <th className="num">IV rank</th>
                <th className="num">Last price</th>
                <th className="num" title="0.85 × the at-the-money straddle on the first expiry after the reaction: the broker's rule for an earnings move">
                  Exp. move
                </th>
                <th>Sector</th>
                <th title="The earnings-navigator spread for strong reads: debit under the IV-rank cut, credit over it">Spread</th>
              </tr>
            </thead>
            <tbody>
              {[...groups.entries()].map(([day, rows]) => [
                <tr key={`h-${day}`} className="group-row">
                  <td colSpan={10}>
                    <strong>{dayHeading(day)}</strong> <span className="chip">{rows.length}</span>
                  </td>
                </tr>,
                ...rows.map((r) => (
                  <tr key={`${day}-${r.symbol}`}>
                    <td>
                      <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`} title={`Open ${r.symbol}'s chart`}>
                        {r.symbol}
                      </Link>
                      <DateFlags r={r} />
                    </td>
                    <td title={r.company ?? undefined} style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.company ?? "—"}
                    </td>
                    <TimeCell r={r} />
                    <EdgeCell r={r} />
                    <StrengthCell v={r.strength} />
                    <td className="num">{r.ivRank === null ? "—" : `${Math.round(r.ivRank)}%`}</td>
                    <td className="num">{r.price === null ? "—" : `$${money(r.price)}`}</td>
                    <MoveCell r={r} />
                    <td>{r.sector ?? "—"}</td>
                    <SpreadCell s={r.suggestion} t={r.trade} listed={r.listed} />
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        </div>
      )}
      {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}

      <p className="muted" style={{ marginTop: 8 }}>
        <strong>The suggested spread has no edge in history.</strong> Replayed over 2020-26 (3,587 priced announcements), it lost 4.3% on
        risk on the defaults and in every variant tried, and the direction read called the reaction 48-49% of the time — a coin. It is
        shown because the screen shows it, not as advice (docs/earnings-screener-plan.md). Direction, strength and IV rank are the
        vendor's values as of the previous session's close; the IV-rank cut is {data?.ivRankCut ?? 50}. A spread shows
        listed strikes once the evening's chain listing has matched them and its price once a regular-hours reading has; until
        then it shows the target prices, marked "target".
      </p>
    </section>
  );
}
