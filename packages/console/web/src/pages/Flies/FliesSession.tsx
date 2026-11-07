import { useQuery } from "@tanstack/react-query";
import type { LiveFliesPayload, TradingMode } from "@console/shared";
import { useAttempts } from "../../components/Attempts";
import { useAdvisorModule, STATUS_LABEL } from "../../components/advisor/AdvisorSlide";
import { Bullet } from "../../components/grid/Bullet";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { fmtMoney } from "../../lib/format";
import { fliesQuery, useOpeningRange, type FliesFilter } from "../../lib/api";
import { ForestCard } from "./ForestCard";
import { SpotPathCard } from "./SpotPathCard";
import { FliesLayout, TabLink } from "./FliesFilter";
import { Section } from "../../components/Section";
import type { ReactNode } from "react";

/**
 * The flies session tab: what happened on ONE session, leading with shapes rather than columns.
 *
 * Everything on it is scoped to the session in view. That is the rule the 2026-09-24 rework applied:
 * the regime cross-tab (era-wide, and a duplicate of the regime cuts tab), fee drag (a structural
 * question one session answers badly; the completion tab has it across the era) and the 14-session
 * spark under net today (a second timeframe inside a one-session tile) all left. The opening range
 * shrank to a tile linking to its own tab. What came in is about the day itself: the spot path with
 * entries and completions on it, why entries were refused, which advice was in force, the live
 * pilot, and when and where the session settled.
 *
 * What it does NOT do is as much of the design as what it does. Every tone here is either the sign
 * of a number or a flag the writer already set; nothing on this page decides that a rate is good.
 * Filled-versus-refused is summed here from the per-arm attempt counts the writer emits -- a sum,
 * which the foot line says, not a classification.
 */

export interface SessionAnalytics {
  today: {
    tradeDate: string | null;
    netPnl: number;
    positions: number;
    open: number;
    riskFree: number;
    completionPct: number | null;
    fees: number;
    completed: number;
    maxPossibleLoss: number;
    sessionPeakWorst: { worst: number; at: string } | null;
    dailyPeakRisk?: { peak: number; at: string | null; source: "booked" | "recorded" | "replayed" | null } | null;
    settlement?: { price: number; source: string | null; at: string | null } | null;
    medianCompletionMin?: number | null;
  };
  byArm: Array<{ arm: string; trades: number; net: number }>;
}

