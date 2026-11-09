import type {
  ArmAnalysis,
  AnalysisStreaks,
  ArmWindow,
  BucketTable,
  Breakdowns,
  CapitalReading,
  DrawdownEpisode,
  ExecutionReading,
  TradingMode,
} from "@console/shared";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../DataTable";
import { Section } from "../Section";
import { HelpSection, HelpWarn, type CardHelpContent } from "../CardHelp";
import { useOpenParam } from "../../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../../pages/Flies/FliesBoxes";
import { ArmRegime } from "./ArmRegime";
import { fmtMoney, fmtNum, fmtPct } from "../../lib/format";
import { useModuleAnalysis, type PerformanceModuleId } from "../../lib/api";
import { matchesArm } from "../../lib/armTag";
import { Tile } from "../performance/Tile";
import { TileGrid } from "../performance/TileGrid";

/**
 * The strategy-analysis slide: per arm, what the headline reading does not say
 * (docs/strategy-analysis-plan.md). Generic over every module `GET /api/analysis/:module` reads, as
 * `PerformanceSlide` is, and read over the same ledger and window.
 *
 * Presentation only. Every figure comes off `core.analysis` through the bridge; the only arithmetic
 * here is a sign flip to show a drawdown as the loss it is. Tone comes from a number's sign, never
 * a threshold, and a null is an em dash.
 */

function tone(v: number | null): "pos" | "neg" | "dim" {
  if (v === null) return "dim";
  return v >= 0 ? "pos" : "neg";
}

/** A drawdown is a fall, stored as a positive depth: shown as the loss it is. Zero stays zero. */
function loss(depth: number | null): number | null {
  return depth === null ? null : depth === 0 ? 0 : -depth;
}

function lossTone(depth: number | null): "neg" | "dim" {
  return depth === null || depth === 0 ? "dim" : "neg";
}

function run(s: AnalysisStreaks): string {
  return s.current === null ? "—" : `${String(s.current.length)} ${s.current.kind === "win" ? "up" : "down"}`;
}

function span(sessions: number | null, days: number | null): string {
  if (sessions === null) return "—";
  return `${String(sessions)} ses${days === null ? "" : ` · ${String(days)}d`}`;
}

/** Which window this arm was read over, and why (core.analysis.windows). */
export function WindowLine({ w }: { w: ArmWindow }) {
  const b = w.break;
  const what = b === null ? null : [b.kind, b.scope !== null && b.scope !== "*" ? b.scope : null].filter(Boolean).join(" · ");
  return (
    <p className="muted" style={{ fontSize: 11, marginTop: 0 }} title={b?.reason ?? undefined}>
      read from <strong>{w.start ?? "the first trade"}</strong>
      {w.scope === "arm_era" ? ` — its era: ${w.reason}${what ? ` (${what})` : ""}` : ` — the suite window: ${w.reason}`}
    </p>
  );
}

/** A fraction from core (0.31) as a percent (31.0%). */
function pct(v: number | null): string {
  return fmtPct(v === null ? null : v * 100, 1);
}

/**
 * Capital, two ways (core.analysis.capital): the risk the arm was observed to carry at once, and the
 * allocation a person declared for it. Six tiles, so the row never comes out ragged.
 */
