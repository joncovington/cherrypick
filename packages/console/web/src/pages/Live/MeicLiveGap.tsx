import { useQuery } from "@tanstack/react-query";
import type { MeicLiveGapPayload, MeicLiveGapSide } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { fmtMoney } from "../../lib/format";

/**
 * MEIC live against paper (docs/console-review-plan.md batch 5): each live condor beside its paper
 * twin (`live-shadow`, which trades what the `live` block trades), paired per session by the reader
 * (`readers/meicLiveGap.ts`). The gap is live's net less paper's, + in live's favour: the broker's
 * fills and fees against the paper model's. Sessions paper traded and live sat out are counted on
 * their own line, because what live did not trade is part of the gap too.
 */
export function useMeicLiveGap() {
  return useQuery<MeicLiveGapPayload>({
    queryKey: ["live-meic-gap"],
    queryFn: async () => {
      const res = await fetch("/api/live/meic/gap");
      if (!res.ok) throw new Error(`meic live gap: HTTP ${res.status}`);
      return (await res.json()) as MeicLiveGapPayload;
    },
    refetchInterval: 60_000,
  });
}

const RIGHT = { textAlign: "right" } as const;

function condor(s: MeicLiveGapSide | null): string {
  if (s === null) return "—";
  const strikes = `${s.putStrike ?? "?"}/${s.callStrike ?? "?"}`;
  const credit = s.credit === null ? "" : ` @ ${s.credit.toFixed(2)}`;
  return `${s.symbol ?? ""} ${strikes}${credit} · ${s.status ?? "?"}`;
}

export function MeicLiveGapView({ d }: { d: MeicLiveGapPayload }) {
  const t = d.totals;
  return (
    <Card title={`live vs paper — ${d.twin}`}>
      {d.ledger === "absent" ? (
        <p className="muted">no MEIC live ledger on this machine, so nothing to compare</p>
      ) : d.rows.length === 0 ? (
        <p className="muted">the live loop has not traded yet</p>
      ) : (
        <>
          <p className="pp-line">
            {t.paired} session{t.paired === 1 ? "" : "s"} both settled: live <PnlCell v={t.liveNet} /> · paper{" "}
            <PnlCell v={t.paperNet} /> · gap <PnlCell v={t.gap} />
            {t.paperOnly > 0 && (
              <span className="muted">
                {" "}
                · paper traded {t.paperOnly} session{t.paperOnly === 1 ? "" : "s"} live sat out ({fmtMoney(t.paperOnlyNet)})
              </span>
            )}
          </p>
          <table className="pp-months gap-table">
            <thead>
              <tr>
                <th>session</th>
                <th>live</th>
                <th>paper</th>
                <th style={RIGHT}>live net</th>
                <th style={RIGHT}>paper net</th>
                <th style={RIGHT}>gap</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r, i) => (
                <tr key={`${r.session}-${i}`} className={r.live === null ? "muted" : undefined} data-session={r.session}>
                  <td>{r.session.slice(5)}</td>
                  <td>{r.live === null ? "not traded" : condor(r.live)}</td>
                  <td>{condor(r.paper)}</td>
                  <td style={RIGHT}>{r.live === null ? "—" : <PnlCell v={r.live.net} />}</td>
                  <td style={RIGHT}>{r.paper === null ? "—" : <PnlCell v={r.paper.net} />}</td>
                  <td style={RIGHT}>{r.gap === null ? "—" : <PnlCell v={r.gap} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="muted pf-caption">
        from the first live session ({d.since ?? "—"}) · paired by session, same strikes first · net is gross less fees
        (settlement inside), whole position · the gap is live less paper, + in live&apos;s favour: the broker&apos;s fills
        and fees against the paper model&apos;s · an open condor has no net yet
      </p>
    </Card>
  );
}

export function MeicLiveGapCard() {
  const q = useMeicLiveGap();
  if (q.isLoading) return null;
  if (q.error || !q.data) return <p className="muted">live vs paper: {(q.error as Error | null)?.message ?? "unavailable"}</p>;
  return <MeicLiveGapView d={q.data} />;
}
