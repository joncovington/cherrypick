import { Fragment } from "react";
import type { FliesLiveGap, LiveGapEra, LiveGapSession } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { Tile } from "../../components/performance/Tile";
import { fmtMoney, fmtPct } from "../../lib/format";
import { fmtGap, signTone, useBridged } from "../../lib/bridged";

/**
 * Live vs paper, paired per session (`run.py live-gap`, `analytics.live_paper_gap`, bridged). Every
 * number is the module's; this page lays them out. The matching (which paper entry is a live
 * entry's twin), the gaps' signs and the split at the completion-rule break are all decided there.
 *
 * Every gap is signed so + favours live: credit gap = live's broker credit − paper's modelled
 * credit; debit gap = paper's completing debit − live's; fee gap = paper's fees − live's; net gap =
 * live net − paper net. The completion gap keeps `live_vs_paper`'s sign: paper's rate − live's.
 */

const RULE_LABEL: Record<LiveGapSession["completion_rule"], string> = {
  modelled_debit: "paper paid its modelled debit",
  pays_limit: "paper paid the live limit",
};

function rate(v: number | null): string {
  return v === null ? "—" : fmtPct(v * 100);
}

function EraTiles({ label, era }: { label: string; era: LiveGapEra | undefined }) {
  if (era === undefined || era.sessions === 0) {
    return (
      <p className="muted" style={{ fontSize: 12 }}>
        {label}: no live sessions
      </p>
    );
  }
  return (
    <>
      <div className="card-subhead">{label}</div>
      <TileGrid count={6}>
        <Tile label="net gap (books)" value={fmtMoney(era.net_gap)} tone={era.net_gap === null ? "dim" : era.net_gap >= 0 ? "pos" : "neg"} n={era.sessions} nUnit="sessions" afterFees title="live's settled net minus paper's, over whole books of different sizes" />
        <Tile label="matched net gap" value={fmtMoney(era.matched_net_gap)} tone={era.matched_net_gap === null ? "dim" : era.matched_net_gap >= 0 ? "pos" : "neg"} n={era.matched} nUnit="pairs" afterFees title="live minus paper on the same structure, settled pairs only" />
        <Tile label="entry fill gap" value={`${fmtGap(era.credit_gap_sum)} pts`} n={era.matched} nUnit="pairs" title="Σ (live broker credit − paper modelled credit), per share" />
        <Tile label="completion debit gap" value={`${fmtGap(era.debit_gap_sum)} pts`} n={era.debit_pairs} nUnit="pairs" title="Σ (paper completing debit − live's), over pairs where both completed; per share" />
        <Tile label="fee gap" value={fmtMoney(era.fee_gap)} title="Σ (paper modelled fees − live fees, broker-reconciled where reconciled)" />
        <Tile label="completion · live / paper" value={`${rate(era.live_completion_rate)} / ${rate(era.paper_completion_rate)}`} title="legged entries that became flies, over the same sessions" />
      </TileGrid>
    </>
  );
}

