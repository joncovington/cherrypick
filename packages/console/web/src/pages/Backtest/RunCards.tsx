import type { BacktestArmCards, BacktestArmSettings, BacktestHealthCheck, BacktestRunCards, ManagePayload } from "@console/shared";
import { BACKTEST_DEFAULT_DD_PCT, BACKTEST_DEFAULT_RISK_PCT, isBacktestRiskPct, type BacktestRiskUnit } from "@console/shared";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { Tile } from "../../components/performance/Tile";
import { TileGrid } from "../../components/performance/TileGrid";
import { signTone } from "../../lib/bridged";
import { fmtCash, fmtCount, fmtNum } from "../../lib/format";
import { TradeLog } from "./TradeLog";

/**
 * A run's cards (`packages/backtest` `cards.py`, 2026-10-26): the path an arm took, its drawdowns,
 * its calendar, its sessions and trades, how its trades ended, whether its edge moved, and the same
 * dollars against an account. Every figure is the module's; nothing is recomputed here.
 *
 * The account is a lens, not a replay: the runner replays fixed lots, so choosing a capital changes
 * only the percentages, never a dollar. It defaults to the run's own peak session risk; the choice
 * lives in the URL (`capital`), so a link keeps it. "Current account" offers the net liq manage last
 * evaluated, account masked.
 *
 * The compounded lens (2026-10-31) re-sizes each session to a share of equity at risk (`risk` in
 * the URL, default 2%): labelled a lens, kept beneath the fixed-lot figures, never in a verdict.
 */

export const CAPITAL_PRESETS = [25_000, 50_000, 100_000] as const;

