import type {
  BacktestArmReport,
  BacktestGapStats,
  BacktestInventory,
  BacktestInventoryStore,
  BacktestOverview,
  BacktestRunReport,
  BacktestStore,
  BacktestSideReading,
  BacktestValidation,
  BridgedResult,
  StudyResult,
  StudyRow,
} from "@console/shared";
import { isBacktestCapital, isBacktestRiskPct, isBacktestRiskUnit, type BacktestRiskUnit } from "@console/shared";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { CardHelpButton, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { signTone, useBridged } from "../../lib/bridged";
import { fmtCash, fmtCount, fmtNum } from "../../lib/format";
import { RunCards } from "./RunCards";
import { studyHref } from "./studyIndex";

/**
 * The Backtest page (2026-10-17): `packages/backtest`'s replays over licensed vendor history, read
 * through `/api/backtest/overview` and `/api/backtest/runs/:id` (`services/backtestBridge.ts`).
 *
 * The page is reached only while the suite says `backtest: true` (`visibility.ts`, fail closed), and
 * the server refuses both reads on its own otherwise. Every verdict here is the module's: the
 * unlock, each check, the paired interval, `no_exposure`. Nothing is recomputed; a null is an em
 * dash, and tone comes only from a net figure's sign (interval ends are never toned).
 *
 * Money is the suite's one layout: "net" is net of fees, signed, whole-position dollars. A run's
 * per-session figures are the replay ledger's `net_pnl`; its fees are already out of them and are
 * shown beside, never subtracted again.
 */

/** The licence text, behind the page's amber flag. It is read before anything is copied off the page. */
export const LICENCE_HELP: CardHelpContent = {
  title: "Licensed vendor data",
  body: (
    <HelpWarn title="Never shared">
      <p>
        Everything on this page is computed from historical data licensed to this machine&apos;s owner. It stays on this console: do not
        screenshot, export, copy or share it. Never in the repo, a PR, an issue or a published page.
      </p>
    </HelpWarn>
  ),
};

/** The amber "licensed vendor data" flag at the head of a tab; it opens the licence text. */
export function LicenceFlag({ inline = false }: { inline?: boolean }) {
  const flag = <CardHelpButton cardTitle="Backtest" help={LICENCE_HELP} flag="licensed vendor data" />;
  return inline ? <span className="lic-flag">{flag}</span> : <div className="bt-pagehead lic-flag">{flag}</div>;
}

function useOverview() {
  return useBridged<BacktestOverview>(["backtest", "overview"], "/api/backtest/overview", 60_000);
}

/** The shared head of every tab: the note, then the overview's loading/failure state. */
export function WithOverview({ children }: { children: (d: BacktestOverview, updatedAt: number) => ReactNode }) {
  const { data, isLoading, isError, error, dataUpdatedAt } = useOverview();
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "backtest read unavailable"}</p>;
  else body = children(data.data, dataUpdatedAt);
  return (
    <>
      <LicenceFlag />
      {body}
    </>
  );
}

// ------------------------------------------------------------------ formatting

export function span(s: [string, string] | null | undefined): string {
  return s === null || s === undefined ? "—" : `${s[0]} → ${s[1]}`;
}

/** An open-shaped measure value, as written. */
export function measure(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? fmtCount(v) : String(Math.round(v * 10_000) / 10_000);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

/** `created_at` (epoch seconds) as New York wall clock, `2026-10-17 09:30`. */
export function etStamp(sec: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(sec * 1000));
  const p = (t: string) => parts.find((x) => x.type === t)?.value ?? "";
  return `${p("year")}-${p("month")}-${p("day")} ${p("hour")}:${p("minute")}`;
}

// ------------------------------------------------------------------ data

