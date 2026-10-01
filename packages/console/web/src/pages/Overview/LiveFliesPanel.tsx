import type { FliesFilter } from "../../lib/api";
import { useFliesMeta } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { Bullet } from "../../components/grid/Bullet";
import { StatTile } from "../../components/grid/GridCard";
import { CompletionTile, NetTodayTile, useSessionAnalytics } from "../Flies/FliesSession";
import { ForestCard } from "../Flies/ForestCard";
import { SpotPathCard } from "../Flies/SpotPathCard";

/**
 * The Overview's right column (2026-10-01): the flies live pilot's latest session, in place of the
 * suite equity curve and the session heatmap. The same cards the flies session tab draws in live
 * mode — net, completion, the payoff at expiry and the spot path — plus one tile that puts the
 * worst case still open beside the session's peak, so a glance says how much is at risk now
 * against the most the day has carried.
 *
 * Every link opens the flies page in live mode (`?mode=live`): that page otherwise opens in the
 * reader's default mode, which would land a click on a live card in the paper book.
 */

const LIVE_FILTER_BASE: Omit<FliesFilter, "date"> = { arm: null, symbol: null, era: null };

function WorstCaseTile({ open, worstNow, peak, peakAt }: { open: number; worstNow: number; peak: number | null; peakAt: string | null }) {
  return (
    <StatTile
      label="worst case · peak"
      value={fmtMoney(worstNow)}
      tone={worstNow > 0 ? "neg" : "dim"}
      to="/live/today"
      toLabel="the live pilot's exposure against its cap"
      span={4}
      title={
        "now: every OPEN position's own worst case at expiry, net of fees and the worst-case assignment fee, summed; " +
        "peak: the largest that sum reached at any moment this session, which stands after settlement until the next session"
      }
      foot={
        peak === null
          ? "nothing entered on this session"
          : `peak ${fmtMoney(peak)}${peakAt !== null ? ` at ${peakAt.slice(11, 16)} ET` : ""} · ${open > 0 ? `${String(open)} open` : "nothing open"}`
      }
    >
      <Bullet min={0} max={Math.max(peak ?? 0, worstNow, 1)} value={worstNow} marker={peak} label="worst case open now against the session's peak" />
    </StatTile>
  );
}

export function LiveFliesPanel() {
  const meta = useFliesMeta("live");
  const session = meta.data?.dates[0] ?? null;
  const filter: FliesFilter = { ...LIVE_FILTER_BASE, date: session };
  const analytics = useSessionAnalytics("live", filter, session !== null);
  const today = analytics.data?.today;

  if (meta.data !== undefined && session === null) {
    return (
      <section className="card">
        <h2>live flies</h2>
        <p className="muted">The live pilot has no sessions on this machine yet.</p>
      </section>
    );
  }

  const worstNow = today !== undefined && today.open > 0 ? Math.abs(today.maxPossibleLoss) : 0;
  const peak = today?.dailyPeakRisk ?? null;

  return (
    <div className="live-flies-panel">
      <div className="grid-12">
        {/* The panel's own heading lives in this tile's label, so the column's first row starts level
            with the desk card beside it rather than a heading's height below. */}
        <NetTodayTile
          today={today}
          label={`live flies P&L${session !== null ? ` · ${session.slice(5)}` : ""}`}
          to="/flies/history?mode=live"
          span={4}
        />
        <CompletionTile today={today} to="/flies/completion?mode=live" span={4} />
        <WorstCaseTile open={today?.open ?? 0} worstNow={worstNow} peak={peak?.peak ?? null} peakAt={peak?.at ?? null} />
        <ForestCard mode="live" filter={filter} variant="hero" height={220} span={12} h={304} to="/flies/forest?mode=live" />
        <SpotPathCard mode="live" filter={filter} arm={null} span={12} h={248} chartHeight={180} to="/flies/timeline?mode=live" />
      </div>
    </div>
  );
}
