import { Fragment, useState } from "react";
import type { PnlCalendarResult, PnlDay, PnlExcursion, PnlTrade, TradingMode } from "@console/shared";
import { Link } from "react-router-dom";
import { Card } from "../../components/DataTable";
import { fmtMoney } from "../../lib/format";
import { usePnlCalendar, usePnlExcursion, usePnlTrades } from "../../lib/api";
import { Spark } from "../../components/chart/Spark";
import { TradeTrace } from "./TradeTrace";
import { useMode } from "../../lib/useMode";
import { armOfTag } from "../../lib/armTag";
import { useFeatures } from "../../lib/useFeatures";
import { isModuleVisible } from "../../lib/visibility";

/**
 * Compare's calendar tab: every arm's settled net per session, as a month grid with a weekly total
 * (docs/strategy-analysis-plan.md Phase 4). The nets are core.metrics' own per-session series, the
 * one each performance tab reads, over the suite window; this file only adds them up for display.
 *
 * A day drills to the arms that traded it, and an arm to its trades (`core.metrics trades`, the same
 * read as the nets). Paper or live follows the page's mode. Tone comes from a number's sign; a day
 * nothing traded is blank, never $0.00.
 */

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"] as const;

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function parse(session: string): Date {
  return new Date(`${session}T00:00:00Z`);
}

/** The Monday of `session`'s week. */
function mondayOf(session: string): string {
  const d = parse(session);
  const back = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - back);
  return iso(d);
}

function addDays(session: string, n: number): string {
  const d = parse(session);
  d.setUTCDate(d.getUTCDate() + n);
  return iso(d);
}

interface DayTotal {
  net: number;
  arms: PnlDay[];
}

/** Sum the picked rows per session. Presentation arithmetic: the rows are core's. */
export function totalsBySession(rows: PnlDay[]): Map<string, DayTotal> {
  const out = new Map<string, DayTotal>();
  for (const r of rows) {
    const t = out.get(r.session) ?? { net: 0, arms: [] };
    t.net += r.net;
    t.arms.push(r);
    out.set(r.session, t);
  }
  return out;
}

function tone(v: number): string {
  return v >= 0 ? "pnl-pos" : "pnl-neg";
}

