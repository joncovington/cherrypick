import type { BwbLiveGap } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { fmtMoney, fmtPrice } from "../../lib/format";
import { fmtGap, signTone, useBridged } from "../../lib/bridged";

/**
 * bwb live vs paper (`run.py live-gap`, `analytics.live_gap`, bridged): over the live arm's sessions,
 * the broker's entry credit against paper's modelled credit on the same structure, whether the
 * add-on fired on both sides, the fee reconciliation (the modelled fees `fee_reconcile` snapshotted
 * against the broker's) and net per session. Live refusals are listed by reason; the term-structure
 * gate's two (`regime_inverted`, `regime_unmeasured`) are named as `live.vix9d_vix_max` by the
 * module. Correlated ladder rows: sessions are not independent draws (honesty rule 5).
 */

const ADDON_LABEL = { both: "both fired", live_only: "live only", paper_only: "paper only", neither: "neither" } as const;

export function BwbLiveGapSlide() {
  const { data, isLoading, dataUpdatedAt } = useBridged<BwbLiveGap>(["bwb-live-gap"], "/api/bwb/live-gap");
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

  const refusals = Object.entries(d.refusals);
  const t = d.totals;
  return (
    <div className="cards cards-wide">
      <Card title={`live vs paper${d.arm !== null ? ` — ${d.arm}` : ""}`} updatedAt={dataUpdatedAt}>
        <p className="muted" style={{ fontSize: 12 }}>
          Each live session beside paper&apos;s <strong>{d.arm ?? "live"}</strong> arm on the same entry session. The live
          credit is the broker&apos;s fill; paper&apos;s is the modelled mid. Gaps read <strong>+ in live&apos;s favour</strong>.
          Fees on a live row are an estimate until <code>fee_reconcile</code> replaces them with the broker&apos;s; the
          variance is shown only for a reconciled row. Ladder rows are correlated — one selloff moves several sessions.
        </p>
        {d.ledger === "absent" ? (
          <p className="muted">No bwb live ledger on this machine, or no live arm configured.</p>
        ) : d.sessions.length === 0 ? (
          <p className="muted">
            The live <strong>{d.arm}</strong> arm has taken no position yet
            {refusals.length > 0 ? " — its refusals are below." : ", and has recorded no refusal either."}
          </p>
        ) : (
          <>
            {t !== undefined && (
              <p style={{ fontSize: 13 }}>
                {String(t.sessions)} session{t.sessions === 1 ? "" : "s"} · closed pairs {String(t.closed_pairs)} · net gap{" "}
                <span className={signTone(t.net_gap)}>{fmtMoney(t.net_gap)}</span> · entry credit gap {fmtGap(t.credit_gap)} pts ·
                add-on matched {String(t.addon_matched)} of {String(t.addon_judged)} · fees reconciled {String(t.reconciled)}
              </p>
            )}
            <div className="table-scroll">
              <table className="data-table num-from-1">
                <thead>
                  <tr>
                    <th>session</th>
                    <th title="the live row's status (cancelled entries are counted apart)">live</th>
                    <th title="broker fill credit, per share">live credit</th>
                    <th title="the live limit / the mid at submission">limit · mid</th>
                    <th title="paper's modelled mid credit, per share">paper credit</th>
                    <th title="live − paper credit, same structure only">credit gap</th>
                    <th>add-on</th>
                    <th title="fees_source: modeled, broker_estimate or reconciled">fees</th>
                    <th title="broker fees − modelled fees, reconciled rows only">fee variance</th>
                    <th>live net</th>
                    <th>paper net</th>
                    <th>net gap</th>
                  </tr>
                </thead>
                <tbody>
                  {d.sessions.map((s) => (
                    <tr key={s.session}>
                      <td>
                        {s.session}
                        {s.cancelled > 0 && <span className="muted"> ({String(s.cancelled)} cancelled)</span>}
                        {!s.same_structure && s.live_position !== null && s.paper_position !== null && (
                          <span className="chip chip-warn" style={{ marginLeft: 6, fontSize: 9 }} title="live and paper hold different strikes or expirations; no credit gap is taken">
                            different structure
                          </span>
                        )}
                      </td>
                      <td className="muted">{s.live_status ?? "—"}</td>
                      <td>{fmtPrice(s.live_credit)}</td>
                      <td className="muted">{`${fmtPrice(s.live_limit)} · ${fmtPrice(s.live_mid_at_submit)}`}</td>
                      <td>{fmtPrice(s.paper_credit)}</td>
                      <td className={signTone(s.credit_gap)}>{fmtGap(s.credit_gap)}</td>
                      <td className={s.addon_match === false ? "" : "muted"}>{s.addon === null ? "—" : ADDON_LABEL[s.addon]}</td>
                      <td className="muted">{`${fmtMoney(s.live_fees)} ${s.fees_source ?? ""}`}</td>
                      <td>{fmtMoney(s.fee_variance)}</td>
                      <td>{s.live_net === null ? <span className="muted">open</span> : <PnlCell v={s.live_net} />}</td>
                      <td>{s.paper_net === null ? <span className="muted">open</span> : <PnlCell v={s.paper_net} />}</td>
                      <td><PnlCell v={s.net_gap} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <Card title="live refusals" updatedAt={dataUpdatedAt}>
        {refusals.length === 0 ? (
          <p className="muted">No live entry has been refused.</p>
        ) : (
          <div className="table-scroll">
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>reason</th>
                <th title="the configured gate this refusal belongs to">gate</th>
                <th>attempts</th>
                <th>sessions</th>
                <th>first</th>
                <th>last</th>
              </tr>
            </thead>
            <tbody>
              {refusals.map(([reason, r]) => (
                <tr key={reason}>
                  <td>{reason}</td>
                  <td>{r.gate !== null ? <code>{r.gate}</code> : <span className="muted">—</span>}</td>
                  <td>{String(r.attempts)}</td>
                  <td>{String(r.sessions)}</td>
                  <td className="muted">{r.first}</td>
                  <td className="muted">{r.last}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Card>
    </div>
  );
}
