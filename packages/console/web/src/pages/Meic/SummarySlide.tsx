/**
 * Trade summary — the denser, gauge-led summary (segmented range control, hero net card,
 * premium-capture gauge, win-rate arcs, leg outcomes, entry-time and weekday bands, the gate's
 * refusal ledger, by-arm rollup, entries table).
 *
 * ⚠️ The PROTOTYPE phase is over: this slide now reads `useMeicSummary` — the one-read endpoint
 * (`/api/meic/summary`, readers/meic.ts "The summary slide's one read"). Every figure arrives
 * computed; the slide derives nothing itself beyond presentation arithmetic (a percentage of a
 * figure the payload already carries, a bar's width from a max it already carries). The former
 * fixture data is gone; where the payload has nothing for a card, the card says so ("no rows in
 * this window") rather than drawing a zero.
 *
 * Money columns are the suite's one layout, computed server-side: gross − fees − settlement =
 * net, signed cash flow, whole-position dollars. Tone is sign-only, never a verdict.
 *
 * The scope is the PAGE's (the manifest header's symbol/arm/era selects) — one scope per MEIC
 * page is the existing rule, and a second control would let the summary disagree with the tabs
 * beside it. The range segments and day arrows drive the reader's window: "day" scopes to the
 * page's resolved session, the rest widen from it, ET-anchored server-side.
 *
 * Styling is scoped to `.ts-root` (see styles.css, the trade-summary block): this slide shipped
 * its own palette (rounded cards, blue selection, mint/salmon tones) borrowed from a reference
 * design, scoped so the rest of the console stayed untouched while the style was judged. It was
 * adopted 2026-10-10 — the tokens moved to `:root` as the suite's and the scope's second palette
 * died — but the `.ts-` class names stay, wired here and pinned by the tests.
 *
 * Charts are hand-rolled FIXED-SIZE SVG (no scaling) so the useMeasure grid rule is not invoked.
 * Colour carries only the sign of a number (pos/neg), never a verdict — the package's tone rule
 * applied to pixels.
 */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMeicSummary } from "../../lib/api";
import type { MeicSummarySlice } from "@console/shared";
import type { TradingMode } from "@console/shared";
import type { MeicScope } from "./MeicTables";
import { StatusLine } from "../../components/summary/ModuleSummary";
import { etToday } from "../../components/table/DateRange";
import { SortTh, sortRows, useSort } from "../../components/DataTable";
import { RANGES, type Range, fmtUsd, sessionLabel, TsCard, TsEmpty, Gauge, NetHero, FaceSwitch, useFace, WinRateArcs as KitWinRateArcs } from "../../components/summary/kit";

export interface MeicSummaryProps {
  /** The page's mode (the PaperLiveBadge toggle) and scope, from the manifest header. Defaults
   *  to every arm and symbol together; the header narrows. One scope per MEIC page. */
  mode: TradingMode;
  scope?: Partial<MeicScope>;
  /** The page's session (the header's day select) — "day" scopes to it. Null = latest session. */
  day: string | null;
  /** Step the page's session back/forward a day (the header's select follows). Null when the
   *  manifest carries no day list, in which case the arrows sit disabled. */
  onDayStep: (delta: -1 | 1) => void;
  /** Whether the arrows can move at all (the manifest has a session list to step through). */
  canStepDay: boolean;
}

/* ---------------------------------------------------------------------------
 * Hero net card
 * ------------------------------------------------------------------------- */