/** One month: a row per week, Mon–Fri, and the week's total. Weekends never trade here. */
export function MonthGrid({
  month,
  totals,
  picked,
  onPick,
}: {
  month: string;
  totals: Map<string, DayTotal>;
  picked: string | null;
  onPick: (session: string) => void;
}) {
  const first = `${month}-01`;
  const weeks: string[] = [];
  for (let monday = mondayOf(first); monday.slice(0, 7) <= month; monday = addDays(monday, 7)) {
    // A week belongs to this month if any of its weekdays does.
    if ([0, 1, 2, 3, 4].some((i) => addDays(monday, i).slice(0, 7) === month)) weeks.push(monday);
  }
  const monthNet = [...totals.entries()].filter(([s]) => s.slice(0, 7) === month).reduce((a, [, t]) => a + t.net, 0);
  return (
    <div style={{ marginBottom: "1rem" }}>
      <div className="card-subhead">
        {month} <span className={tone(monthNet)}>{fmtMoney(monthNet)}</span>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            {WEEKDAYS.map((d) => (
              <th key={d}>{d}</th>
            ))}
            <th>week</th>
          </tr>
        </thead>
        <tbody>
          {weeks.map((monday) => {
            const days = [0, 1, 2, 3, 4].map((i) => addDays(monday, i));
            const weekNet = days.reduce((a, s) => a + (totals.get(s)?.net ?? 0), 0);
            const traded = days.some((s) => totals.has(s));
            return (
              <tr key={monday}>
                {days.map((s) => {
                  const t = totals.get(s);
                  // A day of the neighbouring month is drawn too, dimmed and not a link, so a week
                  // that straddles two months still adds up to its total in both.
                  const own = s.slice(0, 7) === month;
                  return (
                    <td key={s} className={own ? undefined : "muted"}>
                      <span className="muted" style={{ fontSize: 10 }}>
                        {s.slice(8)}
                      </span>{" "}
                      {t !== undefined && !own && <span>{fmtMoney(t.net)}</span>}
                      {t !== undefined && own && (
                        <button
                          type="button"
                          className={`btn btn-quiet ${tone(t.net)}`}
                          style={{ padding: 0, fontWeight: picked === s ? 700 : undefined }}
                          onClick={() => onPick(s)}
                          title={`${String(t.arms.length)} arm-sessions`}
                        >
                          {fmtMoney(t.net)}
                        </button>
                      )}
                    </td>
                  );
                })}
                <td className={traded ? tone(weekNet) : "muted"}>{traded ? fmtMoney(weekNet) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** `HH:MM` of an ISO stamp, with the date in front when it is not the session's own day. */
export function stamp(t: string | null, session: string): string {
  if (t === null || t.length < 16) return "—";
  const day = t.slice(0, 10);
  return day === session ? t.slice(11, 16) : `${day.slice(5)} ${t.slice(11, 16)}`;
}

const SLIPPAGE_MODEL: Record<string, string> = { in_gross: "in gross", cost: "cost" };

const EXCURSION_BASIS: Record<string, string> = {
  gross_at_mid: "gross at mid, built from the legs' marks",
  gross_as_recorded: "gross, as the module records its marks",
  net_of_fees_as_recorded: "net of fees as the live marks record it, before settlement; the exit is the trade's net",
};

/** `MM-DD HH:MM` of an ISO stamp. */
function at(t: string | undefined): string {
  return t === undefined ? "—" : `${t.slice(5, 10)} ${t.slice(11, 16)}`;
}

/**
 * One position's excursion (core.excursions): the best and worst it stood between entry and exit,
 * and when, on the basis its module records -- named, because a gross path and a net one are not
 * the same line. A module that keeps no path says why; nothing is drawn in its place.
 */
export function ExcursionView({ x }: { x: PnlExcursion }) {
  if (!x.ok) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        excursion could not be read — {x.error ?? "unknown"}
      </p>
    );
  }
  if (x.recorded !== true) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        no excursion — {x.reason ?? "not recorded"}
      </p>
    );
  }
  const values = (x.path ?? []).map(([, v]) => v);
  const domain: [number, number] = [Math.min(0, ...values), Math.max(0, ...values)];
  return (
    <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap", fontSize: 11, padding: "0.3rem 0" }}>
      <span>
        best <span className="pnl-pos">{fmtMoney(x.best?.pnl ?? null)}</span> <span className="muted">{at(x.best?.at)}</span>
      </span>
      <span>
        worst <span className="pnl-neg">{fmtMoney(x.worst?.pnl ?? null)}</span> <span className="muted">{at(x.worst?.at)}</span>
      </span>
      {x.endpoint !== undefined && x.endpoint !== null && (
        <span>
          exit {fmtMoney(x.endpoint)}
          {x.reconciles === true && <span className="muted"> · equals the recorded gross</span>}
          {x.reconciles === false && (
            <span className="chip chip-warn" style={{ marginLeft: 6 }}>
              does not reconcile with the recorded gross {fmtMoney(x.gross ?? null)}
            </span>
          )}
        </span>
      )}
      <span style={{ width: 160, height: 26, display: "inline-block" }}>
        <Spark values={values} domain={domain} title="mark P&L from entry to exit" />
      </span>
      <span className="muted">
        {EXCURSION_BASIS[x.basis ?? ""] ?? x.basis} · {String(x.ticks ?? 0)} ticks
        {(x.refused_ticks ?? 0) > 0 ? `, ${String(x.refused_ticks)} refused (a leg or shares unpriced)` : ""}
      </span>
    </div>
  );
}

function TradeExcursion({ module, position, mode }: { module: string; position: string; mode: TradingMode }) {
  const { data, isLoading, isError } = usePnlExcursion(module, position, mode);
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  if (isError || data === undefined) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        excursion could not be read
      </p>
    );
  }
  return <ExcursionView x={data} />;
}

/**
 * One arm's trades on one session, in the suite's money layout: gross, then each cost in its own
 * column, then net. Slippage names its model: `in gross` is already conceded in the fill and is shown
 * as a measure; `cost` is charged and is subtracted once. An unrecorded field reads n/r, never $0.00.
 * The rows come from the read behind the cell, so their nets sum to it; if they ever do not, the
 * table says so rather than showing two figures that cannot be reconciled.
 */
export function TradesTable({
  trades,
  cellNet,
  module,
  mode = "paper",
}: {
  trades: PnlTrade[];
  cellNet: number;
  /** Where the trades came from; with it, a trade that carries a position id opens to its excursion. */
  module?: string;
  mode?: TradingMode;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const nr = (v: number | null) => (v === null ? "n/r" : fmtMoney(v));
  const sum = trades.reduce((a, t) => a + (t.net_pnl ?? 0), 0);
  const reconciles = Math.abs(sum - cellNet) < 0.01;
  return (
    <>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>entry</th>
              <th>exit</th>
              <th>symbol</th>
              <th>structure</th>
              <th>gross</th>
              <th>fees</th>
              <th>settlement</th>
              <th title="in gross: conceded in the modelled fill, a measure; cost: charged, subtracted once">slippage</th>
              <th>net</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <Fragment key={`${t.entry_time ?? ""}-${String(i)}`}>
                <tr>
                  <td className="mono">
                    {module !== undefined && t.position_id !== null ? (
                      <button
                        type="button"
                        className="btn btn-quiet mono"
                        style={{ padding: 0 }}
                        aria-expanded={open === i}
                        onClick={() => setOpen(open === i ? null : i)}
                        title="how far this trade went for and against, how it got in, and what was decided while it was held"
                      >
                        {open === i ? "▾" : "▸"} {stamp(t.entry_time, t.session)}
                      </button>
                    ) : (
                      stamp(t.entry_time, t.session)
                    )}
                  </td>
                <td className="mono">{stamp(t.exit_time, t.session)}</td>
                <td>{t.symbol ?? "—"}</td>
                <td>{t.strategy ?? "—"}</td>
                <td>{nr(t.gross_pnl)}</td>
                <td>{nr(t.fees)}</td>
                <td>{nr(t.settlement)}</td>
                <td>
                  {nr(t.slippage)}
                  {t.slippage !== null && t.slippage_basis !== null && (
                    <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>
                      {SLIPPAGE_MODEL[t.slippage_basis] ?? t.slippage_basis}
                    </span>
                  )}
                </td>
                <td className={t.net_pnl === null ? undefined : tone(t.net_pnl)}>{nr(t.net_pnl)}</td>
                </tr>
                {open === i && module !== undefined && t.position_id !== null && (
                  <tr>
                    <td colSpan={9}>
                      <TradeExcursion module={module} position={t.position_id} mode={mode} />
                      <TradeTrace module={module} position={t.position_id} mode={mode} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {!reconciles && (
        <p style={{ fontSize: 11 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>
            does not reconcile
          </span>
          these trades sum to {fmtMoney(sum)}, the cell says {fmtMoney(cellNet)}
        </p>
      )}
    </>
  );
}

function ArmTrades({ row, session, mode }: { row: PnlDay; session: string; mode: TradingMode }) {
  const { data, isLoading, isError } = usePnlTrades(row.module, session, row.tag, mode, true);
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  if (isError || data === undefined || !data.ok) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        trades could not be read{data?.error ? ` — ${data.error}` : ""}
      </p>
    );
  }
  return <TradesTable trades={data.trades} cellNet={row.net} module={row.module} mode={mode} />;
}

/** The arms that traded one session, largest move first; an arm opens to its trades. */
export function DayDetail({ session, total, mode }: { session: string; total: DayTotal; mode: TradingMode }) {
  const [open, setOpen] = useState<string | null>(null);
  const arms = [...total.arms].sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  return (
    <div style={{ marginTop: "0.6rem" }}>
      <div className="card-subhead">
        {session} <span className={tone(total.net)}>{fmtMoney(total.net)}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>module</th>
              <th>arm</th>
              <th>net</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((r) => {
              const key = `${r.module}-${r.tag}`;
              return (
                <Fragment key={key}>
                  <tr>
                    <td>
                      <Link to={`/${r.module}/history${mode === "live" ? "?mode=live" : ""}`}>{r.module}</Link>
                    </td>
                    <td className="mono">
                      <button
                        type="button"
                        className="btn btn-quiet"
                        style={{ padding: 0 }}
                        aria-expanded={open === key}
                        onClick={() => setOpen(open === key ? null : key)}
                        title="this arm's trades on the day"
                      >
                        {open === key ? "▾" : "▸"} {r.tag}
                      </button>
                    </td>
                    <td className={tone(r.net)}>{fmtMoney(r.net)}</td>
                  </tr>
                  {open === key && (
                    <tr>
                      <td colSpan={3}>
                        <ArmTrades row={r} session={session} mode={mode} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CalendarView({
  data,
  visible,
}: {
  data: PnlCalendarResult;
  visible: (module: string) => boolean;
}) {
  const [module, setModule] = useState<string | null>(null);
  const [arm, setArm] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const shown = data.days.filter((d) => visible(d.module));
  const modules = [...new Set(shown.map((d) => d.module))].sort();
  const arms = module === null ? [] : [...new Set(shown.filter((d) => d.module === module).map((d) => armOfTag(d.tag)))].sort();
  const rows = shown.filter((d) => (module === null || d.module === module) && (arm === null || armOfTag(d.tag) === arm));
  const totals = totalsBySession(rows);
  const months = [...new Set(rows.map((d) => d.session.slice(0, 7)))].sort().reverse();
  const failures = data.failures.filter((f) => visible(f.module));
  const pickedTotal = picked === null ? undefined : totals.get(picked);
  return (
    <>
      <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginBottom: "0.6rem", flexWrap: "wrap" }}>
        <select
          value={module ?? ""}
          onChange={(e) => {
            setModule(e.target.value === "" ? null : e.target.value);
            setArm(null);
          }}
        >
          <option value="">every module</option>
          {modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        {module !== null && (
          <select value={arm ?? ""} onChange={(e) => setArm(e.target.value === "" ? null : e.target.value)}>
            <option value="">every arm</option>
            {arms.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        )}
        <span className="muted" style={{ fontSize: 11 }}>
          {data.mode} · settled net per session, from {data.from ?? "the first session"} — the suite window, not
          each arm's era
        </span>
      </div>
      {failures.map((f) => (
        <p key={f.module} className="muted" style={{ fontSize: 12 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>
            {f.module}
          </span>
          could not be read — {f.error}
        </p>
      ))}
      {months.length === 0 ? (
        <p className="muted">nothing settled in this window</p>
      ) : (
        months.map((m) => <MonthGrid key={m} month={m} totals={totals} picked={picked} onPick={setPicked} />)
      )}
      {picked !== null && pickedTotal !== undefined && (
        <DayDetail key={picked} session={picked} total={pickedTotal} mode={data.mode} />
      )}
    </>
  );
}

export function CalendarTab() {
  const [mode] = useMode();
  const { data, isLoading, isError, dataUpdatedAt } = usePnlCalendar(mode);
  const features = useFeatures();
  const visible = (module: string) => (features === undefined ? true : isModuleVisible(module, features));
  return (
    <div className="cards cards-wide">
      <Card title="P&L calendar" updatedAt={dataUpdatedAt} isError={isError}>
        {isLoading || data === undefined ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : (
          <CalendarView data={data} visible={visible} />
        )}
      </Card>
    </div>
  );
}