export function CapitalTiles({ c }: { c: CapitalReading }) {
  const p = c.peak_risk;
  const a = c.allocation;
  const peakTitle =
    p.withheld !== null
      ? p.withheld
      : `on ${p.session ?? "—"}${p.at !== null ? ` at ${p.at.slice(11, 16)}` : ""}; ` +
        (p.basis === "intraday"
          ? "summed where positions' entry and exit times overlap"
          : p.basis === "mixed"
            ? "positions open across sessions, plus same-session positions where their times overlap"
            : "positions summed over every session they were open") +
        (p.untimed > 0 ? `; ${String(p.untimed)} without times counted as open all session` : "");
  const declared = a.current !== null;
  return (
    <>
      <TileGrid count={6}>
        <Tile
          label="peak risk"
          value={fmtMoney(p.peak)}
          tone="dim"
          n={p.capital_known}
          title={peakTitle}
        />
        <Tile
          label="net / largest peak"
          value={pct(c.return_on_peak_risk)}
          tone={tone(c.return_on_peak_risk)}
          afterFees
          title={
            "the arm's whole net over the most it ever had at risk at once. Not the performance slide's " +
            "\"return on peak risk\", which divides by every session's peak summed and so reads per day of risk"
          }
        />
        <Tile
          label="max drawdown / peak risk"
          value={pct(c.max_drawdown_over_peak_risk)}
          tone={lossTone(c.max_drawdown_over_peak_risk)}
          title="the session max drawdown as a share of the most the arm had at risk at once"
        />
        <Tile
          label="allocation"
          value={declared ? fmtMoney(a.current) : "none declared"}
          tone="dim"
          title={
            declared
              ? a.declared.map((d) => `${fmtMoney(d.capital)} from ${d.from}${d.note ? ` (${d.note})` : ""}`).join("; ")
              : "no allocation is declared for this arm in arm_capital.json"
          }
        />
        <Tile
          label="return on allocation"
          value={pct(a.return)}
          tone={tone(a.return)}
          n={declared ? a.covered : null}
          nUnit="sessions"
          afterFees={declared}
        />
        <Tile
          label="allocation / max drawdown"
          value={a.capital_over_max_drawdown === null ? "—" : `${fmtNum(a.capital_over_max_drawdown, 1)}×`}
          tone="dim"
          title="the latest declared allocation over the dollar max drawdown of the sessions it covers"
        />
      </TileGrid>
      {p.withheld !== null && (
        <p className="muted" style={{ fontSize: 11 }}>
          peak risk unknown — {p.withheld}
        </p>
      )}
      {p.withheld === null && p.capital_known < p.trades && (
        <p className="muted" style={{ fontSize: 11 }}>
          capital recorded on {String(p.capital_known)} of {String(p.trades)} trades: the peak is over those, and the
          ratios beside it are withheld
        </p>
      )}
      {declared && a.uncovered > 0 && (
        <p className="muted" style={{ fontSize: 11 }}>
          {String(a.uncovered)} of {String(a.sessions)} sessions come before the first declared allocation and join
          neither allocation figure
        </p>
      )}
    </>
  );
}

/**
 * The net at multiples of the slippage the module modelled (core.analysis.execution_sensitivity).
 * Over a part of the arm's trades it says so, and the net it starts from is those trades' own.
 */