function NetCard({ s }: { s: MeicSummarySlice }) {
  const { hero } = s;
  // Fee drag is fees ÷ premium collected (the suite's definition), so a losing window still has one.
  const drag = hero.credit > 0 ? hero.fees / hero.credit : null;
  const label = s.window.label === "day" && s.window.from !== null ? sessionLabel(s.window.from) : s.window.label;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{label}</span>}>
      <NetHero net={hero.net} trades={hero.trades} shape={hero.shape} />
      <dl className="ts-kv">
        <div><dt>gross captured</dt><dd className="ts-pos">{fmtUsd(hero.gross)}</dd></div>
        <div><dt>fees</dt><dd className="ts-neg">−{fmtUsd(hero.fees).slice(1)}</dd></div>
        <div>
          <dt>settlement</dt>
          {hero.settlement === null ? (
            <dd className="ts-muted" title="a settled trade in this window never recorded its settlement split; it is inside fees">n/r</dd>
          ) : (
            <dd className="ts-neg">−{fmtUsd(hero.settlement).slice(1)}</dd>
          )}
        </div>
        <div><dt>open risk</dt><dd>{fmtUsd(hero.openRisk)}</dd></div>
        <div><dt>fee drag</dt><dd>{drag === null ? "n/r" : `${(drag * 100).toFixed(1)}%`}</dd></div>
      </dl>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Premium-capture gauge — semicircle, needle at the value, arc filling from 0.
 * Angles run 0° (left) to 180° (right); the domain's zero sits at 90°. The needle clamps to the
 * domain; the label shows the reader's UNclamped value, so a big day pins the rail but never
 * misreports its own percentage.
 * ------------------------------------------------------------------------- */

function CaptureGauge({ s }: { s: MeicSummarySlice }) {
  return (
    <Gauge
      title="premium capture"
      rawPct={s.captureRawPct}
      min={-50}
      max={50}
      caption="of premium sold kept after fees"
      dollars={s.hero.credit > 0 ? { amount: s.hero.net, of: s.hero.credit, ofLabel: "premium sold" } : null}
    />
  );
}

function ReturnOnRiskGauge({ s }: { s: MeicSummarySlice }) {
  // `!r` covers a payload from an OLDER server (a field the reader had not grown yet): the card
  // says "nothing" rather than crashing the slide while the build and the process disagree.
  const r = s.returnOnRisk;
  if (!r) return <TsEmpty what="return on risk" />;
  return (
    <Gauge
      title="return on risk"
      rawPct={r.rorRawPct}
      min={-100}
      max={100}
      caption={`of ${r.capitalAtRisk === null ? "—" : fmtUsd(r.capitalAtRisk).replace("+$", "$")} capital at risk · ${r.coverage ?? "n/r"} settled rows`}
      dollars={
        r.capitalAtRisk === null
          ? null
          : { amount: s.hero.net, of: r.capitalAtRisk, ofLabel: "capital at risk", worst: s.hero.shape?.worst ?? null }
      }
    />
  );
}

function WinRateArcs({ s }: { s: MeicSummarySlice }) {
  const { hero } = s;
  return <KitWinRateArcs daysWon={hero.daysWon} days={hero.daysWon + hero.daysLost} wins={hero.wins} trades={hero.trades} avgWin={hero.shape?.avgWin ?? null} avgLoss={hero.shape?.avgLoss ?? null} shape={hero.shape ?? null} />;
}

/* ---------------------------------------------------------------------------
 * Leg outcomes — `ic_spread_legs` per side: credit the wing SOLD (per-side net is not a ledger
 * fact), the leg state counts, and the stop cost the wing charged. The header carries the
 * leg-pair identity the module's margin read is built on; the footer line, the fee bar.
 * ------------------------------------------------------------------------- */

