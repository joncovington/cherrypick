import { useQuery } from "@tanstack/react-query";
import type { LiveFliesPayload, TradingMode } from "@console/shared";
import { useAttempts } from "../../components/Attempts";
import { useAdvisorModule, STATUS_LABEL } from "../../components/advisor/AdvisorSlide";
import { Bullet } from "../../components/grid/Bullet";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";
import { useOpeningRange, type FliesFilter } from "../../lib/api";
import { ForestCard } from "./ForestCard";
import { SpotPathCard } from "./SpotPathCard";

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
    settlement?: { price: number; source: string | null; at: string | null } | null;
    medianCompletionMin?: number | null;
  };
  byArm: Array<{ arm: string; trades: number; net: number }>;
}

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

  // After the live book settles, "worst case at expiry" would read $0 over a day that carried real
  // risk. The server hands back the session's peak until the next session opens; open positions
  // always win, because while anything is open the live figure is the one that matters.
  const heldPeak = today !== undefined && today.open === 0 ? today.sessionPeakWorst : null;
  const worstValue = heldPeak !== null ? heldPeak.worst : today?.maxPossibleLoss;

  // Completed, open and stranded partition `entered`; naming only the open count read "0 still open
  // of 4 entered" beside 75% on a settled day, which says nothing about where the other 25% went.
  const completionFoot = (() => {
    if (today === undefined) return "—";
    const stranded = today.positions - today.completed - today.open;
    const parts = [`${String(today.completed)} of ${String(today.positions)} completed`];
    if (today.open > 0) parts.push(`${String(today.open)} still open`);
    if (stranded > 0) parts.push(`${String(stranded)} settled uncompleted`);
    if (today.medianCompletionMin != null) parts.push(`median ${today.medianCompletionMin.toFixed(0)}m to complete`);
    return parts.join(" · ");
  })();

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
    <div className="grid-12">
      <StatTile
        label="net today"
        value={today !== undefined ? fmtMoney(today.netPnl) : null}
        tone={today !== undefined && today.netPnl >= 0 ? "pos" : "neg"}
        to="/flies/history"
        toLabel="the session history behind net today"
        foot={today === undefined ? "—" : `after fees · ${String(today.positions)} positions · fees ${fmtMoney(today.fees)}`}
      />

      <StatTile
        label="completion"
        value={today?.completionPct != null ? `${today.completionPct.toFixed(0)}%` : null}
        foot={completionFoot}
        to="/flies/completion"
        toLabel="completion across the era"
      >
        <Bullet
          min={0}
          max={100}
          value={today?.completionPct ?? null}
          label="completion rate on this session"
        />
      </StatTile>

      <StatTile
        label="worst case at expiry"
        value={worstValue !== undefined ? fmtMoney(worstValue) : null}
        tone={worstValue !== undefined && worstValue < 0 ? "neg" : "dim"}
        title={
          heldPeak !== null
            ? "the live book is settled: this is the largest worst case it carried during the session, from the loop's own per-tick exposure — shown until the next session opens"
            : "every open position's own worst case, net of fees and the worst-case assignment fee — zero means nothing open can still lose"
        }
        foot={
          today === undefined
            ? "—"
            : heldPeak !== null
              ? `session peak at ${heldPeak.at.slice(11, 16)} ET · settled · until the next open`
              : `${String(today.riskFree)} of ${String(today.positions)} positions are risk-free`
        }
      />

      {/* `attempts` counts every tick an arm evaluated, not every order it tried — across twelve
          arms and a full session that runs to five figures, so it belongs in the foot as the
          denominator it is rather than beside the fill count as if the two were comparable. */}
      <StatTile
        label="entries"
        value={armRows.length === 0 ? null : String(fills)}
        to="/flies/attempts"
        toLabel="every attempt on this session"
        foot={
          armRows.length === 0
            ? "no attempts recorded on this session"
            : `from ${tries.toLocaleString()} evaluations over ${String(armRows.length)} arms`
        }
      />

      <StatTile
        label="opening range"
        value={or?.rangePoints != null ? `${or.rangePoints.toFixed(1)} pts` : null}
        to="/flies/openingrange"
        toLabel="the opening range, bucket by bucket"
        foot={
          or === undefined
            ? "—"
            : or.complete && or.low !== null && or.high !== null
              ? `${or.low.toFixed(0)}–${or.high.toFixed(0)} · 10:00 at ${or.last?.toFixed(2) ?? "—"}`
              : (or.reason ?? `${String(or.bucketsPresent)} of ${String(or.bucketsExpected)} buckets`)
        }
      />

      <StatTile
        label="settlement"
        value={settle !== null ? settle.price.toFixed(2) : null}
        to="/flies/books"
        toLabel="the books this print settled"
        title="the print this session's books settled against, and where the module took it from"
        foot={
          settle === null
            ? today !== undefined && today.open > 0
              ? "not settled yet"
              : "no settled book on this session"
            : `${settle.source ?? "source not recorded"}${settle.at !== null ? ` · written ${settle.at.slice(11, 16)}` : ""}`
        }
      />

      <StatTile
        label="advice in force"
        value={adv === undefined ? null : cells.length === 0 ? "none" : `${String(applied.length)} of ${String(cells.length)}`}
        tone="dim"
        to="/flies/advisor"
        toLabel="the advisor's experiments on flies"
        title="experiments whose advice the loop applied on this session, of those the advisor issued"
        foot={adviceFoot}
      />

      <StatTile
        label="live pilot"
        to="/live/today"
        toLabel="the live pilot's day: arming, orders and fills"
        value={lv === undefined ? null : liveTrades > 0 && liveNet !== null ? fmtMoney(liveNet) : armedHere ? "armed" : "off"}
        tone={liveTrades > 0 && liveNet !== null ? (liveNet >= 0 ? "pos" : "neg") : "dim"}
        foot={
          lv === undefined
            ? "—"
            : `${
                armedHere
                  ? `armed · ${lv.arm.arm ?? "—"} ${lv.arm.symbol ?? ""}`
                  : liveTrades > 0
                    ? "traded this session"
                    : "not armed for this session"
              } · ${String(liveTrades)} live trade${liveTrades === 1 ? "" : "s"}`
        }
      />

      {/* The hero. A butterfly only pays if spot walks away from its centre, so the shape of the
          payoff is the session's actual subject and everything above is a number about it. */}
      <ForestCard
        mode={mode}
        filter={filter}
        variant="hero"
        height={248}
        to="/flies/books"
      />

      <GridCard
        label="net by arm"
        span={4}
        h={304}
        to="/flies/positions"
        toLabel="the positions behind net by arm"
        foot={
          a === undefined
            ? loading
              ? "reading…"
              : "—"
            : `${String(a.byArm.reduce((n, r) => n + r.trades, 0))} trades · net of fees`
        }
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
      </GridCard>

      <SpotPathCard mode={mode} filter={filter} arm={arm} />


      {/* Why the quiet arms were quiet: the gates, or the market. Counts are evaluations refused,
          summed across arms, so one arm refusing every tick for an hour reads as a long bar. */}
      <GridCard
        label="why entries were refused"
        span={4}
        h={304}
        to="/flies/attempts"
        toLabel="every refusal, arm by arm"
        foot={
          armRows.length === 0
            ? "no attempts recorded on this session"
            : `${refusedTotal.toLocaleString()} refused evaluations · summed over ${String(armRows.length)} arms`
        }
      >
        <DivergingBars
          rows={refusalRows.map(([reason, n]) => ({ label: reason, value: n }))}
          format={(v) => v.toLocaleString()}
          tone="none"
          emptyText={attempts.isLoading ? "reading…" : "nothing refused on this session"}
        />
      </GridCard>
    </div>
  );
}