/** An account size, unsigned whole dollars, `$50,000`: a balance, not a cash flow. */
function dollars(v: number): string {
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

/** A signed month cell in whole dollars, compact past a thousand: `+$158.6k`, `-$420`. */
function compact(v: number): string {
  const a = Math.abs(v);
  const body = a >= 1000 ? `${(a / 1000).toFixed(1)}k` : a.toFixed(0);
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}$${body}`;
}

/** A fraction as a percentage, `4.2%`. */
function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function tone(v: number | null | undefined): "pos" | "neg" | "dim" | undefined {
  if (v === null || v === undefined) return "dim";
  return v > 0 ? "pos" : v < 0 ? "neg" : undefined;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function AccountPicker({
  capital,
  peakRisk,
  onPick,
  current = [],
}: {
  /** The chosen capital, or null for the run's peak risk. */
  capital: number | null;
  peakRisk: number | null;
  onPick: (capital: number | null) => void;
  /** Each account's net liq as manage last evaluated it, offered as "current". */
  current?: CurrentAccount[];
}) {
  const [draft, setDraft] = useState("");
  const apply = () => {
    const v = draft.trim().replace(/[$,]/g, "");
    if (/^[1-9][0-9]{0,9}$/.test(v)) onPick(Number(v));
    setDraft("");
  };
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      <span className="muted" style={{ fontSize: 12 }}>
        account
      </span>
      <button
        type="button"
        className={capital === null ? "mode-btn active" : "mode-btn"}
        aria-pressed={capital === null}
        onClick={() => onPick(null)}
        title="the most any one of this arm's sessions held at risk: the money it needed"
      >
        peak risk{peakRisk === null ? " (n/r)" : ` (${dollars(peakRisk)})`}
      </button>
      {current.map((a) => (
        <button
          key={a.account}
          type="button"
          className={capital === a.netLiq ? "mode-btn active" : "mode-btn"}
          aria-pressed={capital === a.netLiq}
          onClick={() => onPick(a.netLiq)}
          title="this account's net liq as manage last evaluated it"
        >
          {`current ${a.account} (${dollars(a.netLiq)})`}
        </button>
      ))}
      {CAPITAL_PRESETS.map((c) => (
        <button
          key={c}
          type="button"
          className={capital === c ? "mode-btn active" : "mode-btn"}
          aria-pressed={capital === c}
          onClick={() => onPick(c)}
        >
          {dollars(c)}
        </button>
      ))}
      <input
        aria-label="account in whole dollars"
        placeholder={capital !== null && !(CAPITAL_PRESETS as readonly number[]).includes(capital) ? dollars(capital) : "other $"}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") apply();
        }}
        onBlur={apply}
        style={{ width: 100 }}
      />
    </div>
  );
}

function AccountTiles({ c }: { c: BacktestArmCards }) {
  const a = c.account;
  if (a === null) {
    return (
      <p className="muted">
        No account: this module records no capital at risk, so there is no peak risk to default to. Choose an account
        above to read the percentages.
      </p>
    );
  }
  return (
    <>
      {a.ruined_on !== null && a.ruined_on !== undefined && (
        <p>
          <span className="chip chip-missing">ruined {a.ruined_on}</span>{" "}
          <span className="muted">
            The equity reached zero on {a.ruined_on}: an account this size would have stopped there, and every
            percentage past it describes money that was not there.
          </span>
        </p>
      )}
      <AccountTileRow a={a} />
    </>
  );
}

function AccountTileRow({ a }: { a: NonNullable<BacktestArmCards["account"]> }) {
  return (
    <TileGrid count={6}>
      <Tile label="account" value={dollars(a.capital)} title="starting capital; fixed lots, never compounded" />
      <Tile label="ending" value={dollars(a.ending)} tone={tone(a.ending - a.capital)} />
      <Tile label="return" value={pct(a.return)} tone={tone(a.return)} afterFees />
      <Tile
        label="annual return"
        value={pct(a.annual_return)}
        tone={tone(a.annual_return)}
        title="simple: return / years spanned, no compounding"
        n={a.years}
        nUnit="years"
      />
      <Tile label="max drawdown" value={pct(a.max_drawdown)} tone={a.max_drawdown > 0 ? "neg" : undefined} title="as a share of peak equity" />
      <Tile
        label="return / drawdown"
        value={fmtNum(a.return_to_drawdown)}
        title="annual return / max drawdown: return per unit of the worst fall"
      />
    </TileGrid>
  );
}

/** The lens's equity as an underwater pair: the running peak is drawing, not a statistic. */
export function lensPoints(path: { session: string; whole: number }[]): { date: string; equity: number; drawdown: number }[] {
  let peak = -Infinity;
  return path.map((p) => {
    peak = Math.max(peak, p.whole);
    return { date: p.session, equity: p.whole, drawdown: peak - p.whole };
  });
}

/** The compounded lens (`cards.compounded`): labelled as what it is, kept apart from the replay's own. */
export function CompoundedLens({ c }: { c: BacktestArmCards }) {
  const k = c.compounded;
  if (k === undefined) return <p className="muted">No compounded lens: the backtest package predates it.</p>;
  if (k === null) return <p className="muted">No compounded lens without an account: choose one above.</p>;
  const w = k.whole;
  const f = k.fractional;
  return (
    <>
      <p className="muted" style={{ fontSize: 12 }}>
        <span className="chip chip-warn">lens</span> Not what the replay ran, and never part of a verdict. Each
        session is re-sized to put {String(k.risk_pct)}% of the equity at that point at risk, one unit being the lots
        the replay held that session,{" "}
        {k.unit === "stop"
          ? "its risk what its stops lose at the trigger with both sides stopped (a gap through the trigger loses more)"
          : "its risk their max loss"}
        . Assumes P&amp;L, fills and fees scale with size.
      </p>
      {w.stopped_on !== null && (
        <p>
          <span className="chip chip-missing">{`stopped ${w.stopped_on}`}</span>{" "}
          <span className="muted">At whole contracts the account reached zero there; nothing after it traded.</span>
        </p>
      )}
      <TileGrid count={6}>
        <Tile label="start" value={dollars(k.start)} title={`risk ${String(k.risk_pct)}% of equity per session`} />
        <Tile label="ending (whole)" value={dollars(w.ending)} tone={tone(w.ending - k.start)} afterFees />
        <Tile label="CAGR (whole)" value={pct(w.cagr)} tone={tone(w.cagr)} n={k.years} nUnit="years" title="compounded annual growth at whole contracts" />
        <Tile
          label="max drawdown (whole)"
          value={pct(w.max_drawdown_pct)}
          tone={w.max_drawdown_pct > 0 ? "neg" : undefined}
          title={`${dollars(w.max_drawdown)} from the peak`}
        />
        <Tile
          label="size (whole)"
          value={w.units_max === 0 ? "none" : w.units_min === w.units_max ? `${String(w.units_max)}×` : `${String(w.units_min)}–${String(w.units_max)}×`}
          title="units of the replay's lots held per session, smallest to largest"
        />
        <Tile
          label="CAGR (fractional)"
          value={pct(f.cagr)}
          tone={tone(f.cagr)}
          title={`the upper bound: ending ${dollars(f.ending)}, max drawdown ${pct(f.max_drawdown_pct)}`}
        />
      </TileGrid>
      {(w.sessions_rounded_to_none > 0 || k.sessions_unsized > 0) && (
        <p className="muted" style={{ fontSize: 12 }}>
          {w.sessions_rounded_to_none > 0 &&
            `${fmtCount(w.sessions_rounded_to_none)} session(s) left out at whole contracts: ${String(k.risk_pct)}% of the equity then was under one unit. `}
          {k.sessions_unsized > 0 && `${fmtCount(k.sessions_unsized)} session(s) traded with no recorded risk and could not be sized.`}
        </p>
      )}
      <EquityUnderwater equity={lensPoints(k.path)} />
    </>
  );
}

/** A percent the package already states as a percent, `5.4%`. */
function pctOf(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;
}

/** The sizing check (`cards.sizing`): how many units the drawdown cap allows on the chosen account, and
 *  what that size risks a session. A check, never a verdict, and never a change to live config. */
export function SizingCheck({
  c,
  module,
  onLensAt,
}: {
  c: BacktestArmCards;
  module?: string;
  onLensAt?: (riskPct: number, unit: BacktestRiskUnit) => void;
}) {
  const s = c.sizing;
  if (s === undefined) return <p className="muted">No sizing check: the backtest package predates it.</p>;
  if (s === null) return <p className="muted">Choose an account above (your current one, or an amount) to size against it.</p>;
  if (s.insufficient) {
    return (
      <p className="muted">
        {`Too little history to size: ${String(s.evidence ?? 0)} traded sessions, the bootstrap needs ${String(s.min_evidence ?? "more")}.`}
      </p>
    );
  }
  const units = s.units ?? 0;
  const stop = module === "meic" && s.risk_pct_stop_tail !== null && s.risk_pct_stop_tail !== undefined;
  const lensRisk = stop ? s.risk_pct_stop_tail : s.risk_pct_max_loss;
  const ddPerUnitPct = s.drawdown_p99_per_unit === undefined ? null : (100 * s.drawdown_p99_per_unit) / s.account;
  return (
    <>
      <p className="muted" style={{ fontSize: 12 }}>
        <span className="chip chip-warn">check</span>
        {` The most units whose 99th-percentile drawdown over ${String(s.horizon_sessions ?? 100)} sessions stays within ${String(s.dd_pct)}% of ${dollars(s.account)}, from this run's own sessions. Provisional where the run is, never a verdict, and never a change to live config.`}
      </p>
      <TileGrid count={6}>
        <Tile
          label="units allowed"
          value={String(units)}
          tone={units === 0 ? "neg" : undefined}
          title="whole units of the lots the replay held each session"
        />
        <Tile
          label="p99 drawdown / unit"
          value={ddPerUnitPct === null ? "—" : `${ddPerUnitPct.toFixed(1)}%`}
          title={`of the account, one unit, over ${String(s.horizon_sessions ?? 100)} sessions`}
        />
        <Tile label="drawdown cap" value={`${String(s.dd_pct)}%`} />
        <Tile
          label="risk / session (max loss)"
          value={pctOf(s.risk_pct_max_loss)}
          title="the allowed units' median session max loss, of the account"
        />
        {module === "meic" && (
          <Tile
            label="risk / session (stop + overshoot)"
            value={pctOf(s.risk_pct_stop_tail)}
            title={
              s.overshoot
                ? `both sides stopped, filled ${((s.overshoot.p95 - 1) * 100).toFixed(0)}% past the trigger (the 95th percentile of ${String(s.overshoot.n)} recorded stops)`
                : "no stop fills recorded"
            }
          />
        )}
        {module === "meic" && s.overshoot && (
          <Tile
            label="stop overshoot p50 / p95"
            value={`${((s.overshoot.p50 - 1) * 100).toFixed(0)}% / ${((s.overshoot.p95 - 1) * 100).toFixed(0)}%`}
            title={`past the trigger; p99 ${((s.overshoot.p99 - 1) * 100).toFixed(0)}%`}
          />
        )}
      </TileGrid>
      {units === 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          One unit&apos;s bad-tail drawdown is larger than the cap on this account: none fits.
        </p>
      )}
      {onLensAt !== undefined && units > 0 && lensRisk !== null && lensRisk !== undefined && lensRisk > 0 && (
        <button type="button" className="btn btn-quiet" onClick={() => onLensAt(Math.min(100, Math.round(lensRisk * 100) / 100), stop ? "stop" : "max_loss")}>
          {`show the compounded lens at this risk (${pctOf(lensRisk)}${stop ? ", stop distance" : ""})`}
        </button>
      )}
    </>
  );
}

