import type { TradingMode } from "@console/shared";
import { useAttempts } from "../../components/Attempts";
import { CrossTabGrid } from "../../components/RegimeCutsTab";
import { Spark } from "../../components/chart/Spark";
import { Bullet } from "../../components/grid/Bullet";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { GridCard, StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";
import { useModulePerformance, useRegimeCuts, type FliesFilter } from "../../lib/api";
import { ForestCard } from "./ForestCard";
import { OpeningRangeCard } from "./OpeningRangeCard";

/**
 * The flies session tab, leading with shapes rather than columns.
 *
 * What it does NOT do is as much of the design as what it does. Every tone here is either the
 * sign of a number or a flag the writer already set; nothing on this page decides that a rate is
 * good. The old table tinted fee drag above 30% red, and that threshold was invented in this
 * package (in three files, with no shared constant) -- it does not come along. `thin` on a regime
 * cell does, because the module stamped it.
 *
 * Two data notes that shaped the layout. `/api/flies/analytics` has no per-session series at all,
 * so the sparkline on "net today" comes from the calibration reading's own `sessionNets`; when
 * that reading is empty there is no spark, rather than a flat line. And filled-versus-refused is
 * summed here from the per-arm attempt counts the writer emits -- a sum, which the foot line
 * says, not a classification.
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
    maxPossibleLoss: number;
  };
  byArm: Array<{ arm: string; trades: number; net: number }>;
  feeDrag: Array<{ arm: string; dragPct: number | null }>;
}

export function FliesSession({
  mode,
  filter,
  arm,
  analytics,
  loading,
  onOpenSheet,
}: {
  mode: TradingMode;
  filter: FliesFilter;
  arm: string | null;
  analytics: SessionAnalytics | undefined;
  loading: boolean;
  onOpenSheet: (which: "books" | "positions" | "history") => void;
}) {
  const perf = useModulePerformance("flies", "current");
  const attempts = useAttempts("flies", mode, filter.date);
  const regime = useRegimeCuts("flies");

  const a = analytics;
  const today = a?.today;

  // The spark's series: the arm in scope if one is selected, else the first book that is not an
  // advised twin (a twin's history is its experiment's, not the module's), else whatever exists.
  const groups = perf.data?.ok === true ? perf.data.groups : [];
  const group =
    groups.find((g) => g.tag === arm) ??
    groups.find((g) => !g.tag.startsWith("advised:")) ??
    groups[0];
  const sessionNets = (group?.sessionNets ?? []).slice(-14).map(([, net]) => net);

  const armRows = attempts.data?.arms ?? [];
  const fills = armRows.reduce((n, r) => n + r.fills, 0);
  const tries = armRows.reduce((n, r) => n + r.attempts, 0);
  const refusals = new Map<string, number>();
  for (const r of armRows) {
    for (const [reason, n] of Object.entries(r.refusals)) {
      refusals.set(reason, (refusals.get(reason) ?? 0) + n);
    }
  }
  const topRefusal = [...refusals.entries()].sort((x, y) => y[1] - x[1])[0];

  const cuts = regime.data?.status === "ok" ? regime.data.cuts : null;
  const crossTab = cuts?.crossTabs[0];
  const regimeStale = regime.data?.status === "ok" ? regime.data.stale : null;

  return (
    <div className="grid-12">
      <StatTile
        label="net today"
        value={today !== undefined ? fmtMoney(today.netPnl) : null}
        tone={today !== undefined && today.netPnl >= 0 ? "pos" : "neg"}
        onExpand={() => onOpenSheet("history")}
        expandLabel="net today"
        foot={
          group === undefined
            ? "no session history in this era yet"
            : `${group.tag} · last ${String(sessionNets.length)} sessions · after fees`
        }
      >
        <Spark
          values={sessionNets}
          mode="cumulative"
          height={22}
          title={`cumulative net over the last ${String(sessionNets.length)} sessions`}
        />
      </StatTile>

      <StatTile
        label="completion"
        value={today?.completionPct != null ? `${today.completionPct.toFixed(0)}%` : null}
        foot={
          today === undefined
            ? "—"
            : `${String(today.open)} still open of ${String(today.positions)} entered`
        }
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
        value={today !== undefined ? fmtMoney(today.maxPossibleLoss) : null}
        tone={today !== undefined && today.maxPossibleLoss < 0 ? "neg" : "dim"}
        title="every open position's own worst case, net of fees and the worst-case assignment fee — zero means nothing open can still lose"
        foot={
          today === undefined
            ? "—"
            : `${String(today.riskFree)} of ${String(today.positions)} positions are risk-free`
        }
      />

      {/* `attempts` counts every tick an arm evaluated, not every order it tried — across twelve
          arms and a full session that runs to five figures, so it belongs in the foot as the
          denominator it is rather than beside the fill count as if the two were comparable. */}
      <StatTile
        label="entries"
        value={armRows.length === 0 ? null : String(fills)}
        foot={
          armRows.length === 0
            ? "no attempts recorded on this session"
            : `from ${tries.toLocaleString()} evaluations over ${String(armRows.length)} arms${
                topRefusal === undefined ? "" : ` · most refused: ${topRefusal[0]}`
              }`
        }
      />

      {/* The hero. A butterfly only pays if spot walks away from its centre, so the shape of the
          payoff is the session's actual subject and everything above is a number about it. */}
      <ForestCard
        mode={mode}
        filter={filter}
        variant="hero"
        height={248}
        onExpand={() => onOpenSheet("books")}
      />

      <GridCard
        label="net by arm"
        span={4}
        h={304}
        onExpand={() => onOpenSheet("positions")}
        expandLabel="net by arm"
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

      <GridCard
        label={crossTab === undefined ? "regime cuts" : `regime — ${crossTab.dims.join(" × ")}`}
        span={8}
        h={304}
        foot={
          cuts === null
            ? "the module writes this artifact nightly"
            : `era since ${cuts.arms[0]?.eraStart ?? "—"}${regimeStale !== null ? " · stale" : ""} · thin below ${String(cuts.thinBelowSessions)} sessions`
        }
      >
        {regime.data === undefined ? (
          <p className="muted">reading…</p>
        ) : regime.data.status === "failed" ? (
          <p className="pnl-neg">
            the regime-cuts artifact could not be read: {regime.data.error}. This is a failure, not
            an empty day.
          </p>
        ) : regime.data.status === "absent" ? (
          <p className="muted">
            no regime-cuts artifact yet — the module writes it nightly at 16:40 ET.
          </p>
        ) : crossTab === undefined ? (
          <p className="muted">the artifact holds no cross-tab for this era.</p>
        ) : (
          <div className="regime-scroll">
            <CrossTabGrid tab={crossTab} thinBelowSessions={cuts?.thinBelowSessions ?? 3} />
          </div>
        )}
      </GridCard>

      <GridCard
        label="fee drag by arm"
        span={4}
        h={304}
        onExpand={() => onOpenSheet("books")}
        expandLabel="fee drag"
        foot="fees against premium collected · no threshold applied"
      >
        <DivergingBars
          rows={(a?.feeDrag ?? []).map((r) => ({ label: r.arm, value: r.dragPct }))}
          format={(v) => `${v.toFixed(1)}%`}
          tone="none"
          emptyText={loading ? "reading…" : "nothing settled on this session yet"}
        />
      </GridCard>

      <div className="span-12">
        <OpeningRangeCard filter={filter} />
      </div>
    </div>
  );
}