function BySide({ s }: { s: MeicSummarySlice }) {
  const known = s.legs.filter((l) => l.credit > 0 || l.expired + l.stopped + l.settled + l.other > 0);
  if (s.pairs.trades === 0 && known.length === 0) return <TsEmpty what="leg outcomes" />;
  const max = Math.max(...s.legs.map((l) => l.credit), 1);
  const p = s.pairs;
  return (
    <TsCard
      title="leg outcomes"
      aside={
        <span className="ts-muted">
          {p.trades} pairs · clean {p.cleanPct === null ? "n/r" : `${p.cleanPct.toFixed(1)}%`} · double-stop{" "}
          {p.doubleStopPct === null ? "n/r" : `${p.doubleStopPct.toFixed(1)}%`}
        </span>
      }
    >
      {s.legs.map((l) => (
        <div className="ts-side" key={l.side}>
          <div className="ts-side-head">
            <span>{l.side}</span>
            <span className="ts-num ts-pos">credit {fmtUsd(l.credit)}</span>
          </div>
          <div className="ts-hbar">
            <div className="ts-hbar-fill ts-pos" style={{ width: `${((l.credit / max) * 100).toFixed(1)}%` }} />
          </div>
          <div className="ts-side-sub">
            {l.expired} expired · {l.stopped} stopped · {l.settled} settled{l.other > 0 ? ` · ${l.other} other` : ""} · stop debits −
            {fmtUsd(l.stopCost).slice(1)}
          </div>
        </div>
      ))}
      <div className="ts-side-sub">
        fee bar {p.breakevenBarPct === null ? "n/r" : `${p.breakevenBarPct.toFixed(1)}%`} of credit — avg fee over avg credit
        sold, the module's breakeven identity
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By entry time — two stacked bar rows on one shared hour axis, because "where we trade most"
 * and "where the trade pays" are different questions the old single bar conflated: total net
 * scales with opportunity, so a busy-but-thin hour out-towered the hour that actually pays.
 *
 *   performance row — avg net per trade, tone by sign, losses as short markers;
 *   evidence row — a slim muted bar per hour, scaled on ITS OWN scale: activity at a glance,
 *   and thin buckets read thin twice (an average without its n is a coincidence, not a finding).
 *
 * Win % and the bucket's total net travel in the tooltip, verbatim — a number, never a tint:
 * colour carries sign, never a verdict, and nobody distinguishes 83% from 87% of green.
 * ------------------------------------------------------------------------- */

/** The MEIC breakdowns' faces: per trade by default (an edge question), total (what the bucket
 *  contributed). A total is the payload's own (entry time) or avg × trades (weekday, wing, delta),
 *  presentation arithmetic over figures the payload carries. */
const PER_FACES = ["per trade", "total"] as const;

function ByEntryTime({ s }: { s: MeicSummarySlice }) {
  const [face, , setFace] = useFace("breakdown:by entry time", PER_FACES);
  const rows = s.entryTime;
  if (rows.length === 0) return <TsEmpty what="by entry time" />;
  const valueOf = (b: (typeof rows)[number]) => (face === "total" ? b.net : (b.avg ?? 0));
  const maxAvg = Math.max(...rows.map((b) => Math.abs(valueOf(b))), 1);
  const maxCount = Math.max(...rows.map((b) => b.count), 1);
  return (
    <TsCard
      title="by entry time"
      aside={
        <span className="ts-muted">
          entries beneath <FaceSwitch faces={PER_FACES} face={face} onFace={setFace} />
        </span>
      }
    >
      <div className="ts-times">
        {rows.map((b) => {
          const avg = valueOf(b);
          const h = `${((Math.abs(avg) / maxAvg) * 100).toFixed(1)}%`;
          const evidence = `${((b.count / maxCount) * 100).toFixed(1)}%`;
          const tip = [
            b.label + (b.offSession ? " (off-session)" : ""),
            `avg ${fmtUsd(b.avg ?? 0)}/trade`,
            `${b.count} ${b.count === 1 ? "entry" : "entries"}`,
            b.winPct === null ? "nothing settled" : `win ${b.winPct.toFixed(1)}%`,
            `total ${fmtUsd(b.net)}`,
          ].join(" · ");
          return (
            <div className="ts-time" key={b.hour} title={tip}>
              <div className="ts-time-area">
                {avg >= 0 ? (
                  <div className="ts-vbar ts-pos" style={{ height: h }} />
                ) : (
                  <div className="ts-vbar ts-neg ts-marker" style={{ height: h }} />
                )}
              </div>
              <div className="ts-evidence">
                <div className="ts-evidence-fill" style={{ width: evidence }} />
                <span className="ts-evidence-n">{b.count}</span>
              </div>
              <div className={`ts-time-label${b.offSession ? " ts-off" : ""}`}>{b.hour}h</div>
            </div>
          );
        })}
      </div>
      <div className="ts-timescale">
        <span className="ts-muted">entries: 0 → {maxCount} across the window's hours</span>
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Entries table — the trade-table standard's columns, cut to what a window summary shows. The
 * rows come from the same reader the session/history tables use, so they cannot disagree.
 * ------------------------------------------------------------------------- */

function EntriesTable({ s, page, onPage }: { s: MeicSummarySlice; page: number; onPage: (p: number) => void }) {
  if (s.entries.rows.length === 0) return <TsEmpty what="entries" />;
  const multi = s.entries.pages > 1;
  // The pager reads the PAYLOAD's resolved page — the reader clamped the URL's ask onto
  // 1..pages, and the card says what it shows. The URL is the request, not the display.
  const p = s.entries.page;
  return (
    <TsCard
      title="entries"
      aside={
        <span className="ts-muted">
          {multi ? (
            <span className="ts-pager">
              <button aria-label="previous page" disabled={p <= 1} onClick={() => onPage(p - 1)} title="newer entries">
                ‹
              </button>
              page {p} of {s.entries.pages}
              <button aria-label="next page" disabled={p >= s.entries.pages} onClick={() => onPage(p + 1)} title="older entries">
                ›
              </button>
            </span>
          ) : null}
          {s.entries.total} filled in window
        </span>
      }
    >
      <div className="table-scroll">
      <table className="ts-table">
        <thead>
          <tr><th>time</th><th>arm</th><th>sym</th><th className="num">credit</th><th>structure</th><th className="num">net</th></tr>
        </thead>
        <tbody>
          {s.entries.rows.map((e, i) => (
            <tr key={`p${s.entries.page}-${e.time ?? "?"}-${e.arm}-${i}`}>
              <td>{e.time ?? "—"}</td>
              <td>{e.arm}</td>
              <td>{e.symbol}</td>
              <td className="num ts-pos">{fmtUsd(e.credit)}</td>
              <td className="ts-muted">{e.structure}</td>
              <td className="num">
                <span className={e.net >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(e.net)}</span>
                {e.provisional && <span className="ts-badge ts-badge-warn">provisional</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Evaluations — the refusal ledger, told twice. A thin funnel bar for the top line (filled share
 * is a proportion of a whole: a bar, not a gauge), then one horizontal bar per
 * `entry_attempts.outcome`, the payload's order (the taxonomy's own order, count-descending
 * inside), each labelled with its dominant `block_detail`. Bars are NEUTRAL — no tone — because
 * a refusal count has no sign: standing aside is a decision, not a loss. The market line carries
 * the ledger's other half, aggregated server-side over exactly the refused rows the outcomes
 * count: is a gate refusing the regime it was built to refuse?
 * ------------------------------------------------------------------------- */

function Evaluations({ s }: { s: MeicSummarySlice }) {
  const ev = s.evaluations;
  if (ev.evaluated === 0) return <TsEmpty what="evaluations" />;
  const maxN = Math.max(...ev.outcomes.map((r) => r.n), 1);
  const passPct = (ev.filled / ev.evaluated) * 100;
  const m = ev.market;
  const marketBits = [
    m.gexNegPct !== null ? `GEX− ${(m.gexNegPct).toFixed(0)}% of refusals` : null,
    m.medianIvRank !== null ? `median IV rank ${m.medianIvRank.toFixed(2)}` : null,
    m.quality !== null ? `session quality ${m.quality}` : null,
  ].filter((x) => x !== null);
  return (
    <TsCard title="evaluations">
      <div className="ts-funnel-head">
        <span>
          <strong className="ts-num">{ev.filled.toLocaleString("en-US")}</strong> filled of{" "}
          <strong className="ts-num">{ev.evaluated.toLocaleString("en-US")}</strong> evaluated
        </span>
        <span className="ts-muted">{ev.refused.toLocaleString("en-US")} refused</span>
      </div>
      <div className="ts-hbar ts-funnel">
        <div className="ts-funnel-fill" style={{ width: `${passPct.toFixed(2)}%` }} />
      </div>
      <div className="ts-refusal-bars">
        {ev.outcomes.map((r) => (
          <div className="ts-refusal" key={r.outcome} title={r.dominantDetail ?? r.outcome}>
            <div className="ts-refusal-head">
              <span>{r.outcome.replace(/_/g, " ")}</span>
              <span className="ts-num">{r.n.toLocaleString("en-US")}</span>
            </div>
            <div className="ts-hbar">
              <div className="ts-hbar-fill ts-neutral" style={{ width: `${((r.n / maxN) * 100).toFixed(1)}%` }} />
            </div>
            {r.dominantDetail && <div className="ts-side-sub">{r.dominantDetail}</div>}
          </div>
        ))}
      </div>
      <p className="ts-muted ts-market">{marketBits.length > 0 ? `at refusal: ${marketBits.join(" · ")}` : "at refusal: no context recorded"}</p>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By weekday — the week as a calendar, not a sorted list: Mon→Fri, every day present, a bar
 * per day of avg net per trade. The axis is a real ZERO baseline, so a danger day DROPS
 * beneath it (tone by sign only — red is where a number is negative, not a verdict the card
 * invented); the trade count and the win % ride as small pills beside the day label, numbers
 * first. Volume is deliberately NOT the bar: a 303-trade Friday would flatten Monday's −$406
 * into invisibility, and volume has its own honest row in the entry-time card.
 * ------------------------------------------------------------------------- */

function ByWeekday({ s, windowLabel }: { s: MeicSummarySlice; windowLabel: string }) {
  const [face, , setFace] = useFace("breakdown:by weekday", PER_FACES);
  const rows = s.weekday;
  if (rows.length === 0) return <TsEmpty what="by weekday" />;
  const valueOf = (r: (typeof rows)[number]) => (face === "total" ? (r.avgNet ?? 0) * r.trades : (r.avgNet ?? 0));
  const max = Math.max(...rows.map((r) => Math.abs(valueOf(r))), 1);
  return (
    <TsCard
      title="by weekday"
      aside={
        <span className="ts-muted">
          {windowLabel} <FaceSwitch faces={PER_FACES} face={face} onFace={setFace} />
        </span>
      }
    >
      <div className="ts-week">
        {rows.map((r) => {
          const v = valueOf(r);
          const h = `${((Math.abs(v) / max) * 100).toFixed(1)}%`;
          const tip = [
            r.bucket,
            `avg ${fmtUsd(r.avgNet ?? 0)}/trade`,
            `total ${fmtUsd((r.avgNet ?? 0) * r.trades)}`,
            `${r.trades} ${r.trades === 1 ? "trade" : "trades"}`,
            r.winPct === null ? "nothing settled" : `win ${r.winPct.toFixed(1)}%`,
          ].join(" · ");
          return (
            <div className="ts-wday" key={r.bucket} title={tip}>
              {/* Fixed half-height in px; the bar is a percentage of it, up or down from zero. */}
              <div className="ts-wday-axis" style={{ height: "90px" }}>
                {v >= 0 ? (
                  <div className="ts-vbar ts-pos" style={{ height: h }} />
                ) : (
                  <div className="ts-vbar ts-neg" style={{ height: h }} />
                )}
              </div>
              <div className="ts-wday-label">
                <span className="ts-wday-day">{r.bucket}</span>
                <span className="ts-wday-pill">{r.trades}</span>
                <span className={`ts-wday-pill ${r.winPct === null ? "" : r.winPct >= 50 ? "ts-wday-win" : "ts-wday-lose"}`}> 
                  {r.winPct === null ? "n/r" : `${Math.round(r.winPct)}%`}
                </span>
              </div>
              <div className="ts-wday-sub">{fmtUsd(v)}</div>
            </div>
          );
        })}
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Signal breakdown — meic/sessions' deep-analytics block in the summary's bar style. The
 * module's own buckets, the module's own measures (trades, win %, avg net), rendered as one
 * horizontal bar per bucket, scaled by trades, with the money measure beside it. Window-scoped
 * (the toolbar's range), said on the card; a too-thin window would make every band a coin toss,
 * which is why the range label travels with the numbers.
 * ------------------------------------------------------------------------- */

function SignalBreakdown({
  title,
  rows,
  windowLabel,
}: {
  title: string;
  rows: MeicSummarySlice["delta"];
  windowLabel: string;
}) {
  const [face, , setFace] = useFace(`breakdown:${title}`, PER_FACES);
  if (rows.length === 0) return <TsEmpty what={title} />;
  const valueOf = (r: (typeof rows)[number]) => (r.avgNet === null ? null : face === "total" ? r.avgNet * r.trades : r.avgNet);
  const max = Math.max(...rows.map((r) => Math.abs(valueOf(r) ?? 0)), 1);
  return (
    <TsCard
      title={title}
      aside={
        <span className="ts-muted">
          {windowLabel} <FaceSwitch faces={PER_FACES} face={face} onFace={setFace} />
        </span>
      }
    >
      {rows.map((r) => {
        const v = valueOf(r);
        return (
          <div className="ts-side" key={r.bucket} title={`avg net per trade ${r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}`}>
            <div className="ts-side-head">
              <span className="ts-num">{r.bucket}</span>
              <span className={`ts-num ${v === null ? "ts-muted" : v >= 0 ? "ts-pos" : "ts-neg"}`}>
                {v === null ? "n/r" : `${fmtUsd(v)}${face === "per trade" ? "/trade" : ""}`}
              </span>
            </div>
            <div className="ts-hbar">
              <div
                className={`ts-hbar-fill ${v === null ? "ts-neutral" : v >= 0 ? "ts-pos" : "ts-neg"}`}
                style={{ width: `${((Math.abs(v ?? 0) / max) * 100).toFixed(1)}%` }}
              />
            </div>
            <div className="ts-side-sub">
              {r.trades} trades · win {r.winPct === null ? "n/r" : `${r.winPct.toFixed(1)}%`}
              {face === "total" && r.avgNet !== null ? ` · avg ${fmtUsd(r.avgNet)}/trade` : ""}
            </div>
          </div>
        );
      })}
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By arm — one row per arm, its own window across the slide's other cards. The hero is one row
 * of this table. The comparison card does NOT narrow when an arm is selected — comparing one arm
 * to itself is meaningless — and says so.
 * ------------------------------------------------------------------------- */

function ByArm({ s }: { s: MeicSummarySlice }) {
  const sort = useSort();
  if (s.arms.length === 0) return <TsEmpty what="by arm" />;
  const arms = sortRows(s.arms, sort, {
    arm: (a) => a.arm,
    sym: (a) => a.symbol,
    eval: (a) => a.evaluated,
    filled: (a) => a.filled,
    net: (a) => a.net,
  });
  return (
    <TsCard title="by arm" aside={<span className="ts-muted">every arm — the comparison card does not narrow</span>}>
      <div className="table-scroll">
      <table className="ts-table">
        <thead>
          <tr>
            <SortTh label="arm" k="arm" sort={sort} />
            <SortTh label="sym" k="sym" sort={sort} />
            <SortTh label="eval" k="eval" sort={sort} className="num" />
            <SortTh label="filled" k="filled" sort={sort} className="num" />
            <th>dominant refusal</th>
            <SortTh label="net" k="net" sort={sort} className="num" />
          </tr>
        </thead>
        <tbody>
          {arms.map((a) => (
            <tr key={`${a.arm}|${a.symbol}`}>
              <td>{a.arm}</td>
              <td>{a.symbol}</td>
              <td className="num">{a.evaluated.toLocaleString("en-US")}</td>
              <td className="num">{a.filled}</td>
              <td className="ts-muted">{a.dominant === null ? "—" : a.dominant.replace(/_/g, " ")}</td>
              <td className="num">
                <span className={a.net >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(a.net)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Toolbar — range segments and day arrows now DRIVE the read: the range picks the reader's
 * window, the arrows step the page's session. The slide owns no preferences: the theme choice
 * moved to the console (a `theme` pref), so the toggle the prototype carried is gone.
 * ------------------------------------------------------------------------- */

function Toolbar({
  range,
  onRange,
  label,
  entriesFilled,
  canStepDay,
  onDayStep,
}: {
  range: Range;
  onRange: (r: Range) => void;
  label: string;
  entriesFilled: number;
  canStepDay: boolean;
  onDayStep: (delta: -1 | 1) => void;
}) {
  return (
    <div className="ts-toolbar">
      <h2 className="ts-title">trade summary</h2>
      <div className="ts-seg" role="tablist" aria-label="range">
        {RANGES.map((r) => (
          <button key={r} role="tab" aria-selected={range === r} className={range === r ? "ts-seg-on" : ""} onClick={() => onRange(r)}>
            {r}
          </button>
        ))}
      </div>
      <div className="ts-daynav">
        <button aria-label="previous day" disabled={!canStepDay} onClick={() => onDayStep(-1)}>
          ‹
        </button>
        <span>
          {label} <span className="ts-muted">· {entriesFilled} entries filled</span>
        </span>
        <button aria-label="next day" disabled={!canStepDay || range !== "day"} onClick={() => onDayStep(1)} title={range !== "day" ? "day stepping applies to the day range" : undefined}>
          ›
        </button>
      </div>
    </div>
  );
}

/**
 * The summary slide. One fetch, one payload; loading and error are stated, never faked — an
 * error page must not look like an empty window, and an empty window must not look like a
 * loading page. The badge is gone: every figure on this slide is now the ledger's own.
 */
export function MeicSummary({ mode, scope, day, onDayStep, canStepDay }: MeicSummaryProps) {
  // The range rides the URL (?range=week): a link to a page is a link to the range it names —
  // the console's own convention for mode — and it makes the window statically verifiable.
  // Anything unstated or unrecognised falls through to the day, like the header's day select.
  const [params, setParams] = useSearchParams();
  const stated = params.get("range");
  const range: Range = (RANGES as readonly string[]).includes(stated ?? "") ? (stated as Range) : "day";
  const setRange = (r: Range) => {
    const next = new URLSearchParams(params);
    if (r === "day") next.delete("range");
    else next.set("range", r);
    // A new window restarts the entries at page 1 — page 3 of the old window means nothing there.
    next.delete("page");
    setParams(next, { replace: true });
  };
  // The page rides the URL beside the range (?page=2), unstated = first, non-numeric falls
  // through to the first the same way an unknown range falls through to the day.
  const page = /^[1-9][0-9]*$/.test(params.get("page") ?? "") ? Number(params.get("page")) : 1;
  const setPage = (p: number) => {
    const next = new URLSearchParams(params);
    if (p <= 1) next.delete("page");
    else next.set("page", String(p));
    setParams(next, { replace: true });
  };
  const q = useMeicSummary({
    mode,
    symbol: scope?.symbol ?? null,
    profile: scope?.profile ?? null,
    era: scope?.era ?? null,
    day,
    range,
    page,
  });
  if (q.isError) {
    return (
      <div className="ts-root">
        <p className="ts-empty">the summary read failed — {q.error instanceof Error ? q.error.message : "unknown error"}</p>
      </div>
    );
  }
  const s = q.data ?? null;
  const loading = q.isLoading;
  const lastTraded =
    s !== null && (day ?? null) === null && s.window.label === "day" && s.window.from !== null && s.window.from < etToday()
      ? s.window.from
      : null;
  const windowLabel =
    (s?.window.label === "day" && s.window.from !== null
      ? `${lastTraded !== null ? "last traded " : ""}${sessionLabel(s.window.from)}`
      : s?.window.label) || "…";
  return (
    <div className="ts-root">
      <Toolbar
        range={range}
        onRange={setRange}
        label={windowLabel}
        entriesFilled={s?.hero.trades ?? 0}
        canStepDay={canStepDay}
        onDayStep={onDayStep}
      />
      {loading && <p className="ts-empty">reading the ledger…</p>}
      {/* No payload at all (a scope with no ledger rows): one statement, not a wall of empty cards
          or — worse — a wall of zeros. */}
      {s === null && <p className="ts-empty">no rows in this window</p>}
      {s !== null && (
      <>
      <StatusLine module="meic" mode={mode} lastTraded={lastTraded} />
      {/* Rows group by the question they answer: money; the trade as built and how it ended
          (leg outcomes + the two geometry cards beside it); the gate's ledger and the arms; when
          we trade (the day's clock beside the era's week); then the ledger itself. */}
      <div className="ts-row ts-row-4">
        <NetCard s={s} />
        <CaptureGauge s={s} />
        <ReturnOnRiskGauge s={s} />
        <WinRateArcs s={s} />
      </div>
      <div className="ts-row ts-row-3">
        <BySide s={s} />
        <SignalBreakdown title="by wing width" rows={s.wing} windowLabel={s.window.label} />
        <SignalBreakdown title="by short-call delta" rows={s.delta} windowLabel={s.window.label} />
      </div>
      <div className="ts-row ts-row-2">
        <Evaluations s={s} />
        <ByArm s={s} />
      </div>
      <div className="ts-row ts-row-2">
        <ByEntryTime s={s} />
        <ByWeekday s={s} windowLabel={s.window.label} />
      </div>
      {/* Entries is full width UNDER the signal blocks — the window's entries are a long row of
          data that a third of a grid cell would cramp. */}
      <div className="ts-row">
        <EntriesTable s={s} page={page} onPage={setPage} />
      </div>
      </>
      )}
    </div>
  );
}