/** One masked account's current net liq, from manage's latest evaluation. */
export interface CurrentAccount {
  account: string;
  netLiq: number;
}

/** The accounts manage last evaluated, with their net liq: the "current account" choices. */
function useCurrentAccounts(): CurrentAccount[] {
  const { data } = useQuery<ManagePayload>({
    queryKey: ["manage", "current-accounts"],
    queryFn: async () => {
      const res = await fetch("/api/manage");
      if (!res.ok) throw new Error(`manage: HTTP ${String(res.status)}`);
      return (await res.json()) as ManagePayload;
    },
    staleTime: 5 * 60_000,
  });
  return (data?.accounts ?? [])
    .filter((a): a is typeof a & { account: string; netLiq: number } => a.account !== null && a.netLiq !== null && a.netLiq > 0)
    .map((a) => ({ account: a.account, netLiq: Math.round(a.netLiq) }));
}

const UNIT_LABEL: Record<BacktestRiskUnit, { text: string; title: string }> = {
  max_loss: { text: "max loss", title: "a unit's risk is its positions' max loss: the most it can lose" },
  stop: {
    text: "stop distance",
    title: "a unit's risk is what its stops lose at the trigger, both sides stopped; a gap through the trigger loses more",
  },
};

/** The lens's risk unit, max loss or stop distance. */
export function UnitPicker({ unit, onPick }: { unit: BacktestRiskUnit; onPick: (unit: BacktestRiskUnit) => void }) {
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
      <span className="muted" style={{ fontSize: 12 }}>
        risk unit
      </span>
      {(Object.keys(UNIT_LABEL) as BacktestRiskUnit[]).map((u) => (
        <button
          key={u}
          type="button"
          className={unit === u ? "mode-btn active" : "mode-btn"}
          aria-pressed={unit === u}
          onClick={() => onPick(u)}
          title={UNIT_LABEL[u].title}
        >
          {UNIT_LABEL[u].text}
        </button>
      ))}
    </span>
  );
}

