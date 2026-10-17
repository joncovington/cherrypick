import { Fragment, useState } from "react";
import type { FliesLiveGap, LiveGapEra, LiveGapSession, LiveGapWhy } from "@console/shared";
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

/**
 * Why the books differ over an era (`analytics._why_summary`): unmatched entries by which book traded
 * and the other side's reason category, and matched pairs by their largest gap. For "paper only", the
 * net is what paper made on entries live refused -- what that kind of refusal cost live (+) or saved
 * it (−) -- over books whose positions had already diverged, so a gate row is often path, not
 * precision.
 */
export function WhyTable({ label, why }: { label: string; why: LiveGapWhy | undefined }) {
  if (why === undefined) return null;
  const unmatched = Object.entries(why.unmatched).sort((a, b) => b[1].entries - a[1].entries);
  const pairs = Object.entries(why.pairs).sort((a, b) => b[1].pairs - a[1].pairs);
  if (unmatched.length === 0 && pairs.length === 0) return null;
  return (
    <>
      <div className="card-subhead">{label}</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>which book traded · why the other did not</th>
              <th>entries</th>
              <th title="the net of the book that traded, on these entries">its net</th>
              <th>reasons</th>
            </tr>
          </thead>
          <tbody>
            {unmatched.map(([key, v]) => (
              <tr key={key}>
                <td>{key}</td>
                <td>{String(v.entries)}</td>
                <td><PnlCell v={v.net} /></td>
                <td className="muted">
                  {Object.entries(v.reasons)
                    .sort((a, b) => b[1] - a[1])
                    .map(([r, n]) => `${r} ×${String(n)}`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
            {pairs.map(([cat, v]) => (
              <tr key={`pair-${cat}`}>
                <td>both traded · largest gap: {cat}</td>
                <td>{String(v.pairs)}</td>
                <td><PnlCell v={v.net_gap} /></td>
                <td className="muted">matched net gap, live − paper</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** One session's entries that only one book took, and its pairs' gap categories. */
export function SessionWhy({ s }: { s: LiveGapSession }) {
  const unmatched = s.unmatched ?? [];
  const pairs = (s.pairs ?? []).filter((p) => p.category !== undefined && p.category !== null);
  if (unmatched.length === 0 && pairs.length === 0) return <span className="muted">every entry matched; nothing to explain</span>;
  return (
    <table className="data-table">
      <tbody>
        {unmatched.map((u) => (
          <tr key={u.position}>
            <td className="mono">{u.entry_time === null ? "—" : u.entry_time.slice(11, 16)}</td>
            <td>{u.which}</td>
            <td className="mono">{`${u.side} ${String(u.center)}`}</td>
            <td>{u.net === null ? <span className="muted">open</span> : <PnlCell v={u.net} />}</td>
            <td>
              {u.category} <span className="muted">— {u.other_side_reason}</span>
            </td>
          </tr>
        ))}
        {pairs.map((p) => (
          <tr key={p.live_position}>
            <td className="mono">{p.live_entry_time === undefined || p.live_entry_time === null ? "—" : p.live_entry_time.slice(11, 16)}</td>
            <td>both</td>
            <td className="mono">{`${p.side} ${String(p.center)}`}</td>
            <td><PnlCell v={p.net_gap} /></td>
            <td>largest gap: {p.category}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LiveGapSlide() {
  const [open, setOpen] = useState<string | null>(null);
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
      <Card title="why the books differ" updatedAt={dataUpdatedAt}>
        <p className="muted" style={{ fontSize: 12 }}>
          Every entry one book took and the other did not, with the other book&apos;s own reason: live&apos;s refusal
          covering that minute (or its order on the same structure that never filled), paper&apos;s attempt outcome
          then. Grouped as live limit, timing, gate, data or fill; a reason not yet grouped reads &quot;other&quot; with
          the reason beside it. Matched pairs are grouped by their largest gap. The books hold different positions
          once they diverge, so a gate row is often path, not threshold precision.
        </p>
        <WhyTable label={`before ${breakFrom ?? "the break"}`} why={d.eras.modelled_debit?.why} />
        <WhyTable label={`from ${breakFrom ?? "the break"}`} why={d.eras.pays_limit?.why} />
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
                      <td>
                        <button
                          type="button"
                          className="btn btn-quiet"
                          style={{ padding: 0 }}
                          aria-expanded={open === s.session}
                          onClick={() => setOpen(open === s.session ? null : s.session)}
                          title="why the books differ this session"
                        >
                          {open === s.session ? "▾" : "▸"} {s.session}
                        </button>
                      </td>
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
                    {open === s.session && (
                      <tr>
                        <td colSpan={12}>
                          <SessionWhy s={s} />
                        </td>
                      </tr>
                    )}
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