export function LiveGapSlide() {
  const { data, isLoading, dataUpdatedAt } = useBridged<FliesLiveGap>(["flies-live-gap"], "/api/flies/live-gap");
  const d = data?.data ?? null;

  if (isLoading) {
    return (
      <div className="cards cards-wide">
        <Card title="live vs paper">
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        </Card>
      </div>
    );
  }
  if (data === undefined || !data.ok || d === null) {
    return (
      <div className="cards cards-wide">
        <Card title="live vs paper" isError>
          <p className="muted">{data?.error ?? "the live-gap read is unavailable"}</p>
        </Card>
      </div>
    );
  }
  if (d.ledger === "absent" || d.sessions.length === 0) {
    return (
      <div className="cards cards-wide">
        <Card title="live vs paper" updatedAt={dataUpdatedAt}>
          <p className="muted">
            {d.ledger === "absent" ? "No live ledger on this machine." : `The live ${d.arm} arm has no sessions yet.`}
          </p>
        </Card>
      </div>
    );
  }

  const breakFrom = d.completion_rule_from ?? null;
  return (
    <div className="cards cards-wide">
      <Card title={`live vs paper — ${d.arm}, paired per session`} updatedAt={dataUpdatedAt}>
        <p className="muted" style={{ fontSize: 12 }}>
          Each live session beside paper&apos;s <strong>{d.arm}</strong> book on the same day. A live entry&apos;s twin is
          paper&apos;s entry of the same structure (side, centre, wing) within {String(d.match_window_minutes ?? "—")}{" "}
          minutes; the two books differ in size, so the book net gap and the matched gap are shown apart. Every gap
          reads <strong>+ in live&apos;s favour</strong>; the completion gap is paper&apos;s rate minus live&apos;s.
          {breakFrom !== null && (
            <>
              {" "}
              From <strong>{breakFrom}</strong> paper&apos;s legged completions pay the live limit
              (<code>engine.pays_limit</code>), so the debit gap means something different either side and the
              totals are never pooled across it.
            </>
          )}
        </p>
        <EraTiles label={`before ${breakFrom ?? "the break"} — ${RULE_LABEL.modelled_debit}`} era={d.eras.modelled_debit} />
        <EraTiles label={`from ${breakFrom ?? "the break"} — ${RULE_LABEL.pays_limit}`} era={d.eras.pays_limit} />
      </Card>
      <Card title="per session" updatedAt={dataUpdatedAt}>
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>session</th>
                <th title="live entries / completed">live</th>
                <th title="paper entries / completed">paper</th>
                <th title="paper completion rate − live's">compl. gap</th>
                <th title="live settled net">live net</th>
                <th title="paper settled net">paper net</th>
                <th title="live net − paper net (whole books)">net gap</th>
                <th title="live entries matched to a paper twin / live entries">matched</th>
                <th title="Σ live broker credit − paper modelled credit, per share">fill gap</th>
                <th title="Σ paper completing debit − live's, per share">debit gap</th>
                <th title="Σ paper fees − live fees; (b) = n of the matched live rows broker-reconciled">fee gap</th>
                <th title="live − paper net over matched, settled pairs">matched net</th>
              </tr>
            </thead>
            <tbody>
              {d.sessions.map((s, i) => {
                const prev = d.sessions[i - 1];
                const crossed = prev !== undefined && prev.completion_rule !== s.completion_rule;
                return (
                  <Fragment key={s.session}>
                    {crossed && (
                      <tr>
                        <td colSpan={12} className="muted" style={{ fontSize: 11, borderTop: "2px solid var(--border-strong, #888)" }}>
                          {`measurement break ${breakFrom ?? s.session} (completion_rule): ${RULE_LABEL[s.completion_rule]} from here — not poolable with the rows above`}
                        </td>
                      </tr>
                    )}
                    <tr>
                      <td>{s.session}</td>
                      <td>{`${String(s.live.entries)} / ${String(s.live.completed)}`}</td>
                      <td>{`${String(s.paper.entries)} / ${String(s.paper.completed)}`}</td>
                      <td className={signTone(s.completion_gap === null ? null : -s.completion_gap)}>
                        {s.completion_gap === null ? "—" : `${s.completion_gap > 0 ? "+" : ""}${(s.completion_gap * 100).toFixed(0)} pt`}
                      </td>
                      <td>{s.live.net === null ? <span className="muted">open</span> : <PnlCell v={s.live.net} />}</td>
                      <td>{s.paper.net === null ? <span className="muted">open</span> : <PnlCell v={s.paper.net} />}</td>
                      <td><PnlCell v={s.net_gap} /></td>
                      <td className="muted">{`${String(s.matched)} / ${String(s.live.entries)}`}</td>
                      <td className={signTone(s.credit_gap_sum)}>{fmtGap(s.credit_gap_sum)}</td>
                      <td className={signTone(s.debit_gap_sum)}>{fmtGap(s.debit_gap_sum)}</td>
                      <td className={signTone(s.fee_gap)}>
                        {fmtMoney(s.fee_gap)}
                        {s.broker_fees > 0 && <span className="muted"> ({String(s.broker_fees)}b)</span>}
                      </td>
                      <td><PnlCell v={s.matched_net_gap} /></td>
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