/** The lens's risk per session, in percent: an input applied on Enter or blur. */
export function RiskPicker({ risk, onPick }: { risk: number | null; onPick: (risk: number | null) => void }) {
  return (
    <PercentPicker
      label="lens risk / session"
      aria="compounded lens risk per session, percent"
      value={risk}
      fallback={BACKTEST_DEFAULT_RISK_PCT}
      onPick={onPick}
    />
  );
}

/** A percent above 0 and at most 100, applied on Enter or blur; the package's default reads as null. */
export function PercentPicker({
  label,
  aria,
  value,
  fallback,
  onPick,
}: {
  label: string;
  aria: string;
  value: number | null;
  fallback: number;
  onPick: (v: number | null) => void;
}) {
  const [draft, setDraft] = useState("");
  const apply = () => {
    const v = draft.trim().replace(/%$/, "");
    if (v === "") return;
    if (isBacktestRiskPct(v)) onPick(Number(v) === fallback ? null : Number(v));
    setDraft("");
  };
  return (
    <label className="muted" style={{ fontSize: 12 }}>
      {label}{" "}
      <input
        aria-label={aria}
        placeholder={`${String(value ?? fallback)}%`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") apply();
        }}
        onBlur={apply}
        style={{ width: 56 }}
      />
    </label>
  );
}