export function ExecutionTable({ e }: { e: ExecutionReading }) {
  if (e.withheld !== null) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        not computed — {e.withheld}
      </p>
    );
  }
  const where = e.basis === "in_gross" ? "already inside gross (the modelled fill concedes it)" : "charged as its own cost";
  return (
    <>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th title="multiples of the slippage the module modelled; 1× is what was recorded">slippage</th>
              <th>extra cost</th>
              <th>net</th>
              <th>change</th>
            </tr>
          </thead>
          <tbody>
            {e.rows.map((r) => (
              <tr key={r.multiple}>
                <td>
                  {fmtNum(r.multiple, 1)}×{r.multiple === 1 && <span className="muted"> (recorded)</span>}
                </td>
                <td>{r.extra === 0 ? "—" : fmtMoney(r.extra)}</td>
                <td className={`pnl-${r.net >= 0 ? "pos" : "neg"}`}>{fmtMoney(r.net)}</td>
                <td>{r.multiple === 1 ? "—" : pct(r.change)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 11 }}>
        {fmtMoney(e.slippage)} of slippage modelled, {where}.{" "}
        {e.breakeven_multiple !== null
          ? `The net reaches zero at ${fmtNum(e.breakeven_multiple, 1)}× it.`
          : "The net is not positive at the recorded slippage, so there is no breakeven to find."}
        {e.scope === "covered" &&
          ` Over the ${String(e.covered)} of ${String(e.trades)} trades that record slippage: their net, not the arm's.`}
      </p>
    </>
  );
}

const DIMENSION_LABELS: Array<[keyof Omit<Breakdowns, "min_sessions">, string, string]> = [
  ["weekday", "day of week", "day"],
  ["hour", "hour of entry", "hour"],
  ["event", "scheduled releases that day", "release"],
  ["month", "month", "month"],
  ["vix", "VIX at entry", "VIX"],
  ["term", "VIX/VIX3M at entry (contango under 0.97, backwardation from 1.0)", "term"],
];

function BucketRows({ t, head, minSessions }: { t: BucketTable; head: string; minSessions: number }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>{head}</th>
            <th>trades</th>
            <th title={`a row with fewer than ${String(minSessions)} sessions is thin: shown, never a finding`}>
              sessions
            </th>
            <th>net</th>
            <th>win rate</th>
            <th>avg net</th>
            <th>best</th>
            <th>worst</th>
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r) => (
            <tr key={r.bucket} className={r.thin ? "muted" : undefined}>
              <td>
                {r.bucket}
                {r.thin && (
                  <span className="chip" style={{ marginLeft: 6, fontSize: 9 }} title="too few sessions to read">
                    thin
                  </span>
                )}
              </td>
              <td>{String(r.trades)}</td>
              <td>{String(r.sessions)}</td>
              <td className={r.thin ? undefined : `pnl-${r.net >= 0 ? "pos" : "neg"}`}>{fmtMoney(r.net)}</td>
              <td>{pct(r.win_rate)}</td>
              <td>{fmtMoney(r.avg_net)}</td>
              <td>{fmtMoney(r.best)}</td>
              <td>{fmtMoney(r.worst)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Days held, in a half-width card: the five figures that answer "where do the exits leave the
 * money", not the breakdowns' full column set, which would scroll sideways there. A thin row keeps
 * its chip and loses its tone, as in the breakdowns.
 */
function HeldRows({ t }: { t: BucketTable & { min_sessions: number } }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>held</th>
            <th title={`a row with fewer than ${String(t.min_sessions)} sessions is thin: shown, never a finding`}>trades</th>
            <th>win rate</th>
            <th>avg net</th>
            <th>net</th>
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r) => (
            <tr key={r.bucket} className={r.thin ? "muted" : undefined}>
              <td>
                {r.bucket}
                {r.thin && (
                  <span className="chip" style={{ marginLeft: 6, fontSize: 9 }} title={`${String(r.sessions)} sessions: too few to read`}>
                    thin
                  </span>
                )}
              </td>
              <td>{String(r.trades)}</td>
              <td>{pct(r.win_rate)}</td>
              <td>{fmtMoney(r.avg_net)}</td>
              <td className={r.thin ? undefined : `pnl-${r.net >= 0 ? "pos" : "neg"}`}>{fmtMoney(r.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * P&L by something known at entry (core.analysis.buckets), one column set for every dimension.
 * A thin row is dimmed and carries no tone: a colour would read as a finding it cannot support.
 * `only` draws a single dimension (a lightbox tab); the caveat that these are associations, not
 * predictions, is the card's flag and help.
 */
export function BreakdownTables({ b, only }: { b: Breakdowns; only?: keyof Omit<Breakdowns, "min_sessions"> }) {
  return (
    <>
      {DIMENSION_LABELS.filter(([key]) => only === undefined || key === only).map(([key, title, head]) => {
        const t = b[key];
        if (t === undefined) return null; // a regime dimension, absent where the module cuts its own
        if (t.rows.length === 0) {
          return (
            <p key={key} className="muted" style={{ fontSize: 11 }}>
              {title}: nothing to break down
              {t.skipped > 0 ? ` (${String(t.skipped)} trades record no entry time)` : ""}
            </p>
          );
        }
        return (
          <div key={key} style={{ marginBottom: "0.6rem" }}>
            <div className="muted" style={{ fontSize: 11, margin: "0.3rem 0" }}>
              {title}
              {t.skipped > 0 && ` · ${String(t.skipped)} trades not bucketed`}
            </div>
            <BucketRows t={t} head={head} minSessions={b.min_sessions} />
          </div>
        );
      })}
    </>
  );
}

/** The breakdowns card's reading: per dimension, how many buckets, how many are thin, how many trades went unbucketed. */
function BreakdownSummary({ b }: { b: Breakdowns }) {
  const rows = DIMENSION_LABELS.flatMap(([key, , head]) => {
    const t = b[key];
    return t === undefined ? [] : [{ key, head, t }];
  });
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>cut</th>
            <th>buckets</th>
            <th title={`a row with fewer than ${String(b.min_sessions)} sessions is thin: shown, never a finding`}>thin</th>
            <th>not bucketed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, head, t }) => (
            <tr key={key}>
              <td>{head}</td>
              <td>{String(t.rows.length)}</td>
              <td>{String(t.rows.filter((r) => r.thin).length)}</td>
              <td>{t.skipped > 0 ? `${String(t.skipped)} trades` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EpisodesTable({ episodes }: { episodes: DrawdownEpisode[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>high</th>
            <th>low</th>
            <th>new high</th>
            <th>depth</th>
            <th title="sessions traded from the high to the low, and calendar days">falling</th>
            <th title="sessions traded from the low to the new high, and calendar days">recovering</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((e) => (
            <tr key={`${e.peak_session ?? "start"}-${e.low_session}`}>
              <td>{e.peak_session ?? "start"}</td>
              <td>{e.low_session}</td>
              <td className={e.open ? "muted" : undefined}>{e.recovered_session ?? "not yet"}</td>
              <td className="pnl-neg">{fmtMoney(loss(e.depth))}</td>
              <td>{span(e.sessions_falling, e.days_falling)}</td>
              <td>{span(e.sessions_recovering, e.days_recovering)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DurabilityTable({ d }: { d: ArmAnalysis["durability"] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>trades</th>
            <th title="winning trades removed, of the number the share asks for">winners removed</th>
            <th>their net</th>
            <th>net without them</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>all {String(d.trades)}</td>
            <td>—</td>
            <td>—</td>
            <td className={`pnl-${d.net >= 0 ? "pos" : "neg"}`}>{fmtMoney(d.net)}</td>
          </tr>
          {d.without_top.map((r) => (
            <tr key={r.fraction}>
              <td>without the top {String(Math.round(r.fraction * 100))}%</td>
              <td>
                {String(r.removed)}
                {r.removed < r.asked && <span className="muted"> of {String(r.asked)}</span>}
              </td>
              <td>{fmtMoney(r.removed_net)}</td>
              <td className={`pnl-${r.net >= 0 ? "pos" : "neg"}`}>
                {fmtMoney(r.net)}
                {r.sign_flips && (
                  <span className="chip chip-warn" style={{ marginLeft: 6, fontSize: 9 }}>
                    sign flips
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const RUNS_HELP: CardHelpContent = {
  title: "Runs and extremes",
  body: (
    <>
      <HelpSection title="Two kinds of run">
        <p>
          Session runs count days in a row up or down. Trade runs count trade by trade, and where a session holds several
          positions the session runs are the ones that say how many days in a row.
        </p>
      </HelpSection>
      <HelpSection title="Best and worst">
        <p>The single best and worst trade by net, after fees. Hover a tile for its session.</p>
      </HelpSection>
    </>
  ),
};

const DRAWDOWN_HELP: CardHelpContent = {
  title: "Drawdowns and the stress test",
  body: (
    <>
      <HelpSection title="Drawdowns">
        <p>
          A drawdown is a fall below a previous high, by session, shown as the loss it is. The open one, if any, has not
          yet recovered to a new high.
        </p>
      </HelpSection>
      <HelpSection title="The stress test">
        <p>
          The same sessions are reordered, and again moved in blocks so a bad stretch stays together, and the 95th
          percentile drawdown is read off. It is withheld where the arm has too few sessions to resample.
        </p>
      </HelpSection>
    </>
  ),
};

const DURABILITY_HELP: CardHelpContent = {
  title: "Without the biggest winners",
  body: (
    <HelpWarn title="The strategy is not re-run">
      <p>
        Winners are removed from the result, not from the trading. An arm whose entries depend on earlier ones would have
        traded differently without them.
      </p>
    </HelpWarn>
  ),
};

const EXECUTION_HELP: CardHelpContent = {
  title: "Worse fills",
  body: (
    <HelpSection title="What the ladder does">
      <p>
        The net at multiples of the slippage the module modelled; 1× is what was recorded. Over a part of the arm&apos;s
        trades it says so, and the net it starts from is those trades&apos; own.
      </p>
    </HelpSection>
  ),
};

const CAPITAL_HELP: CardHelpContent = {
  title: "Capital",
  body: (
    <HelpSection title="Two ways">
      <p>
        The risk the arm was observed to carry at once, and the allocation a person declared for it in
        arm_capital.json. Hover a tile for how it was made.
      </p>
    </HelpSection>
  ),
};

const BREAKDOWN_HELP: CardHelpContent = {
  title: "Breakdowns",
  body: (
    <>
      <HelpWarn title="Associations, not predictions">
        <p>
          Historical associations over this window&apos;s trades. A row with too few sessions is thin: shown, dimmed, and
          never a finding.
        </p>
      </HelpWarn>
      <HelpSection title="Unknown and unmeasured">
        <p>
          A release day the calendar cannot vouch for reads &quot;unknown&quot;, never &quot;none&quot;. An entry the regime
          recorder did not cover (before 2026-08-24, or an outage) reads &quot;unmeasured&quot;, never a calm day.
        </p>
      </HelpSection>
    </>
  ),
};

const HELD_HELP: CardHelpContent = {
  title: "By days held",
  body: (
    <>
      <HelpWarn title="Set by the exits">
        <p>
          How long a trade is held is decided by how it was closed, so this is not something to choose an entry by. Winners
          closed early sit in the short rows; a loser left to run, or held to expiry, lands in the long ones for that reason
          alone. Read it as a check on the exit rules: a long row that loses most of what the short rows make says the losers
          are being held.
        </p>
      </HelpWarn>
      <HelpSection title="The rows">
        <p>
          Calendar days in ET from entry to exit: same day, 1-6, 7-20 and 21 or more. A trade with no recorded entry or exit
          time is counted as not bucketed. A row with too few sessions is thin: shown, dimmed, and never a finding.
        </p>
      </HelpSection>
    </>
  ),
};

type BreakdownKey = (typeof DIMENSION_LABELS)[number][0];

export function ArmCard({
  module,
  tag,
  a,
  updatedAt,
  regime,
}: {
  module: PerformanceModuleId;
  tag: string;
  a: ArmAnalysis;
  updatedAt: number;
  /** The arm's nightly regime panel (`ArmRegime`), for a module that writes regime cuts. Passed in
   *  rather than fetched here, so the card itself holds no data hook. */
  regime?: ReactNode;
}) {
  const lb = useOpenParam("an");
  const s = a.stress;
  const dd = a.drawdowns;
  const deepest = dd.deepest[0]?.depth ?? null;
  const cov = a.coverage;
  const key = (part: string) => `analysis-${module}-${tag}-${part}`;
  const stressTitle =
    s.withheld !== null
      ? `withheld: ${s.withheld}`
      : `the ${String(Math.round(s.quantile * 100))}th percentile of ${String(s.resamples)} reorderings of the same ${String(s.sessions)} sessions`;

  // Breakdown dimensions that have rows, one lightbox tab each.
  const cuts = a.buckets;
  const cutTabs: Array<{ id: BreakdownKey; label: string }> =
    cuts === undefined ? [] : DIMENSION_LABELS.filter(([k]) => cuts[k] !== undefined).map(([k, title, head]) => ({ id: k, label: head || title }));

  const boxes: FliesBox[] = [];
  if (dd.deepest.length > 0) {
    boxes.push({
      id: `${tag}~drawdowns`,
      kicker: `${tag} › drawdowns`,
      title: "Deepest drawdowns",
      tabs: [{ id: "episodes", label: "episodes" }],
      body: () => (
        <>
          <EpisodesTable episodes={dd.deepest} />
          {dd.longest_closed !== null && (
            <p className="muted" style={{ fontSize: 11 }}>
              longest recovered: {dd.longest_closed.peak_session ?? "start"} → {dd.longest_closed.recovered_session},{" "}
              {String(dd.longest_closed.sessions_falling)} sessions falling and{" "}
              {String(dd.longest_closed.sessions_recovering ?? 0)} recovering
            </p>
          )}
        </>
      ),
    });
  }
  if (cuts !== undefined) {
    boxes.push({
      id: `${tag}~breakdowns`,
      kicker: `${tag} › breakdowns`,
      title: "P&L by what was known at entry",
      tabs: cutTabs,
      body: (tab) => <BreakdownTables b={cuts} only={tab as BreakdownKey} />,
    });
  }

  return (
    <div className="an-arm">
      <div className="an-arm-head">
        <h2 className="an-arm-name">{tag}</h2>
        {a.window !== undefined && <WindowLine w={a.window} />}
      </div>

      <Section concept="hist" tag="the record" question="How has it run?">
        <Card title="runs and extremes" collapseKey={key("runs")} updatedAt={updatedAt} help={RUNS_HELP}>
          <TileGrid count={6}>
            <Tile label="longest up run" value={String(a.session_streaks.longest_win)} tone="dim" nUnit="sessions" n={s.sessions} />
            <Tile label="longest down run" value={String(a.session_streaks.longest_loss)} tone="dim" nUnit="sessions" n={s.sessions} />
            <Tile label="current run" value={run(a.session_streaks)} tone="dim" title="sessions in a row up or down, as the series ends" />
            <Tile
              label="trade runs"
              value={`${String(a.streaks.longest_win)} W / ${String(a.streaks.longest_loss)} L`}
              tone="dim"
              n={cov.trades}
              title="longest runs counted trade by trade; where a session holds several positions the session runs are the ones that say how many days in a row"
            />
            <Tile label="best trade" value={fmtMoney(a.best_trade?.net ?? null)} tone={tone(a.best_trade?.net ?? null)} afterFees title={a.best_trade?.session ?? undefined} />
            <Tile label="worst trade" value={fmtMoney(a.worst_trade?.net ?? null)} tone={tone(a.worst_trade?.net ?? null)} afterFees title={a.worst_trade?.session ?? undefined} />
          </TileGrid>
          {(cov.entry_spot < cov.trades || cov.entry_time < cov.trades) && (
            <p className="muted" style={{ fontSize: 11 }}>
              recorded on {String(cov.entry_time)} of {String(cov.trades)} trades: entry time; {String(cov.entry_spot)}: entry spot;{" "}
              {String(cov.exit_spot)}: exit spot
            </p>
          )}
        </Card>
        {a.held !== undefined && a.held !== null && (
          <Card title="by days held" collapseKey={key("held")} updatedAt={updatedAt} help={HELD_HELP} flag="set by the exits">
            <HeldRows t={a.held} />
            {a.held.skipped > 0 && (
              <p className="muted" style={{ fontSize: 11 }}>
                {String(a.held.skipped)} trades not bucketed: no recorded entry or exit time
              </p>
            )}
          </Card>
        )}
      </Section>

      <Section concept="risk" tag="how bad" question="How deep did it fall, and how much did it risk?">
        <Card title="drawdowns and the stress test" collapseKey={key("drawdowns")} updatedAt={updatedAt} help={DRAWDOWN_HELP}>
          <TileGrid count={6}>
            <Tile label="drawdowns" value={String(dd.count)} tone="dim" title="falls below a previous high, by session" />
            <Tile label="deepest (session)" value={fmtMoney(loss(deepest))} tone={lossTone(deepest)} afterFees />
            <Tile
              label="open drawdown"
              value={dd.open === null ? "none" : fmtMoney(loss(dd.open.depth))}
              tone={dd.open === null ? "dim" : lossTone(dd.open.depth)}
              title={dd.open !== null ? `below the ${dd.open.peak_session ?? "start"} high since ${dd.open.low_session}'s low` : undefined}
            />
            <Tile label="max drawdown (session)" value={fmtMoney(loss(s.historical))} tone={lossTone(s.historical)} n={s.sessions} nUnit="sessions" />
            <Tile label="reordered, 95th pct" value={fmtMoney(loss(s.reordered))} tone={lossTone(s.reordered)} title={stressTitle} />
            <Tile
              label={`blocks of ${String(s.block)}, 95th pct`}
              value={fmtMoney(loss(s.grouped))}
              tone={lossTone(s.grouped)}
              title={s.withheld !== null ? stressTitle : `${stressTitle}, moved in blocks of ${String(s.block)} so a bad stretch stays together`}
            />
          </TileGrid>
          {s.withheld !== null && (
            <p className="muted" style={{ fontSize: 11 }}>
              stress test withheld — {s.withheld}
            </p>
          )}
          {dd.deepest.length > 0 && (
            <div className="pf-mores pf-push">
              <BoxOpen lb={lb} id={`${tag}~drawdowns`}>deepest drawdowns</BoxOpen>
            </div>
          )}
        </Card>

        {a.capital !== undefined && (
          <Card title="capital" collapseKey={key("capital")} updatedAt={updatedAt} help={CAPITAL_HELP}>
            <CapitalTiles c={a.capital} />
          </Card>
        )}
      </Section>

      <Section concept="edge" tag="edge" question="Does the edge survive the biggest winners and worse fills?">
        <Card title="without the biggest winners" collapseKey={key("durability")} updatedAt={updatedAt} help={DURABILITY_HELP} flag="not re-run">
          <DurabilityTable d={a.durability} />
        </Card>

        {a.execution !== undefined && (
          <Card title="worse fills" collapseKey={key("execution")} updatedAt={updatedAt} help={EXECUTION_HELP}>
            <ExecutionTable e={a.execution} />
          </Card>
        )}
      </Section>

      {(cuts !== undefined || regime !== undefined) && (
        <Section concept="dir" tag="what shaped it" question="What was known at entry when it made or lost money?">
          {cuts !== undefined && (
            <Card title="breakdowns" collapseKey={key("breakdowns")} updatedAt={updatedAt} help={BREAKDOWN_HELP} flag="not predictions">
              <BreakdownSummary b={cuts} />
              <div className="pf-mores pf-push">
                {cutTabs.map((t, i) => (
                  <BoxOpen key={t.id} lb={lb} id={`${tag}~breakdowns`} tab={i === 0 ? undefined : t.id}>
                    {t.label}
                  </BoxOpen>
                ))}
              </div>
            </Card>
          )}
          {regime}
        </Section>
      )}
      <FliesBoxHost lb={lb} boxes={boxes} />
    </div>
  );
}

/** The arm whose sections show when no arm is picked in the header: the address's, else the first. */
export function pickedArm(tags: readonly string[], asked: string | null): string | null {
  if (tags.length === 0) return null;
  return asked !== null && tags.includes(asked) ? asked : tags[0]!;
}

/**
 * Every arm in one table, with what says how it went: sessions, trades, net, the deepest fall and the
 * worst trade. A row's name picks the arm whose sections show below; the pick is in the address
 * (`?an-arm=`), so it links. Shown only when no arm is picked in the header and there is more than one.
 */
export function ArmPicker({
  groups,
  picked,
  onPick,
}: {
  groups: Array<{ tag: string; analysis: ArmAnalysis }>;
  picked: string | null;
  onPick: (tag: string) => void;
}) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>arm</th>
            <th className="num">sessions</th>
            <th className="num">trades</th>
            <th className="num">net</th>
            <th className="num">deepest fall</th>
            <th className="num">worst trade</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const a = g.analysis;
            const net = typeof a.reading["net_pnl"] === "number" ? (a.reading["net_pnl"] as number) : null;
            const deepest = loss(a.drawdowns?.deepest[0]?.depth ?? null);
            const worst = a.worst_trade?.net ?? null;
            return (
              <tr key={g.tag}>
                <td>
                  <button type="button" className="chip" aria-pressed={g.tag === picked} onClick={() => onPick(g.tag)} title={`Show ${g.tag}'s analysis`}>
                    {g.tag}
                  </button>
                </td>
                <td className="num">{String(a.stress.sessions)}</td>
                <td className="num">{a.coverage === undefined ? "—" : String(a.coverage.trades)}</td>
                <td className={`num ${net === null ? "" : net >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(net)}</td>
                <td className={`num ${deepest === null ? "" : "pnl-neg"}`}>{fmtMoney(deepest)}</td>
                <td className={`num ${worst === null ? "" : worst >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(worst)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const PICKER_HELP: CardHelpContent = {
  title: "Which arm",
  body: (
    <HelpSection title="Picking an arm">
      <p>
        Every arm read, each over its own window. Pick a name to show that arm&apos;s runs, drawdowns, durability and what was known at entry
        below. Picking an arm in the header narrows the page to it instead.
      </p>
    </HelpSection>
  ),
};

/** The reading card's ?: which window each arm was read over, in the slide's own words. */
function readingHelp(era: { from: string | null; note: string | null; moduleEra: string | null }): CardHelpContent {
  return {
    title: "What is being read",
    body: (
      <>
        {era.note !== null && (
          <HelpSection title="The window">
            <p>
              Each arm is read over its own era where this module&apos;s measurement breaks name arms (its latest break, else
              the module&apos;s latest book-wide one), and otherwise over the suite&apos;s evidence window (
              {era.from ?? "all time"}) — {era.note}
              {era.moduleEra !== null && <> — and to this module&apos;s own <strong>{era.moduleEra}</strong> era</>}. Every arm
              says which. The performance tab reads the suite window, so the two can differ.
            </p>
          </HelpSection>
        )}
        <HelpSection title="What the cards add">
          <p>
            Per arm, what the headline reading does not say: runs, drawdowns, how the result holds up without its biggest winners
            and under worse fills, and what was known at entry.
          </p>
        </HelpSection>
      </>
    ),
  };
}

export function AnalysisSlide({
  module,
  mode = "paper",
  arm = null,
}: {
  module: PerformanceModuleId;
  mode?: TradingMode;
  /** The page's arm picker (flies, MEIC): one arm's card, or every arm's with an index above. */
  arm?: string | null;
}) {
  const { data, isLoading, dataUpdatedAt } = useModuleAnalysis(module, "current", mode);
  const [params, setParams] = useSearchParams();
  const pickArm = (tag: string) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.set("an-arm", tag);
        return next;
      },
      { replace: true },
    );

  if (isLoading || data === undefined) {
    return (
      <div className="cards cards-wide">
        <Card title="analysis">
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        </Card>
      </div>
    );
  }
  if (!data.ok) {
    return (
      <div className="cards cards-wide">
        <Card title="analysis" isError>
          <p className="muted">{data.error ?? "strategy analysis unavailable"}</p>
        </Card>
      </div>
    );
  }
  // The arm picker, applied to what was read; an advised book stays with its arm.
  const matching = data.groups.filter((g) => matchesArm(g.tag, arm));
  // With no arm picked in the header and several read, one arm's sections show at a time.
  const picking = arm === null && matching.length > 1;
  const picked = picking ? pickedArm(matching.map((g) => g.tag), params.get("an-arm")) : null;
  const shown = picking ? matching.filter((g) => g.tag === picked) : matching;
  return (
    <div className="pf-layout an-layout">
      <Section concept="stand" tag="the reading" question="What is being read?">
        <Card
          title="this reading"
          collapseKey={`analysis-${module}-reading`}
          updatedAt={dataUpdatedAt}
          help={readingHelp(data.era)}
          flag={data.allocations.error !== null ? "allocations not applied" : undefined}
        >
          {data.mode === "live" && (
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>
              the <strong>live</strong> ledger — a measurement of the live book
            </p>
          )}
          {data.allocations.error !== null && (
            <p className="muted" style={{ fontSize: 12 }}>
              <span className="chip chip-warn" style={{ marginRight: 6 }}>
                allocations not applied
              </span>
              {data.allocations.error}
            </p>
          )}
          {arm !== null && (
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>
              showing <strong>{arm}</strong>, picked in the header
            </p>
          )}
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            each arm over its own era, else the suite window from {data.era.from ?? "all time"}
          </p>
        </Card>
      </Section>
      {picking && (
        <Section concept="stand" tag="the arms" question="Which arm?">
          <Card title={`arms · ${String(matching.length)}`} collapseKey={`analysis-${module}-arms`} updatedAt={dataUpdatedAt} help={PICKER_HELP}>
            <ArmPicker groups={matching} picked={picked} onPick={pickArm} />
          </Card>
        </Section>
      )}
      {shown.length === 0 ? (
        <Card title="analysis" updatedAt={dataUpdatedAt}>
          <p className="muted">
            {arm !== null && data.groups.length > 0 ? `no closed trades for ${arm} in this window` : "no closed trades in this window yet"}
          </p>
        </Card>
      ) : (
        shown.map((g) => (
          <div key={g.tag}>
            <ArmCard
              module={module}
              tag={g.tag}
              a={g.analysis}
              updatedAt={dataUpdatedAt}
              // Only flies and MEIC write regime cuts; every other module's regime waits for entry
              // timestamps on its ledger records (core.regime.regime_at needs a moment to read).
              regime={module === "flies" || module === "meic" ? <ArmRegime module={module} tag={g.tag} /> : undefined}
            />
          </div>
        ))
      )}
    </div>
  );
}
