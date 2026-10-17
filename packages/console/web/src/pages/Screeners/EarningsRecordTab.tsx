import { Link } from "react-router-dom";
import type { EarningsRecordRow, EarningsRecordTotals, EarningsRecordTrade } from "@console/shared";
import { useEarningsRecord } from "../../lib/api";

/**
 * The earnings calendar's forward record (2026-10-17; docs/earnings-screener-plan.md, step 5): every
 * past announcement, what the calendar showed the session before it, and what happened. Read-only;
 * `technicals earnings-record` builds every figure each morning.
 *
 * Money follows the suite's layout: signed whole-position cash for one spread (a credit received +,
 * a debit paid -), entry + exit = gross, fees and the modelled slippage in their own columns, net =
 * gross - fees - slippage. The spread is entered at mid on its entry session's last regular-hours
 * reading and closed at mid on the reaction day's; a day-2 close is shown beside it. A journal: rows
 * keep their order, newest first, and do not sort.
 */

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v < 0 ? "−" : v > 0 ? "+" : ""}$${s}`;
}

function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(100 * v).toFixed(digits)}%`;
}

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function Totals({ label, t }: { label: string; t: EarningsRecordTotals }) {
  return (
    <span className="chip" title={`${t.n} spreads closed on the ${label}: net, share won, return on risk`}>
      {label}: {t.n} spreads · net <span className={t.net >= 0 ? "pnl-pos" : "pnl-neg"}>{money(t.net)}</span> · won {pct(t.won)} · RoR{" "}
      {pct(t.returnOnRisk, 1)}
    </span>
  );
}

function NetCell({ t }: { t: EarningsRecordTrade | null }) {
  if (t === null) return <td className="muted num">—</td>;
  return (
    <td className={`num ${t.net >= 0 ? "pnl-pos" : "pnl-neg"}`} title={`gross ${money(t.gross)} − fees ${money(t.fees)} − slippage ${money(t.slippage)}`}>
      {money(t.net)}
    </td>
  );
}

function SpreadCells({ r }: { r: EarningsRecordRow }) {
  const s = r.spread;
  if (s === null) {
    return (
      <>
        <td className="muted" title="No suggested spread: not a strong read, or no entry session (time of day unknown)">
          —
        </td>
        {Array.from({ length: 6 }, (_, i) => (
          <td key={i} className="muted num">
            —
          </td>
        ))}
      </>
    );
  }
  if (s.state !== "entered" || s.kind === null) {
    return (
      <>
        <td className="muted">{s.state}</td>
        {Array.from({ length: 6 }, (_, i) => (
          <td key={i} className="muted num">
            —
          </td>
        ))}
      </>
    );
  }
  const t = s.react;
  return (
    <>
      <td title={`${s.expiry}; ${s.liquidAtEntry === false ? "thin at entry" : s.liquidAtEntry ? "liquid at entry" : "liquidity unread"}`}>
        {/* The strikes come [long, short]; a credit spread reads short first, as on the calendar. */}
        {s.kind} {s.right === "C" ? "call" : "put"} {(s.kind === "credit" ? [...(s.strikes ?? [])].reverse() : s.strikes ?? []).join("/")}
        {s.liquidAtEntry === false && <span className="chip chip-warn"> thin</span>}
      </td>
      <td className="num">{money(t?.entry)}</td>
      <td className="num">{money(t?.exit)}</td>
      <td className="num">{money(t?.gross)}</td>
      <td className="num">{t ? money(-t.fees) : "—"}</td>
      <td className="num">{t ? money(-t.slippage) : "—"}</td>
      <NetCell t={t} />
    </>
  );
}

export function EarningsRecordTab() {
  const { data, isLoading, isError } = useEarningsRecord();
  const s = data?.summary ?? null;
  const rows = data?.rows ?? [];
  return (
    <section className="card">
      <div className="card-head">
        <h2>Earnings record</h2>
        <span className="chip">record-only</span>
        {data?.asOf && <span className="card-asof">as of {data.asOf}</span>}
      </div>
      {isError && <p className="muted">Could not read the earnings record.</p>}
      {isLoading && <p className="muted">Reading the record…</p>}
      {data && data.asOf === null && (
        <p className="muted">No record yet: `technicals earnings-record` writes it each morning at 06:50 ET, once announcements on the calendar have reacted.</p>
      )}
      {s && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
          <span className="chip" title="Announcements whose reaction is measured, and the share that stayed inside the one-SD move">
            {s.measured} of {s.announcements} measured · inside ±1 SD {pct(s.insideSd)} · median |move| {s.medianMoveOverSd?.toFixed(2) ?? "—"} SD
          </span>
          <span className="chip" title="Strong reads whose reaction went their way (history: 48-49%)">
            direction called {pct(s.directionRight)} of {s.strongReads} strong reads
          </span>
          <Totals label="reaction day" t={s.react} />
          <Totals label="day 2" t={s.day2} />
        </div>
      )}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Report</th>
                <th>Direction</th>
                <th className="num" title="The one-SD move the calendar showed the session before">
                  ±1 SD
                </th>
                <th className="num" title="The reaction-day close against the session before's">
                  Move
                </th>
                <th className="num">|Move| / SD</th>
                <th title="Strong reads only: did the reaction go the read's way">Called</th>
                <th>Spread</th>
                <th className="num" title="Opening at mid: a credit received +, a debit paid −">
                  Entry
                </th>
                <th className="num" title="Closing at mid on the reaction day">
                  Exit
                </th>
                <th className="num">Gross</th>
                <th className="num">Fees</th>
                <th className="num" title="Modelled slippage: fills at mid do not concede it">
                  Slippage
                </th>
                <th className="num">Net</th>
                <th className="num" title="Net had it closed the session after instead">
                  Net, day 2
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.symbol}-${r.date}`}>
                  <td>
                    <Link className="module-link" to={`/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`}>
                      {r.symbol}
                    </Link>
                  </td>
                  <td title={`calendar of ${r.calendarDay}; reacted ${r.reactionDay}`}>
                    {shortDate(r.date)} <span className="muted">{r.timing ? r.timing.toUpperCase() : "time unknown"}</span>
                  </td>
                  <td>{r.edgeLabel ?? "—"}</td>
                  <td className="num">{r.sdMovePct === null ? "—" : `±${r.sdMovePct.toFixed(1)}%`}</td>
                  <td className={`num ${r.movePct === null ? "muted" : r.movePct >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                    {r.movePct === null ? "—" : `${r.movePct > 0 ? "+" : r.movePct < 0 ? "−" : ""}${Math.abs(r.movePct).toFixed(1)}%`}
                  </td>
                  <td className="num">
                    {r.moveOverSd === null ? "—" : r.moveOverSd.toFixed(2)}
                    {r.insideSd === false && <span className="muted"> outside</span>}
                  </td>
                  <td className={r.directionRight === null ? "muted" : r.directionRight ? "pnl-pos" : "pnl-neg"}>
                    {r.directionRight === null ? "—" : r.directionRight ? "yes" : "no"}
                  </td>
                  <SpreadCells r={r} />
                  <NetCell t={r.spread?.day2 ?? null} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ marginTop: 8 }}>
        The forward check on the 2020-26 replay, which found no edge in the suggested spread (−4.3% on risk) and the direction read
        right on 48-49% of reactions. One spread a row, at mid on regular-hours readings; costs from the suite's fee schedule.
      </p>
    </section>
  );
}