/** A setting's value as the strip shows it: absent reads "unset". */
function settingValue(v: unknown): string {
  if (v === null || v === undefined) return "unset";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** How many differing params the strip names before "+N more". */
const STRIP_DIFFS = 6;

/** The arm's settings on one line: cadence, fill model, and what it changes from control. */
export function settingsLine(arm: string, s: BacktestArmSettings | undefined): string | null {
  if (s === undefined) return null;
  if (!s.recorded) return `${arm}: settings not recorded for this run.`;
  const head = [s.cadence === null ? null : `${String(s.cadence)} s ticks`, s.fill_model ? `fill model ${s.fill_model}` : null]
    .filter((x) => x !== null)
    .join(" · ");
  const keys = Object.keys(s.differs);
  let tail = "";
  if (s.base === null) tail = arm === "control" ? " · the base every other arm is read against" : "";
  else if (keys.length === 0) tail = ` · same settings as ${s.base}`;
  else {
    const named = keys
      .slice(0, STRIP_DIFFS)
      .map((k) => `${k} ${settingValue(s.differs[k]!.arm)} (${s.base} ${settingValue(s.differs[k]!.base)})`);
    const more = keys.length > STRIP_DIFFS ? `, +${String(keys.length - STRIP_DIFFS)} more` : "";
    tail = ` · vs ${s.base}: ${named.join(", ")}${more}`;
  }
  return `${arm}: ${head}${tail}`;
}

/** One health check in a sentence: what it measured, against its bar. */
export function healthSentence(h: BacktestHealthCheck): string {
  switch (h.check) {
    case "best_sessions":
      return h.flag
        ? `Rests on its best ${h.k} sessions: without them the rest average ${fmtCash(h.rest_mean)} a session.`
        : `Not carried by a few days: without its best ${h.k} sessions the rest still average ${fmtCash(h.rest_mean)} a session.`;
    case "win_rate":
      return h.flag
        ? `Only ${pct(h.value)} of sessions win (under ${pct(h.bar, 0)}): long losing runs are normal for it.`
        : `${pct(h.value)} of sessions win.`;
    case "losing_run":
      return `Longest losing run: ${fmtCount(h.value)} sessions${h.flag ? ` (${fmtCount(h.bar)} or more)` : ""}.`;
    case "drawdown_length":
      return `Longest drawdown: ${fmtCount(h.value)} sessions${h.recovered ? "" : ", not recovered by the run's end"}${h.flag ? ` (${fmtCount(h.bar)} or more)` : ""}.`;
    case "edge_trend":
      return h.flag ? "The edge is decaying: the run's second half earns less than its first, beyond noise." : `Edge trend: ${h.value}.`;
    case "fee_drag":
      return h.flag ? `Fees take ${pct(h.value)} of the premium collected (${pct(h.bar, 0)} or more).` : `Fees take ${pct(h.value)} of the premium.`;
  }
}

/** The health checks first, flagged ones on top; nothing for a card from an older backtest package. */
function HealthChecks({ c }: { c: BacktestArmCards }) {
  const h = c.health;
  if (h === undefined) return null;
  const ordered = [...h.checks].sort((a, b) => Number(b.flag) - Number(a.flag));
  return (
    <>
      <div className="card-subhead">
        health <span className={h.flags > 0 ? "chip chip-warn" : "chip chip-ok"}>{h.flags > 0 ? `${String(h.flags)} flagged` : "no flags"}</span>
      </div>
      <ul style={{ margin: "4px 0 8px", paddingLeft: 18 }}>
        {ordered.map((x) => (
          <li key={x.check} className={x.flag ? undefined : "muted"}>
            {healthSentence(x)}
          </li>
        ))}
      </ul>
    </>
  );
}

function SessionTiles({ c }: { c: BacktestArmCards }) {
  const s = c.sessions;
  const net = c.equity.length === 0 ? null : c.equity[c.equity.length - 1]!.cum;
  return (
    <TileGrid count={6}>
      <Tile label="net" value={fmtCash(net)} tone={tone(net)} afterFees n={c.equity.length} nUnit="sessions" />
      <Tile label="max drawdown" value={fmtCash(c.max_drawdown === 0 ? 0 : -c.max_drawdown)} tone={c.max_drawdown > 0 ? "neg" : undefined} />
      <Tile label="winning sessions" value={pct(s.win_rate)} n={s.count} nUnit="traded" title="sessions with no trade are neither" />
      <Tile
        label="avg win / avg loss"
        value={`${fmtCash(s.avg_win)} / ${fmtCash(s.avg_loss)}`}
        title={`payoff ratio ${fmtNum(s.payoff_ratio)}`}
      />
      <Tile
        label="best / worst session"
        value={`${fmtCash(s.best)} / ${fmtCash(s.worst)}`}
        title={`${s.best_session ?? "—"} / ${s.worst_session ?? "—"}`}
      />
      <Tile
        label="skew / kurtosis"
        value={`${fmtNum(s.skew ?? null)} / ${fmtNum(s.kurtosis ?? null)}`}
        title="of per-session net; kurtosis is not excess, so a normal reads 3"
      />
    </TileGrid>
  );
}

function PremiumTiles({ c }: { c: BacktestArmCards }) {
  const p = c.premium;
  if (p === null) return <p className="muted">No trade carries a single credit, so there is no premium to measure.</p>;
  return (
    <TileGrid count={4}>
      <Tile label="premium collected" value={fmtCash(p.premium)} n={p.n} title="the credits received, whole-position dollars" />
      <Tile
        label="trading fees"
        value={fmtCash(p.fees === 0 ? 0 : -p.fees)}
        title={p.fees_not_recorded ? `settlement apart; ${String(p.fees_not_recorded)} trades without recorded fees` : "settlement apart"}
      />
      <Tile label="fee drag" value={pct(p.fee_drag)} title="trading fees / premium collected" />
      <Tile
        label="capture rate"
        value={pct(p.capture_rate)}
        tone={tone(p.capture_rate)}
        n={p.capture_n}
        afterFees
        title="net / the structures' own ceilings: how much of the credit was kept"
      />
    </TileGrid>
  );
}

function TradeTiles({ c }: { c: BacktestArmCards }) {
  const t = c.trades;
  return (
    <TileGrid count={6}>
      <Tile label="trades" value={fmtCount(t.count)} title={t.not_recorded ? `${String(t.not_recorded)} more with no recorded net` : undefined} />
      <Tile label="winning trades" value={pct(t.win_rate)} n={t.count} />
      <Tile label="avg winner" value={fmtCash(t.avg_win)} tone={tone(t.avg_win)} />
      <Tile label="avg loser" value={fmtCash(t.avg_loss)} tone={tone(t.avg_loss)} />
      <Tile label="payoff ratio" value={fmtNum(t.payoff_ratio)} title="avg winner / avg loser" />
      <Tile label="best / worst trade" value={`${fmtCash(t.best)} / ${fmtCash(t.worst)}`} />
    </TileGrid>
  );
}

function EdgeTrend({ c }: { c: BacktestArmCards }) {
  const e = c.edge_trend;
  if (e === null) return <p className="muted">Too few sessions to compare the run&apos;s halves.</p>;
  const chip = e.verdict === "within noise" ? "chip" : e.verdict === "improving" ? "chip chip-ok" : "chip chip-missing";
  return (
    <p>
      <span className={chip}>{e.verdict ?? "—"}</span>{" "}
      <span className={signTone(e.mean_first)}>{fmtCash(e.mean_first)}</span> →{" "}
      <span className={signTone(e.mean_second)}>{fmtCash(e.mean_second)}</span> per session, split at {e.split}. Change{" "}
      {fmtCash(e.change)}; 95% block-bootstrap interval {e.ci95 === null ? "—" : `${fmtCash(e.ci95[0])} to ${fmtCash(e.ci95[1])}`}
      .
    </p>
  );
}

function DrawdownTable({ c }: { c: BacktestArmCards }) {
  if (c.drawdowns.length === 0) return <p className="muted">No drawdown.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">depth</th>
            <th className="num" title="as a share of peak equity on the account above">depth %</th>
            <th>peak</th>
            <th>trough</th>
            <th className="num">sessions to trough</th>
            <th>recovered</th>
            <th className="num">sessions to recover</th>
          </tr>
        </thead>
        <tbody>
          {c.drawdowns.map((d) => (
            <tr key={`${d.peak ?? "start"}-${d.trough}`}>
              <td className="num pnl-neg">{fmtCash(-d.depth)}</td>
              <td className="num">{pct(d.depth_pct)}</td>
              <td>{d.peak ?? "start"}</td>
              <td>{d.trough}</td>
              <td className="num">{fmtCount(d.sessions_to_trough)}</td>
              <td>{d.recovered ?? <span className="chip chip-warn">not recovered</span>}</td>
              <td className="num">{fmtCount(d.sessions_to_recover)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MonthGrid({ c }: { c: BacktestArmCards }) {
  const { months, years } = c.calendar;
  if (years.length === 0) return <p className="muted">No sessions.</p>;
  const maxAbs = Math.max(...Object.values(months).map((v) => Math.abs(v)), 1);
  const cell = (v: number | undefined) => {
    if (v === undefined) return { background: "var(--track)" };
    const alpha = 0.15 + 0.85 * (Math.abs(v) / maxAbs);
    return { background: `color-mix(in srgb, var(${v >= 0 ? "--pos" : "--neg"}) ${Math.round(alpha * 100)}%, transparent)` };
  };
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>year</th>
            {MONTHS.map((m) => (
              <th key={m}>{m}</th>
            ))}
            <th>net</th>
            <th title="the year's own max drawdown, its path starting from zero">max DD</th>
            <th title="winning sessions / sessions that traded">won</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y.year}>
              <td>{y.year}</td>
              {MONTHS.map((_, i) => {
                const v = months[`${y.year}-${String(i + 1).padStart(2, "0")}`];
                return (
                  <td key={i} style={cell(v)} title={v === undefined ? undefined : fmtCash(v)}>
                    {v === undefined ? "" : compact(v)}
                  </td>
                );
              })}
              <td className={signTone(y.net)}>{fmtCash(y.net)}</td>
              <td>{y.max_drawdown === 0 ? fmtCash(0) : fmtCash(-y.max_drawdown)}</td>
              <td>{`${String(y.winning)} / ${String(y.traded)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExitTable({ c }: { c: BacktestArmCards }) {
  if (c.exits === null) return <p className="muted">This module records no outcome per trade.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>how it ended</th>
            <th>trades</th>
            <th>share</th>
            <th>winning</th>
            <th>avg net</th>
            <th>total net</th>
          </tr>
        </thead>
        <tbody>
          {c.exits.map((e) => (
            <tr key={e.outcome}>
              <td>{e.outcome}</td>
              <td>{fmtCount(e.count)}</td>
              <td>{pct(e.share)}</td>
              <td>{pct(e.win_rate)}</td>
              <td className={signTone(e.avg_net)}>{fmtCash(e.avg_net)}</td>
              <td className={signTone(e.total_net)}>{fmtCash(e.total_net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RunCards({
  runId,
  module,
  cards,
  capital,
  onCapital,
  risk = null,
  onRisk,
  unit = "max_loss",
  onUnit,
  dd = null,
  onDd,
  onLensAt,
}: {
  runId: string;
  /** The run's module: MEIC's trades can open their path. */
  module?: string;
  cards: BacktestRunCards | undefined;
  capital: number | null;
  onCapital: (capital: number | null) => void;
  /** The compounded lens's risk per session in percent, or null for the default. */
  risk?: number | null;
  onRisk?: (risk: number | null) => void;
  /** The lens's risk unit; the stop unit is offered for MEIC only, whose ledger records its stops. */
  unit?: BacktestRiskUnit;
  onUnit?: (unit: BacktestRiskUnit) => void;
  /** The sizing check's drawdown cap in percent, or null for the default. */
  dd?: number | null;
  onDd?: (dd: number | null) => void;
  /** Point the lens at a risk and unit: the sizing check's "show the lens at this size". */
  onLensAt?: (riskPct: number, unit: BacktestRiskUnit) => void;
}) {
  const names = Object.keys(cards?.arms ?? {});
  const [picked, setPicked] = useState<string | null>(null);
  const current = useCurrentAccounts();
  if (cards === undefined) return <p className="muted">No cards: the backtest package predates them.</p>;
  const arm = picked !== null && names.includes(picked) ? picked : names.includes("control") ? "control" : names[0];
  const c = arm === undefined ? undefined : cards.arms?.[arm];
  return (
    <>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", margin: "8px 0" }}>
        {names.length > 0 && (
          <label className="muted" style={{ fontSize: 12 }}>
            arm{" "}
            <select value={arm ?? ""} onChange={(e) => setPicked(e.target.value)}>
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
        {/* Shown even when the cards failed, so a chosen account can always be undone. */}
        <AccountPicker capital={capital} peakRisk={c?.peak_risk ?? null} onPick={onCapital} current={current} />
        {onRisk !== undefined && <RiskPicker risk={risk ?? null} onPick={onRisk} />}
        {onUnit !== undefined && module === "meic" && <UnitPicker unit={unit} onPick={onUnit} />}
        {onDd !== undefined && (
          <PercentPicker
            label="drawdown cap"
            aria="sizing check drawdown cap, percent of the account"
            value={dd}
            fallback={BACKTEST_DEFAULT_DD_PCT}
            onPick={onDd}
          />
        )}
      </div>
      {!cards.ok ? (
        <p className="muted">Cards unavailable: {cards.error ?? "unknown error"}</p>
      ) : c === undefined ? (
        <p className="muted">No arms.</p>
      ) : (
        <>
          <p className="muted" style={{ fontSize: 12 }}>
            Fixed lots, never compounded: the account restates the same dollars as percentages.
            {cards.account?.basis === "chosen"
              ? " One account for every arm, so arms read side by side."
              : " Account: this arm's own peak session risk, so arms differ; pick an amount to compare them."}
          </p>
          {settingsLine(arm!, c.settings) !== null && (
            <p className="mono" style={{ fontSize: 12, margin: "4px 0" }} title={c.settings?.recorded ? JSON.stringify(c.settings.differs) : undefined}>
              {settingsLine(arm!, c.settings)}
            </p>
          )}
          <HealthChecks c={c} />
          <AccountTiles c={c} />
          <div className="card-subhead">sizing check</div>
          <SizingCheck c={c} module={module} onLensAt={onLensAt} />
          <div className="card-subhead">compounded lens</div>
          <CompoundedLens c={c} />
          <SessionTiles c={c} />
          <div className="card-subhead">equity and drawdown</div>
          <EquityUnderwater equity={c.equity.map((p) => ({ date: p.session, equity: p.cum, drawdown: p.drawdown }))} />
          <div className="card-subhead">edge trend</div>
          <EdgeTrend c={c} />
          <div className="card-subhead">deepest drawdowns</div>
          <DrawdownTable c={c} />
          <div className="card-subhead">by month and year</div>
          <MonthGrid c={c} />
          <div className="card-subhead">trades</div>
          <TradeTiles c={c} />
          <PremiumTiles c={c} />
          <div className="card-subhead">how trades ended</div>
          <ExitTable c={c} />
          <div className="card-subhead">trade log</div>
          <TradeLog runId={runId} arm={arm!} pathable={module === "meic"} />
        </>
      )}
    </>
  );
}
