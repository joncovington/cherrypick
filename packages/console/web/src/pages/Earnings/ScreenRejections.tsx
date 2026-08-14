import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { Card } from "../../components/DataTable";

/**
 * Why the screen rejected what it rejected — and, more usefully, which threshold is worth moving.
 *
 * This card used to build its own histogram off `scan_log` and got the answer wrong in a way that
 * looked authoritative: it named `bid_ask_spread_too_wide` and `no_weekly_options` as the top
 * problems when neither has ever blocked a candidate ALONE, so moving either changes nothing. Two
 * causes, both structural. `scan_log` holds four incompatible reason vocabularies and pooling them
 * inflates whatever the retired regimes emitted most. And a raw count cannot distinguish a gate
 * doing its job from a gate standing behind five others.
 *
 * The numbers now come from `screen_metrics`, the module's own classifier, so this card and
 * `python -m cherrypick.earnings.screen_report` can no longer disagree.
 */

interface ScreenReason {
  reason: string;
  total: number;
  sole: number;
  strategies: number;
}

interface ScreenPayload {
  ok: boolean;
  error: string | null;
  metrics: {
    profile: string;
    since: string | null;
    funnel: { screened_decisions: number; screened_symbols: number; accepted: number; rejected: number; opened: number };
    reasons: ScreenReason[];
    excluded: Array<{ label: string; rows: number }>;
  } | null;
}

function useScreenMetrics(mode: TradingMode) {
  return useQuery<ScreenPayload>({
    queryKey: ["earnings-screen", mode],
    queryFn: async () => {
      const res = await fetch(`/api/earnings/screen?mode=${mode}`);
      if (!res.ok) throw new Error(`HTTP ${String(res.status)}`);
      return (await res.json()) as ScreenPayload;
    },
    // Classifying the whole scan history is a subprocess; the answer moves only when a scan runs.
    staleTime: 120_000,
    refetchInterval: 120_000,
  });
}

export function ScreenRejections({ mode }: { mode: TradingMode }) {
  const { data, isLoading, isError, dataUpdatedAt } = useScreenMetrics(mode);
  const m = data?.metrics ?? null;
  const reasons = m?.reasons ?? [];
  const max = Math.max(...reasons.map((r) => r.total), 1);

  return (
    <Card title="Why symbols were rejected" collapseKey="earnings-screen" updatedAt={dataUpdatedAt}>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "60%" }} />
      ) : isError || data?.ok !== true || m === null ? (
        <p className="stale-note">{data?.error ?? "Could not read the screening metrics."}</p>
      ) : (
        <>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 0.6rem", lineHeight: 1.5 }}>
            {m.funnel.screened_decisions.toLocaleString()} decisions across {m.funnel.screened_symbols} symbols
            {m.since !== null && <> since {m.since}</>} — {m.funnel.accepted} accepted, {m.funnel.opened} opened.{" "}
            <strong>Sole</strong> is the only column a threshold change can act on: a name failing six gates still
            fails five, so a gate that never fires alone is shadowed by another and tuning it changes nothing.
          </p>

          <table className="data-table">
            <thead>
              <tr>
                <th>reason</th>
                <th style={{ width: "40%" }} />
                <th style={{ textAlign: "right" }}>total</th>
                <th style={{ textAlign: "right" }}>sole</th>
              </tr>
            </thead>
            <tbody>
              {reasons.map((r) => (
                <tr key={r.reason}>
                  <td style={{ whiteSpace: "nowrap" }}>{r.reason}</td>
                  <td>
                    <div style={{ height: 8, background: "var(--row-line)", borderRadius: 2, position: "relative" }}>
                      <div
                        style={{
                          width: `${(r.total / max) * 100}%`,
                          height: "100%",
                          background: "var(--text-muted)",
                          opacity: 0.35,
                          borderRadius: 2,
                        }}
                      />
                      {/* The actionable share, drawn over the total rather than beside it. */}
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          width: `${(r.sole / max) * 100}%`,
                          height: "100%",
                          background: "var(--accent)",
                          opacity: 0.75,
                          borderRadius: 2,
                        }}
                      />
                    </div>
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--num-face)", fontSize: 11.5 }}>{r.total}</td>
                  <td
                    className={r.sole > 0 ? "" : "muted"}
                    style={{ textAlign: "right", fontFamily: "var(--num-face)", fontSize: 11.5 }}
                  >
                    {r.sole}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {(m.excluded ?? []).length > 0 && (
            <p className="muted" style={{ fontSize: 11, margin: "0.5rem 0 0", lineHeight: 1.55 }}>
              Excluded before counting, rather than pooled:{" "}
              {m.excluded.map((e, i) => (
                <span key={e.label}>
                  {i > 0 && "; "}
                  {e.label} ({e.rows.toLocaleString()})
                </span>
              ))}
              . scan_log has carried four incompatible reason vocabularies, and mixing them produces
              confidently wrong numbers.
            </p>
          )}
        </>
      )}
    </Card>
  );
}
