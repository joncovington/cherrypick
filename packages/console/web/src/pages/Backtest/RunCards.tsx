import type { BacktestArmCards, BacktestRunCards } from "@console/shared";
import { useState } from "react";
import { EquityUnderwater } from "../../components/EquityUnderwater";
import { Tile } from "../../components/performance/Tile";
import { TileGrid } from "../../components/performance/TileGrid";
import { signTone } from "../../lib/bridged";
import { fmtCash, fmtCount, fmtNum } from "../../lib/format";

/**
 * A run's cards (`packages/backtest` `cards.py`, 2026-10-26): the path an arm took, its drawdowns,
 * its calendar, its sessions and trades, how its trades ended, whether its edge moved, and the same
 * dollars against an account. Every figure is the module's; nothing is recomputed here.
 *
 * The account is a lens, not a replay: the runner replays fixed lots, so choosing a capital changes
 * only the percentages, never a dollar. It defaults to the run's own peak session risk; the choice
 * lives in the URL (`capital`), so a link keeps it.
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
}: {
  /** The chosen capital, or null for the run's peak risk. */
  capital: number | null;
  peakRisk: number | null;
  onPick: (capital: number | null) => void;
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
  cards,
  capital,
  onCapital,
}: {
  cards: BacktestRunCards | undefined;
  capital: number | null;
  onCapital: (capital: number | null) => void;
}) {
  const names = Object.keys(cards?.arms ?? {});
  const [picked, setPicked] = useState<string | null>(null);
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
        <AccountPicker capital={capital} peakRisk={c?.peak_risk ?? null} onPick={onCapital} />
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
          <AccountTiles c={c} />
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
        </>
      )}
    </>
  );
}
