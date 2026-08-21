import { useQuery } from "@tanstack/react-query";
import type { MeicDivergence, TradingMode } from "@console/shared";
import { DataCard, fmtPct } from "../../components/DataTable";

/**
 * Profile divergence: how often MEIC's arms reached DIFFERENT entry decisions on the same tick.
 *
 * The flies page has had this since week one and the reasoning carries over: an experiment can
 * only separate two arms to the extent they actually disagree, and a pair agreeing above 80%
 * cannot answer the question as framed however long it runs. That is a week-one finding, not a
 * month-three one.
 *
 * MEIC's arms differ in GATES rather than in centring, so this compares the OUTCOME each profile
 * reached — read from `entry_attempts`, which records refusals as well as fills. The arms that
 * matter most here are the ones that go dark on a low-IV day, and a table of fills alone cannot
 * see them.
 */
export function MeicDivergenceCard({ mode, date }: { mode: TradingMode; date: string | null }) {
  const { data, isLoading, dataUpdatedAt } = useQuery<MeicDivergence>({
    queryKey: ["meic-divergence", mode, date],
    queryFn: async () => {
      const params = new URLSearchParams({ mode });
      if (date !== null) params.set("date", date);
      const res = await fetch(`/api/meic/divergence?${params.toString()}`);
      if (!res.ok) throw new Error(`divergence: HTTP ${res.status}`);
      return (await res.json()) as MeicDivergence;
    },
    refetchInterval: 60_000,
  });

  const allAgree = data?.allAgreeRatePct ?? null;
  return (
    <DataCard
      title={`Profile divergence${data?.date != null ? ` (${data.date})` : ""}`}
      headers={["pair", "ticks", "agreed %", ""]}
      numFrom={1}
      loading={isLoading}
      rowCount={data?.pairs.length ?? 0}
      updatedAt={dataUpdatedAt}
      empty="no ticks where two or more profiles evaluated"
      controls={
        data !== undefined && data.ticks > 0 ? (
          <span className="chip muted" title="Ticks where every evaluating profile reached the same outcome.">
            all agree {fmtPct(allAgree, 1)} of {data.ticks.toLocaleString()} ticks
          </span>
        ) : undefined
      }
      footer={
        <p className="integrity-note">
          Agreement here is on the <em>decision</em> — filled, gate-blocked, window-blocked — not on
          the trade. Two profiles can both fill a tick and still hold different strikes or widths, so
          a pair at 100% is telling you their GATES never separated, which is a different claim from
          &ldquo;these two books are the same&rdquo;. Arms that differ only in wing width are
          expected to agree here by construction; reading that as a finding is the mistake worth
          avoiding.{" "}
          {data !== undefined && data.outcomes.length > 0 && (
            <>Outcomes seen: {data.outcomes.map((o) => `${o.outcome} ${o.count.toLocaleString()}`).join(" · ")}.</>
          )}
        </p>
      }
    >
      {data?.pairs.map((p) => {
        const rate = p.agreementRatePct;
        const tooHigh = rate !== null && rate >= 80;
        return (
          <tr key={p.profiles}>
            <td>{p.profiles}</td>
            <td>{p.ticks.toLocaleString()}</td>
            <td className={tooHigh ? "pnl-neg" : undefined}>{fmtPct(rate, 1)}</td>
            <td className="muted">
              {tooHigh ? "cannot separate these two on entry decisions" : ""}
            </td>
          </tr>
        );
      })}
    </DataCard>
  );
}
