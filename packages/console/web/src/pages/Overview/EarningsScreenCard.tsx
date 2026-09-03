import { useQuery } from "@tanstack/react-query";
import { Card } from "../../components/DataTable";

/**
 * Earnings' screening funnel, compact, on Overview -- the module screens candidate SYMBOLS
 * (846 decisions across 140 symbols is typical), which is a different question from "did an
 * entry attempt fill or get refused" (`readers/desk.ts`'s own comment on why the Entries card's
 * earnings row stays unavailable: scan_log has a documented history of producing wrong numbers
 * when read raw -- `ScreenRejections.tsx`'s own docstring has the incident). This reuses the
 * SAME already-bridged `/api/earnings/screen` endpoint that page's own rejection card reads, so
 * there is no new server-side derivation here, just a compact projection of it. Starts collapsed:
 * supplementary context, not core exposure/entries data, on a page whose whole redesign goal was
 * fitting 1440x900 without a scroll.
 */
interface ScreenPayload {
  ok: boolean;
  error: string | null;
  metrics: {
    since: string | null;
    funnel: { screened_symbols: number; accepted: number; rejected: number; opened: number };
  } | null;
}

function useEarningsScreenFunnel() {
  return useQuery<ScreenPayload>({
    queryKey: ["earnings-screen", "paper", null],
    queryFn: async () => {
      const res = await fetch("/api/earnings/screen?mode=paper");
      if (!res.ok) throw new Error(`HTTP ${String(res.status)}`);
      return (await res.json()) as ScreenPayload;
    },
    staleTime: 120_000,
    refetchInterval: 120_000,
  });
}

export function EarningsScreenCard() {
  const { data, isLoading, dataUpdatedAt } = useEarningsScreenFunnel();
  const funnel = data?.metrics?.funnel;
  return (
    <Card
      title="Earnings screening funnel"
      collapseKey="earnings-screen-funnel"
      defaultCollapsed
      updatedAt={dataUpdatedAt}
    >
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : funnel === undefined ? (
        <p className="muted">screening metrics unavailable</p>
      ) : (
        <>
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">screened</span>
              <span className="stat-value">{funnel.screened_symbols}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">accepted</span>
              <span className="stat-value pnl-pos">{funnel.accepted}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">rejected</span>
              <span className="stat-value pnl-neg">{funnel.rejected}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">opened</span>
              <span className="stat-value">{funnel.opened}</span>
            </div>
          </div>
          <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
            Candidate symbols screened this era, not entry attempts on an already-chosen structure
            — a different question from the Entries card above.{" "}
            <a href="/earnings/detail" className="module-link">earnings' strategy detail</a> has
            the full rejection breakdown by gate.
          </p>
        </>
      )}
    </Card>
  );
}
