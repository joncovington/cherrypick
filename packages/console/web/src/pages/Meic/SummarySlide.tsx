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
 * Styling is scoped to `.ts-root` (see styles.css, "Trade summary prototype"): this slide ships
 * its own palette (rounded cards, blue selection, mint/salmon tones) borrowed from a reference
 * design, deliberately at odds with the console's terminal-luxe chrome around it. Scoped so the
 * rest of the console is untouched while the style is under judgement; if adopted, the tokens
 * move outward and the scope is dropped.
 *
 * Charts are hand-rolled FIXED-SIZE SVG (no scaling) so the useMeasure grid rule is not invoked.
 * Colour carries only the sign of a number (pos/neg), never a verdict — the package's tone rule
 * applied to pixels.
 */
import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useMeicSummary } from "../../lib/api";
import type { MeicSummarySlice } from "@console/shared";
import type { TradingMode } from "@console/shared";
import type { MeicScope } from "./MeicTables";

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

const RANGES = ["day", "week", "month", "year", "all"] as const;
type Range = (typeof RANGES)[number];

/* `+$1,580.00` — signed, grouped, whole-position dollars. The summary's own display format for
 * the hero and charts; the entries table keeps the suite's `fmtCash` vocabulary. */
function fmtUsd(v: number): string {
  const sign = v < 0 ? "-" : "+";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/* The window's session label, as the header's day select says it. An ISO date becomes
 * "Wednesday, October 7, 2026"; an open window (week/month/…) shows the reader's own label. */
function sessionLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

/* ---------------------------------------------------------------------------
 * Shared bits
 * ------------------------------------------------------------------------- */

function TsCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="ts-card">
      <header className="ts-card-head">
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

/* A card whose payload has nothing for it. Says why, draws nothing — a zero would read as
 * "traded and broke even", which is a different claim from "no rows in this window". */
function TsEmpty({ what }: { what: string }) {
  return (
    <section className="ts-card">
      <header className="ts-card-head">
        <h3>{what}</h3>
      </header>
      <p className="ts-empty">no rows in this window</p>
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Hero net card
 * ------------------------------------------------------------------------- */

function NetCard({ s }: { s: MeicSummarySlice }) {
  const { hero } = s;
  const drag = hero.gross > 0 ? hero.fees / hero.gross : null;
  const label = s.window.label === "day" && s.window.from !== null ? sessionLabel(s.window.from) : s.window.label;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{label}</span>}>
      <div className={`ts-hero ${hero.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(hero.net)}</div>
      <dl className="ts-kv">
        <div><dt>gross captured</dt><dd className="ts-pos">{fmtUsd(hero.gross)}</dd></div>
        <div><dt>fees</dt><dd className="ts-neg">−{fmtUsd(hero.fees).slice(1)}</dd></div>
        <div><dt>settlement</dt><dd className="ts-neg">−{fmtUsd(hero.settlement).slice(1)}</dd></div>
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

const G = { min: -50, max: 50, cx: 90, cy: 84, r: 70, stroke: 13 };

function gpolar(r: number, theta: number): [number, number] {
  const rad = (theta * Math.PI) / 180;
  return [G.cx - r * Math.cos(rad), G.cy - r * Math.sin(rad)];
}

function gaugeArc(a: number, b: number): string {
  const [x1, y1] = gpolar(G.r, a);
  const [x2, y2] = gpolar(G.r, b);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${G.r} ${G.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function thetaOf(v: number): number {
  const t = Math.min(Math.max(v, G.min), G.max);
  return ((t - G.min) / (G.max - G.min)) * 180;
}

function CaptureGauge({ s }: { s: MeicSummarySlice }) {
  const raw = s.captureRawPct;
  if (raw === null) {
    return <TsEmpty what="premium capture" />;
  }
  const value = s.capturePct ?? 0;
  const zero = thetaOf(0);
  const at = thetaOf(value);
  const [from, to] = value >= 0 ? [zero, at] : [at, zero];
  const [nx, ny] = gpolar(G.r - 18, at);
  return (
    <TsCard title="premium capture">
      <svg className="ts-gauge" viewBox="0 0 180 104" role="img" aria-label={`capture ${raw.toFixed(1)}%`}>
        <path d={gaugeArc(0, 180)} className="ts-track" strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <path d={gaugeArc(from, to)} className={value >= 0 ? "ts-arc-pos" : "ts-arc-neg"} strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <line x1={G.cx} y1={G.cy} x2={nx} y2={ny} className="ts-needle" strokeWidth={2} />
        <circle cx={G.cx} cy={G.cy} r={3.5} className="ts-needle" />
        <text x={8} y={100} className="ts-tick">{G.min}</text>
        <text x={168} y={100} className="ts-tick">{G.max}</text>
      </svg>
      <div className={`ts-gauge-value ${raw >= 0 ? "ts-pos" : "ts-neg"}`}>{raw.toFixed(1)}%</div>
      <p className="ts-muted">of premium sold kept after fees</p>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Win-rate arcs — two concentric radial bars, outer = sessions with positive net, inner = the
 * window's settled trades. Both fractions come from the payload's own counts.
 * ------------------------------------------------------------------------- */

function Ring({ r, fraction, className }: { r: number; fraction: number; className: string }) {
  const c = 2 * Math.PI * r;
  return (
    <>
      <circle cx={70} cy={70} r={r} className="ts-track" strokeWidth={11} fill="none" />
      <circle
        cx={70} cy={70} r={r}
        className={className}
        strokeWidth={11} fill="none" strokeLinecap="round"
        strokeDasharray={`${(fraction * c).toFixed(2)} ${c.toFixed(2)}`}
        transform="rotate(-90 70 70)"
      />
    </>
  );
}

function WinRateArcs({ s }: { s: MeicSummarySlice }) {
  const { hero } = s;
  const tradesOf = hero.trades;
  const daysOf = hero.daysWon + hero.daysLost;
  if (tradesOf === 0 && daysOf === 0) return <TsEmpty what="win rate" />;
  const dayPct = daysOf > 0 ? (hero.daysWon / daysOf) * 100 : 0;
  const tradePct = tradesOf > 0 ? (hero.wins / tradesOf) * 100 : 0;
  return (
    <TsCard title="win rate">
      <div className="ts-rings">
        <svg viewBox="0 0 140 140" role="img" aria-label={`sessions ${dayPct.toFixed(1)}%, trades ${tradePct.toFixed(1)}%`}>
          <Ring r={54} fraction={dayPct / 100} className="ts-ring-days" />
          <Ring r={36} fraction={tradePct / 100} className="ts-ring-entries" />
        </svg>
        <div className="ts-rings-center">
          <span className={dayPct >= 50 ? "ts-pos" : "ts-neg"}>{dayPct.toFixed(0)}%</span>
          <span className="ts-muted">sessions</span>
        </div>
      </div>
      <dl className="ts-legend">
        <div><span className="ts-swatch ts-ring-days" /> sessions {hero.daysWon}/{daysOf}</div>
        <div><span className="ts-swatch ts-ring-entries" /> trades {hero.wins}/{tradesOf}</div>
      </dl>
    </TsCard>
  );
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
            {l.expired} expired · {l.stopped} stopped · {l.settled} settled{l.other > 0 ? ` · ${l.other} other` : ""} · stop cost −
            {l.stopCost.toLocaleString("en-US", { maximumFractionDigits: 2 })}
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

function ByEntryTime({ s }: { s: MeicSummarySlice }) {
  const rows = s.entryTime;
  if (rows.length === 0) return <TsEmpty what="by entry time" />;
  const maxAvg = Math.max(...rows.map((b) => Math.abs(b.avg ?? 0)), 1);
  const maxCount = Math.max(...rows.map((b) => b.count), 1);
  return (
    <TsCard title="by entry time" aside={<span className="ts-muted">per trade · entries beneath</span>}>
      <div className="ts-times">
        {rows.map((b) => {
          const avg = b.avg ?? 0;
          const h = `${((Math.abs(avg) / maxAvg) * 100).toFixed(1)}%`;
          const evidence = `${((b.count / maxCount) * 100).toFixed(1)}%`;
          const tip = [
            b.label + (b.offSession ? " (off-session)" : ""),
            `avg ${fmtUsd(avg)}/trade`,
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
  const rows = s.weekday;
  if (rows.length === 0) return <TsEmpty what="by weekday" />;
  const max = Math.max(...rows.map((r) => Math.abs(r.avgNet ?? 0)), 1);
  return (
    <TsCard title="by weekday" aside={<span className="ts-muted">{windowLabel}</span>}>
      <div className="ts-week">
        {rows.map((r) => {
          const v = r.avgNet ?? 0;
          const h = `${((Math.abs(v) / max) * 100).toFixed(1)}%`;
          const tip = [
            r.bucket,
            `avg ${fmtUsd(v)}/trade`,
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
  if (rows.length === 0) return <TsEmpty what={title} />;
  const max = Math.max(...rows.map((r) => r.trades), 1);
  return (
    <TsCard title={title} aside={<span className="ts-muted">{windowLabel}</span>}>
      {rows.map((r) => (
        <div className="ts-side" key={r.bucket} title={`avg net per trade ${r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}`}>
          <div className="ts-side-head">
            <span className="ts-num">{r.bucket}</span>
            <span className="ts-num ts-muted">{r.trades} trades</span>
          </div>
          <div className="ts-hbar">
            <div
              className={`ts-hbar-fill ${r.avgNet === null ? "ts-neutral" : r.avgNet >= 0 ? "ts-pos" : "ts-neg"}`}
              style={{ width: `${((r.trades / max) * 100).toFixed(1)}%` }}
            />
          </div>
          <div className="ts-side-sub">
            win {r.winPct === null ? "n/r" : `${r.winPct.toFixed(1)}%`} · avg net {r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}/trade
          </div>
        </div>
      ))}
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By arm — one row per arm, its own window across the slide's other cards. The hero is one row
 * of this table. The comparison card does NOT narrow when an arm is selected — comparing one arm
 * to itself is meaningless — and says so.
 * ------------------------------------------------------------------------- */

function ByArm({ s }: { s: MeicSummarySlice }) {
  if (s.arms.length === 0) return <TsEmpty what="by arm" />;
  return (
    <TsCard title="by arm" aside={<span className="ts-muted">every arm — the comparison card does not narrow</span>}>
      <table className="ts-table">
        <thead>
          <tr>
            <th>arm</th>
            <th>sym</th>
            <th className="num">eval</th>
            <th className="num">filled</th>
            <th>dominant refusal</th>
            <th className="num">net</th>
          </tr>
        </thead>
        <tbody>
          {s.arms.map((a) => (
            <tr key={a.arm}>
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
  const windowLabel =
    (s?.window.label === "day" && s.window.from !== null ? sessionLabel(s.window.from) : s?.window.label) || "…";
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
      {/* Rows group by the question they answer: money; the trade as built and how it ended
          (leg outcomes + the two geometry cards beside it); the gate's ledger and the arms; when
          we trade (the day's clock beside the era's week); then the ledger itself. */}
      <div className="ts-row ts-row-3">
        <NetCard s={s} />
        <CaptureGauge s={s} />
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