/** One session's analytics. Shares the flies page's query key, so the two never fetch it twice. */
export function useSessionAnalytics(mode: TradingMode, filter: FliesFilter, enabled = true) {
  return useQuery<SessionAnalytics>({
    queryKey: ["flies-analytics", mode, filter],
    enabled,
    queryFn: async () => {
      const res = await fetch(`/api/flies/analytics?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`flies analytics: HTTP ${res.status}`);
      return (await res.json()) as SessionAnalytics;
    },
    refetchInterval: 30_000,
  });
}

type Today = SessionAnalytics["today"];

/**
 * One tile of the day's row: a card with its figure, one foot line and a link to the tab that holds
 * the detail. What explains the figure sits behind the card's ?; a caveat that must not be missed is
 * its amber flag. `value` is null for "not recorded", drawn as a dash, never a zero.
 */
export function FliesTile({
  title,
  value,
  tone,
  foot,
  to,
  toLabel,
  help,
  flag,
  children,
}: {
  title: string;
  value: string | null;
  tone?: "pos" | "neg" | "dim";
  foot?: ReactNode;
  to?: string;
  toLabel?: string;
  help?: CardHelpContent;
  flag?: string;
  children?: ReactNode;
}) {
  const toneClass = value === null ? "" : tone === "pos" ? " pnl-pos" : tone === "neg" ? " pnl-neg" : tone === "dim" ? " muted" : "";
  return (
    <Card title={title} className="fl-w3 fl-tile" help={help} flag={flag}>
      <div className={`fl-hero${toneClass}`}>{value ?? "—"}</div>
      {children}
      {foot !== undefined ? <p className="muted fl-state">{foot}</p> : null}
      {to !== undefined ? (
        <div className="pf-mores pf-push">
          <TabLink to={to}>{toLabel ?? title}</TabLink>
        </div>
      ) : null}
    </Card>
  );
}

/** Net on the session, after fees. */
export function NetTodayTile({ today, label = "net today", to = "/flies/history" }: { today: Today | undefined; label?: string; to?: string }) {
  return (
    <FliesTile
      title={label}
      value={today !== undefined ? fmtMoney(today.netPnl) : null}
      tone={today !== undefined && today.netPnl >= 0 ? "pos" : "neg"}
      to={to}
      toLabel="history"
      foot={today === undefined ? "—" : `after fees · ${String(today.positions)} positions · fees ${fmtMoney(today.fees)}`}
    />
  );
}

/** Completed, open and stranded partition `entered`; naming only the open count read "0 still
 *  open of 4 entered" beside 75% on a settled day, which says nothing about where the other 25% went. */
function completionFoot(today: Today | undefined): string {
  if (today === undefined) return "—";
  const stranded = today.positions - today.completed - today.open;
  const parts = [`${String(today.completed)} of ${String(today.positions)} completed`];
  if (today.open > 0) parts.push(`${String(today.open)} still open`);
  if (stranded > 0) parts.push(`${String(stranded)} settled uncompleted`);
  if (today.medianCompletionMin != null) parts.push(`median ${today.medianCompletionMin.toFixed(0)}m to complete`);
  return parts.join(" · ");
}

export function CompletionTile({ today, to = "/flies/completion" }: { today: Today | undefined; to?: string }) {
  return (
    <FliesTile
      title="completion"
      value={today?.completionPct != null ? `${today.completionPct.toFixed(0)}%` : null}
      foot={completionFoot(today)}
      to={to}
      toLabel="completion"
    >
      <Bullet min={0} max={100} value={today?.completionPct ?? null} label="completion rate on this session" />
    </FliesTile>
  );
}

export const PEAK_RISK_HELP: CardHelpContent = {
  title: "Daily peak risk",
  body: (
    <>
      <HelpWarn title="The largest worst case">
        <p>
          The largest worst case the book carried at any moment this session: every open position's own worst case, net of fees and the
          worst-case assignment fee, summed. It stands after settlement until the next session starts.
        </p>
      </HelpWarn>
      <HelpSection title="Where the figure comes from">
        <p>
          Booked: flies' own figure, recorded on the settled book at actual fills. Recorded: the live loop's tick record, the figure its
          buying-power cap reads; it can hold an entry's limit price before a better fill is reconciled, so it can sit a little above the
          settled book's figure. Replayed: rebuilt from the positions' entries and completions. The foot says which.
        </p>
      </HelpSection>
    </>
  ),
};

/** The live pilot's view of one session — the Live page's own payload, asked for that date. */
function useLiveSession(session: string | null) {
  return useQuery<LiveFliesPayload>({
    queryKey: ["live-flies", session],
    enabled: session !== null,
    queryFn: async () => {
      const res = await fetch(`/api/live/flies?session=${encodeURIComponent(session ?? "")}`);
      if (!res.ok) throw new Error(`live: HTTP ${res.status}`);
      return (await res.json()) as LiveFliesPayload;
    },
    refetchInterval: 30_000,
  });
}

export function FliesSession({
  mode,
  filter,
  arm,
  analytics,
  loading,
}: {
  mode: TradingMode;
  filter: FliesFilter;
  arm: string | null;
  analytics: SessionAnalytics | undefined;
  loading: boolean;
}) {
  const attempts = useAttempts("flies", mode, filter.date);
  const a = analytics;
  const today = a?.today;
  const session = today?.tradeDate ?? null;
  const range = useOpeningRange(session);
  const advisor = useAdvisorModule("flies");
  const live = useLiveSession(session);

  const armRows = attempts.data?.arms ?? [];
  const fills = armRows.reduce((n, r) => n + r.fills, 0);
  const tries = armRows.reduce((n, r) => n + r.attempts, 0);
  const refusals = new Map<string, number>();
  for (const r of armRows) {
    for (const [reason, n] of Object.entries(r.refusals)) {
      refusals.set(reason, (refusals.get(reason) ?? 0) + n);
    }
  }
  const refusalRows = [...refusals.entries()].sort((x, y) => y[1] - x[1]);
  const refusedTotal = refusalRows.reduce((n, [, c]) => n + c, 0);

  // The session's peak risk, not the risk still open: "worst case at expiry" summed only open
  // positions, so it fell to $0 the moment a book settled over a day that carried real risk. The
  // peak is the session's own, so it stands through settlement until the next session's first tick
  // moves this page on. What is still open now rides in the foot while anything is.
  const peak = today?.dailyPeakRisk ?? null;
  const openRisk = today !== undefined && today.open > 0 ? Math.abs(today.maxPossibleLoss) : null;

  // Advice in force: the advisor's own enactment rows for this session, one per experiment. Names
  // and params come off the experiment records; nothing here judges whether the advice helped.
  const adv = advisor.data;
  const experiments = [...(adv?.active ?? []), ...(adv?.queued ?? []), ...(adv?.concluded ?? [])];
  const cells = (adv?.sessions ?? []).filter((c) => c.session === session);
  const applied = cells.filter((c) => c.status === "enacted" || c.status === "carried");
  const adviceFoot =
    adv === undefined
      ? "—"
      : cells.length === 0
        ? "no advice recorded for this session"
        : cells
            .map((c) => {
              const e = experiments.find((x) => x.id === c.experimentId);
              const params = e === undefined ? "" : Object.entries(e.params).map(([k, v]) => `${k} ${String(v)}`).join(", ");
              const label = STATUS_LABEL[c.status] ?? c.status;
              return `${e?.name ?? c.experimentId ?? "—"}${params !== "" ? ` (${params})` : ""}: ${label}`;
            })
            .join(" · ");

  // The live pilot on this session. `arm` is the pilot's CURRENT arm record, so it only speaks for
  // this session when its date is this session's.
  const lv = live.data;
  const armedHere = lv !== undefined && lv.arm.armed && lv.arm.date === session;
  const liveTrades = lv?.periods.today.trades ?? 0;
  const liveNet = lv?.periods.today.net ?? null;

  const or = range.data;
  const settle = today?.settlement ?? null;

  return (
    <FliesLayout>
      <Section concept="stand" tag="the day" question="How did today go?">
        <NetTodayTile today={today} />

        <CompletionTile today={today} />

        <FliesTile
          title="daily peak risk"
          value={peak !== null ? fmtMoney(peak.peak) : null}
          tone={peak !== null && peak.peak > 0 ? "neg" : "dim"}
          help={PEAK_RISK_HELP}
          foot={
            today === undefined || peak === null
              ? "nothing entered on this session"
              : [
                  peak.at !== null ? `peak at ${peak.at.slice(11, 16)} ET` : "no position could lose",
                  `${peak.source ?? "source not recorded"}`,
                  openRisk !== null ? `${fmtMoney(openRisk)} still at risk now` : today.open === 0 && today.positions > 0 ? "settled · until the next session" : null,
                  `${String(today.riskFree)} of ${String(today.positions)} risk-free`,
                ]
                  .filter((x): x is string => x !== null)
                  .join(" · ")
          }
        />

        {/* `attempts` counts every tick an arm evaluated, not every order it tried: across twelve
            arms and a full session that runs to five figures, so it belongs in the foot as the
            denominator it is rather than beside the fill count as if the two were comparable. */}
        <FliesTile
          title="entries"
          value={armRows.length === 0 ? null : String(fills)}
          to="/flies/attempts"
          toLabel="attempts"
          foot={armRows.length === 0 ? "no attempts recorded on this session" : `from ${tries.toLocaleString()} evaluations over ${String(armRows.length)} arms`}
        />

        <FliesTile
          title="opening range"
          value={or?.rangePoints != null ? `${or.rangePoints.toFixed(1)} pts` : null}
          to="/flies/openingrange"
          toLabel="opening range"
          foot={
            or === undefined
              ? "—"
              : or.complete && or.low !== null && or.high !== null
                ? `${or.low.toFixed(0)}–${or.high.toFixed(0)} · 10:00 at ${or.last?.toFixed(2) ?? "—"}`
                : (or.reason ?? `${String(or.bucketsPresent)} of ${String(or.bucketsExpected)} buckets`)
          }
        />

        <FliesTile
          title="settlement"
          value={settle !== null ? settle.price.toFixed(2) : null}
          to="/flies/books"
          toLabel="books"
          help={{
            title: "Settlement",
            body: (
              <HelpSection title="The print">
                <p>The print this session's books settled against, and where the module took it from.</p>
              </HelpSection>
            ),
          }}
          foot={
            settle === null
              ? today !== undefined && today.open > 0
                ? "not settled yet"
                : "no settled book on this session"
              : `${settle.source ?? "source not recorded"}${settle.at !== null ? ` · written ${settle.at.slice(11, 16)}` : ""}`
          }
        />

        <FliesTile
          title="advice in force"
          value={adv === undefined ? null : cells.length === 0 ? "none" : `${String(applied.length)} of ${String(cells.length)}`}
          tone="dim"
          to="/flies/advisor"
          toLabel="advisor"
          help={{
            title: "Advice in force",
            body: (
              <HelpSection title="What is counted">
                <p>Experiments whose advice the loop applied on this session, of those the advisor issued.</p>
              </HelpSection>
            ),
          }}
          foot={adviceFoot}
        />

        <FliesTile
          title="live pilot"
          to="/live/flies"
          toLabel="live"
          value={lv === undefined ? null : liveTrades > 0 && liveNet !== null ? fmtMoney(liveNet) : armedHere ? "armed" : "off"}
          tone={liveTrades > 0 && liveNet !== null ? (liveNet >= 0 ? "pos" : "neg") : "dim"}
          foot={
            lv === undefined
              ? "—"
              : `${
                  armedHere ? `armed · ${lv.arm.arm ?? "—"} ${lv.arm.symbol ?? ""}` : liveTrades > 0 ? "traded this session" : "not armed for this session"
                } · ${String(liveTrades)} live trade${liveTrades === 1 ? "" : "s"}`
          }
        />
      </Section>

      <Section concept="dir" tag="the book" question="What does the book look like?">
        {/* The hero. A butterfly only pays if spot walks away from its centre, so the shape of the
            payoff is the session's actual subject and everything above is a number about it. */}
        <ForestCard mode={mode} filter={filter} variant="hero" height={248} to="/flies/forest" />

        <Card
          title="net by arm"
          className="fl-w4"
          help={{
            title: "Net by arm",
            body: (
              <HelpSection title="What is shown">
                <p>Each arm's net on this session, after fees, over the trades that settled.</p>
              </HelpSection>
            ),
          }}
        >
          <DivergingBars
            rows={(a?.byArm ?? []).map((r) => ({
              label: r.arm,
              value: r.net,
              title: `${r.arm}: ${String(r.trades)} trades`,
            }))}
            format={fmtMoney}
            emptyText={loading ? "reading…" : "nothing settled on this session yet"}
          />
          <p className="muted fl-state">{a === undefined ? (loading ? "reading…" : "—") : `${String(a.byArm.reduce((n, r) => n + r.trades, 0))} trades · net of fees`}</p>
          <div className="pf-mores pf-push">
            <TabLink to="/flies/positions">positions</TabLink>
          </div>
        </Card>
      </Section>

      {/* Why the quiet arms were quiet: the gates, or the market. Counts are evaluations refused,
          summed across arms, so one arm refusing every tick for an hour reads as a long bar. */}
      <Section concept="risk" tag="why" question="Why were entries refused?">
        <Card
          title="refusals"
          className="fl-w6"
          help={{
            title: "Refusals",
            body: (
              <HelpSection title="Summed across arms">
                <p>
                  Each gate's refused evaluations on this session, added over the arms, so one arm refusing every tick for an hour reads as
                  a long bar. A refusal is an evaluation that placed nothing, not a missed trade.
                </p>
              </HelpSection>
            ),
          }}
        >
          <DivergingBars
            rows={refusalRows.map(([reason, n]) => ({ label: reason, value: n }))}
            format={(v) => v.toLocaleString()}
            tone="none"
            emptyText={attempts.isLoading ? "reading…" : "nothing refused on this session"}
          />
          <p className="muted fl-state">
            {armRows.length === 0
              ? "no attempts recorded on this session"
              : `${refusedTotal.toLocaleString()} refused evaluations · summed over ${String(armRows.length)} arms`}
          </p>
          <div className="pf-mores pf-push">
            <TabLink to="/flies/attempts">attempts</TabLink>
          </div>
        </Card>

        <SpotPathCard mode={mode} filter={filter} arm={arm} />
      </Section>
    </FliesLayout>
  );
}