/** "2.65 GB" / "31 MB". */
export function fmtBytes(b: number | undefined): string {
  if (b === undefined) return "—";
  return b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${Math.round(b / 1e6)} MB`;
}

/** The largest table's rows and span, the one line a store is read by. */
export function mainTable(s: BacktestInventoryStore): string {
  const t = Object.entries(s.tables ?? {}).sort(([, a], [, b]) => b.rows - a.rows)[0];
  if (!t) return "—";
  const [name, v] = t;
  return `${name}: ${v.rows.toLocaleString("en-US")} rows${v.span ? ` · ${v.span[0]} to ${v.span[1]}` : ""}`;
}

// ------------------------------------------------------------------ runs

/** The cards' choices beyond the run: the account, the lens's risk and unit, the sizing check's cap.
 *  Null is the package's default for each, and keeps it out of the URL and the query key. */
export interface RunChoices {
  capital: number | null;
  risk?: number | null;
  unit?: BacktestRiskUnit | null;
  dd?: number | null;
}

export function useRun(id: string | null, { capital, risk = null, unit = null, dd = null }: RunChoices) {
  // The default account (the run's peak risk) and defaults elsewhere keep the plain key; a chosen
  // account, then each other choice, extend it.
  const key: unknown[] = ["bridged", "backtest", "run", id];
  if (capital !== null || risk !== null || unit !== null || dd !== null) key.push(capital);
  if (risk !== null) key.push("risk", risk);
  if (unit !== null) key.push("unit", unit);
  if (dd !== null) key.push("dd", dd);
  return useQuery<BridgedResult<BacktestRunReport>>({
    queryKey: key,
    enabled: id !== null,
    // Keep the last account's cards on screen while another loads.
    placeholderData: (prev) => prev,
    queryFn: async () => {
      const params = new URLSearchParams();
      if (capital !== null) params.set("capital", String(capital));
      if (risk !== null) params.set("risk", String(risk));
      if (unit !== null) params.set("unit", unit);
      if (dd !== null) params.set("dd", String(dd));
      const q = params.size === 0 ? "" : `?${params.toString()}`;
      const res = await fetch(`/api/backtest/runs/${encodeURIComponent(id ?? "")}${q}`);
      if (!res.ok) throw new Error(`run ${id ?? ""}: HTTP ${String(res.status)}`);
      return (await res.json()) as BridgedResult<BacktestRunReport>;
    },
    // A run's report is written once and never changes.
    staleTime: Infinity,
  });
}

function ci(v: [number, number] | null | undefined): string {
  return v === null || v === undefined ? "—" : `${fmtCash(v[0])} to ${fmtCash(v[1])}`;
}

export function ArmsTable({ arms }: { arms: Array<[string, BacktestArmReport]> }) {
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>arm</th>
            <th>sessions</th>
            <th>trades</th>
            <th title="trading fees over the run, whole-position dollars; already out of every net figure, never subtracted again">fees</th>
            <th title="the sum of per-session net (net of fees), whole-position dollars">net</th>
            <th title="mean per-session net">net / session</th>
            <th title="standard deviation of per-session net">sd</th>
            <th title="block-bootstrap 95% interval on the mean per-session net">mean 95% CI</th>
            <th title="mean / sd per session">sharpe</th>
            <th title="probabilistic Sharpe ratio against zero">PSR</th>
            <th title="deflated Sharpe ratio at the trial count">DSR</th>
            <th title="trials the DSR is deflated by">trials</th>
            <th title="sessions the record would need for its Sharpe to be distinguishable from zero">min record</th>
          </tr>
        </thead>
        <tbody>
          {arms.map(([arm, a]) => {
            const s = a.summary;
            return (
              <tr key={arm}>
                <td>
                  {arm}
                  {a.no_exposure === true && (
                    <span
                      className="chip chip-warn"
                      style={{ marginLeft: 6 }}
                      title="every session matched control's to the cent, trade count included: this arm measured nothing"
                    >
                      no exposure
                    </span>
                  )}
                </td>
                <td>{fmtCount(s.sessions)}</td>
                <td>{fmtCount(a.trades ?? null)}</td>
                <td>{a.fees === null || a.fees === undefined ? "—" : fmtCash(-Math.abs(a.fees))}</td>
                <td className={signTone(s.total)}>{fmtCash(s.total)}</td>
                <td className={signTone(s.mean)}>{fmtCash(s.mean)}</td>
                <td>{fmtNum(s.sd)}</td>
                <td>{ci(s.mean_ci95_block_bootstrap)}</td>
                <td>{fmtNum(s.sharpe_per_session, 3)}</td>
                <td>{fmtNum(s.psr, 3)}</td>
                <td>{fmtNum(s.dsr, 3)}</td>
                <td>{fmtCount(s.trials)}</td>
                <td>{fmtNum(s.min_track_record, 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PairingTable({ arms }: { arms: Array<[string, BacktestArmReport]> }) {
  const paired = arms.filter(([arm]) => arm !== "control");
  if (paired.length === 0) return <p className="muted">Only control ran: nothing to pair.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>arm − control</th>
            <th title="sessions both arms traded">sessions</th>
            <th title="sum of per-session net differences, whole-position dollars">net diff</th>
            <th title="mean per-session net difference">diff / session</th>
            <th title="standard deviation of the per-session difference">sd</th>
            <th title="interval on the mean difference">CI</th>
            <th title="one-sided t lower bound on the mean difference">t lower</th>
            <th title="sessions the arm beat control / control beat the arm / ties">sign</th>
            <th title="two-sided sign-test p">sign p</th>
            <th title="sessions only the arm / only control traded">one-sided</th>
          </tr>
        </thead>
        <tbody>
          {paired.map(([arm, a]) => {
            const v = a.vs_control;
            if (v === null || v === undefined) {
              return (
                <tr key={arm}>
                  <td>{arm}</td>
                  <td colSpan={9} className="muted">
                    not paired: a side has no dated sessions
                  </td>
                </tr>
              );
            }
            return (
              <tr key={arm}>
                <td>
                  {arm}
                  {a.no_exposure === true && (
                    <span className="chip chip-warn" style={{ marginLeft: 6 }}>
                      no exposure
                    </span>
                  )}
                </td>
                <td>{fmtCount(v.sessions)}</td>
                <td className={signTone(v.total_diff)}>{fmtCash(v.total_diff)}</td>
                <td className={signTone(v.mean_diff)}>{fmtCash(v.mean_diff)}</td>
                <td>{fmtNum(v.sd_diff)}</td>
                <td title={v.ci_level === undefined ? undefined : `${String(Math.round(v.ci_level * 100))}% interval`}>{ci(v.ci)}</td>
                <td>{v.t_lower === undefined ? "—" : fmtCash(v.t_lower)}</td>
                <td>
                  {v.arm_better === undefined ? "—" : `${String(v.arm_better)} / ${String(v.control_better ?? 0)} / ${String(v.ties ?? 0)}`}
                </td>
                <td>{fmtNum(v.sign_test_p ?? null, 3)}</td>
                <td>
                  {v.arm_only_sessions === undefined ? "—" : `${String(v.arm_only_sessions)} / ${String(v.control_only_sessions ?? 0)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The cards' account from the URL: whole dollars, or null for the run's peak risk. */
function capitalParam(raw: string | null): number | null {
  return raw !== null && isBacktestCapital(raw) ? Number(raw) : null;
}

/** The compounded lens's risk per session from the URL, in percent, or null for the default. */
function riskParam(raw: string | null): number | null {
  return raw !== null && isBacktestRiskPct(raw) ? Number(raw) : null;
}

/** The run cards' choices, read from and written to the URL (`?capital=&risk=&unit=&dd=`). */
export function useRunChoices() {
  const [params, setParams] = useSearchParams();
  const capital = capitalParam(params.get("capital"));
  const risk = riskParam(params.get("risk"));
  const rawUnit = params.get("unit");
  // Max loss is the default and keeps the plain URL; only `stop` is carried.
  const unit = rawUnit !== null && isBacktestRiskUnit(rawUnit) && rawUnit !== "max_loss" ? rawUnit : null;
  // The sizing check's drawdown cap; the package's default keeps the plain URL.
  const dd = riskParam(params.get("dd"));
  const pick = (key: "capital" | "risk" | "unit" | "dd", v: number | string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(key);
    else next.set(key, String(v));
    setParams(next, { replace: true });
  };
  return { params, setParams, capital, risk, unit, dd, pick };
}

/** The run cards: the account, sizing check, lens, drawdowns, health, months, exits and the trade log. */
export function RunCardsSection({ id, r }: { id: string; r: BacktestRunReport }) {
  const { params, setParams, capital, risk, unit, dd, pick } = useRunChoices();
  return (
    <RunCards
      runId={id}
      module={typeof r["module"] === "string" ? r["module"] : undefined}
      cards={r.cards}
      capital={capital}
      onCapital={(c) => pick("capital", c)}
      risk={risk}
      onRisk={(v) => pick("risk", v)}
      unit={unit ?? "max_loss"}
      onUnit={(u) => pick("unit", u === "max_loss" ? null : u)}
      dd={dd}
      onDd={(v) => pick("dd", v)}
      onLensAt={(riskPct, u) => {
        const next = new URLSearchParams(params);
        next.set("risk", String(riskPct));
        if (u === "max_loss") next.delete("unit");
        else next.set("unit", u);
        setParams(next, { replace: true });
      }}
    />
  );
}

/** Sessions the replay refused, and why. */
export function RefusedSessions({ r }: { r: BacktestRunReport }) {
  if (Object.keys(r.refused).length === 0) return <p className="muted">None.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>session</th>
            <th>why</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(r.refused)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([day, why]) => (
              <tr key={day}>
                <td>{day}</td>
                <td className="muted">{why}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

/** Which way an interval sits against zero, in words. */
function against(ci: [number, number] | null | undefined): string {
  if (!ci) return "";
  if (ci[0] > 0) return "the interval is above zero";
  if (ci[1] < 0) return "the interval is below zero";
  return "the interval includes zero";
}

/**
 * The run in plain words, before the tables: what ran, which study it answers and that study's
 * verdict, whether it counts as evidence, and each arm against control. Every figure is the
 * report's; the card only words it.
 */
export function RunPlainEnglish({ r, study }: { r: BacktestRunReport; study?: { row: StudyRow; result: StudyResult } }) {
  const arms = Object.entries(r.arms);
  const control = r.arms["control"];
  return (
    <div className="plain-english">
      <div className="card-subhead">in plain English</div>
      <p>
        A replay of the {r.window} window, {span(r.span)}, at {String(r.cadence)} s ticks, with {arms.length} arm
        {arms.length === 1 ? "" : "s"}.{" "}
        {study ? (
          <>
            It is <Link to={studyHref(study.row.study)}>item {study.row.id}</Link>&apos;s {study.result.window ?? "result"} run: &ldquo;
            {study.row.title}&rdquo;. The study&apos;s verdict here is <strong>{study.result.summary ?? "not written"}</strong>
            {study.row.outcome ? `, and the decision: ${study.row.outcome}` : ""}.
          </>
        ) : (
          "No study in the register names this run."
        )}
      </p>
      {control && (
        <p>
          Control made {fmtCash(control.summary.mean)} a session on average, {fmtCash(control.summary.total)} over the run.
        </p>
      )}
      <ul style={{ margin: "0 0 8px", paddingLeft: "1.1em" }}>
        {arms
          .filter(([arm]) => arm !== "control")
          .map(([arm, a]) => {
            const v = a.vs_control;
            if (a.no_exposure === true) return <li key={arm}>{arm} measured nothing: every session matched control&apos;s to the cent.</li>;
            if (!v || v.mean_diff === null) return <li key={arm}>{arm}: not paired with control.</li>;
            return (
              <li key={arm}>
                {arm}: {fmtCash(v.mean_diff)} a session against control; {against(v.ci)} ({ci(v.ci)}).
              </li>
            );
          })}
      </ul>
    </div>
  );
}

/**
 * The trials the DSR deflates by, and the provisional looks that are not trials. With looks and no
 * trials, a provisional run's DSR is deflated by nothing, so it reads no stricter than its PSR; the
 * line says so rather than let "trials 1" pass for one look.
 */
export function trialsLine(d: BacktestOverview): string {
  const list = (r: Record<string, number> | undefined) =>
    Object.entries(r ?? {})
      .map(([m, n]) => `${m} ${String(n)}`)
      .join(", ");
  const trials = list(d.trials);
  const looks = list(d.provisional_looks);
  const head = `Trials recorded: ${trials || "none"}.`;
  if (!looks) return head;
  return (
    `${head} Provisional looks, distinct specs run before validation unlocked: ${looks}. ` +
    `A look is not a trial, so ${trials ? "a provisional run's DSR is deflated by the trials alone" : "a provisional run's DSR is deflated by nothing and reads no stricter than its PSR"}.`
  );
}

/** A run made before validation unlocked history: shown as such wherever it appears. */
export function ProvisionalChip() {
  return (
    <Link
      to="/backtest/data"
      className="chip chip-warn"
      style={{ marginLeft: 6 }}
      title="made before validation unlocked history: not a trial, not evidence, never shown to the advisor. Open data for why"
    >
      provisional
    </Link>
  );
}
