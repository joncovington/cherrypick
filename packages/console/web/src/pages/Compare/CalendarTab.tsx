import { useState } from "react";
import type { PnlCalendarResult, PnlDay, TradingMode } from "@console/shared";
import { Link } from "react-router-dom";
import { Card } from "../../components/DataTable";
import { fmtMoney } from "../../lib/format";
import { usePnlCalendar } from "../../lib/api";
import { armOfTag } from "../../lib/armTag";
import { useFeatures } from "../../lib/useFeatures";
import { isModuleVisible } from "../../lib/visibility";

/**
 * Compare's calendar tab: every arm's settled net per session, as a month grid with a weekly total
 * (docs/strategy-analysis-plan.md Phase 4). The nets are core.metrics' own per-session series, the
 * one each performance tab reads, over the suite window; this file only adds them up for display.
 *
 * A day drills to the arms that traded it, each linking to its module. Tone comes from a number's
 * sign; a day nothing traded is blank, never $0.00.
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

/** The arms that traded one session, largest move first, each linking to its module. */
export function DayDetail({ session, total }: { session: string; total: DayTotal }) {
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
            {arms.map((r) => (
              <tr key={`${r.module}-${r.tag}`}>
                <td>
                  <Link to={`/${r.module}/history`}>{r.module}</Link>
                </td>
                <td className="mono">{r.tag}</td>
                <td className={tone(r.net)}>{fmtMoney(r.net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CalendarView({ data, visible }: { data: PnlCalendarResult; visible: (module: string) => boolean }) {
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
          settled net per session, from {data.from ?? "the first session"} — the suite window, not each arm's era
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
      {picked !== null && pickedTotal !== undefined && <DayDetail session={picked} total={pickedTotal} />}
    </>
  );
}

export function CalendarTab({ mode = "paper" }: { mode?: TradingMode }) {
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
