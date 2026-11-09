import { useQuery } from "@tanstack/react-query";
import type { MeicLiveGapPayload, MeicLiveGapSide } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { fmtMoney } from "../../lib/format";
import { BoxButton, BoxButtons } from "./portfolioBoxContext";

/**
 * MEIC live against paper: each live condor beside its paper
 * twin (`live-shadow`, which trades what the `live` block trades), paired per session by the reader
 * (`readers/meicLiveGap.ts`). The gap is live's net less paper's, + in live's favour: the broker's
 * fills and fees against the paper model's. Sessions paper traded and live sat out are counted on
 * their own line, because what live did not trade is part of the gap too. Since 2026-11-06 the card is
 * the summary; the pair-by-pair table and the execution record open from it.
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

const GAP_HELP: CardHelpContent = {
  title: "Live against paper",
  body: (
    <>
      <HelpWarn title="Paired by session, not pooled">
        <p>
          Each live condor is set beside its paper twin, the same strikes first. The gap is live less paper, + in live&apos;s favour: the broker&apos;s fills and fees against the
          paper model&apos;s. Net is gross less fees (settlement inside), whole position; an open condor has no net yet.
        </p>
      </HelpWarn>
      <HelpSection title="What live sat out">
        <p>A session paper traded and live did not is counted on its own line, because what live did not trade is part of the gap too.</p>
      </HelpSection>
      <HelpSection title="Execution by week">
        <p>The orchestrator&apos;s record: fills against the mid at submit and every human override, beside each week&apos;s net. Its gap counts every session live was working, so it also counts paper&apos;s entries live missed.</p>
      </HelpSection>
    </>
  ),
};

/** Each pair of sessions' condors, live beside paper: the lightbox's body. */
export function MeicLivePairs({ d }: { d: MeicLiveGapPayload }) {
  return (
    <>
      <div className="gap-scroll">
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
      </div>
      <p className="muted pf-caption">from the first live session ({d.since ?? "—"})</p>
    </>
  );
}

/** The parent card: the paired totals and the gap, with the pairs and the execution record one button away. */
export function MeicLiveGapView({ d, className }: { d: MeicLiveGapPayload; className?: string }) {
  const t = d.totals;
  return (
    <Card title={`live vs paper — ${d.twin}`} collapseKey="live-meic-gap" className={className} help={GAP_HELP} flag="paired, not pooled">
      {d.ledger === "absent" ? (
        <p className="muted">no MEIC live ledger on this machine, so nothing to compare</p>
      ) : d.rows.length === 0 ? (
        <p className="muted">the live loop has not traded yet</p>
      ) : (
        <p className="pp-line">
          {t.paired} pair{t.paired === 1 ? "" : "s"} settled on both sides: live <PnlCell v={t.liveNet} /> · paper <PnlCell v={t.paperNet} /> · gap{" "}
          <PnlCell v={t.gap} />
          {t.paperOnly > 0 && (
            <span className="muted">
              {" "}
              · paper traded {t.paperOnly} condor
              {t.paperOnly === 1 ? "" : "s"} live sat out ({fmtMoney(t.paperOnlyNet)})
            </span>
          )}
        </p>
      )}
      <BoxButtons>
        <BoxButton id="execution">execution by week</BoxButton>
        <BoxButton id="pairs">pair by pair</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/** The live-vs-paper card, reading the gap itself (a caller that also needs it passes it in). */
export function MeicLiveGapCard({ className }: { className?: string }) {
  const q = useMeicLiveGap();
  if (q.isLoading) return null;
  if (q.error || !q.data) return <p className="muted">live vs paper: {(q.error as Error | null)?.message ?? "unavailable"}</p>;
  return <MeicLiveGapView d={q.data} className={className} />;
}
