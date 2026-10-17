import type { RegimeArm, RegimeCutsModule, RegimeCutsPayload } from "@console/shared";
import { Link, useLocation } from "react-router-dom";
import { fmtMoney, fmtPct } from "../../lib/format";
import { useRegimeCuts } from "../../lib/api";
import { armOfTag } from "../../lib/armTag";

/**
 * One arm's regime rows on the analysis page, from the module's NIGHTLY regime-cuts artifact
 * (`core.regimecuts`, the same payload the regime tab renders), never recomputed here.
 *
 * The artifact answers for a different window than the rest of the arm's card: it is scoped to the
 * arm's own era (its latest measurement break) as of the session it was cut, where the card reads
 * the suite's evidence window live. So the panel names its own window and session every time it
 * draws, and shows only each dimension's single buckets -- the cross-tabs, paired tests and
 * multiplicity live on the regime tab, which this links to. The writer's `thin` and `fragile`
 * stamps pass through unchanged; a thin or fragile cell carries no tone.
 */

export function RegimeSummary({ payload, tag }: { payload: RegimeCutsPayload; tag: string }) {
  if (payload.status === "absent") {
    return <p className="muted" style={{ fontSize: 11 }}>no nightly regime cut on this machine yet</p>;
  }
  if (payload.status === "failed") {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        the regime cut could not be read — {payload.error}
      </p>
    );
  }
  const { cuts, stale } = payload;
  // An advised book is `<arm>@<experiment>`; the cut keys the arm.
  const arm: RegimeArm | undefined = cuts.arms.find((a) => a.arm === armOfTag(tag));
  if (arm === undefined) {
    return (
      <p className="muted" style={{ fontSize: 11 }}>
        this arm is not in the {cuts.session ?? "latest"} regime cut
      </p>
    );
  }
  const dims = Object.entries(arm.dimensions);
  return (
    <>
      <p className="muted" style={{ fontSize: 11 }}>
        a different window from the rest of this card: the nightly cut of {cuts.session ?? "—"}, over this arm's own
        era from {arm.eraStart ?? "its first session"} ({String(arm.sessions)} sessions)
        {stale !== null && ` — older than the ledger, which has ${stale.latestSession}`}
      </p>
      {dims.length === 0 ? (
        <p className="muted" style={{ fontSize: 11 }}>no regime dimension was cut for this arm</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>dimension</th>
                <th>bucket</th>
                <th>sessions</th>
                <th>trades</th>
                <th>net</th>
                <th>win rate</th>
              </tr>
            </thead>
            <tbody>
              {dims.flatMap(([name, d]) =>
                d.buckets.map((c, i) => {
                  const quiet = c.thin || c.fragile === true;
                  return (
                    <tr key={`${name}-${c.bucket}`} className={c.thin ? "muted" : undefined}>
                      <td>{i === 0 ? name : ""}</td>
                      <td>
                        {c.bucket}
                        {c.thin && (
                          <span className="chip" style={{ marginLeft: 6, fontSize: 9 }}>
                            thin
                          </span>
                        )}
                        {c.fragile === true && (
                          <span
                            className="chip chip-warn"
                            style={{ marginLeft: 6, fontSize: 9 }}
                            title="one session carries much of this cell, or dropping one flips its sign (the writer's stamp)"
                          >
                            fragile
                          </span>
                        )}
                      </td>
                      <td>{String(c.sessions)}</td>
                      <td>{String(c.trades)}</td>
                      <td className={quiet || c.netPnl === null ? undefined : `pnl-${c.netPnl >= 0 ? "pos" : "neg"}`}>
                        {fmtMoney(c.netPnl)}
                      </td>
                      <td>{fmtPct(c.winRate === null ? null : c.winRate * 100, 1)}</td>
                    </tr>
                  );
                }),
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** The panel: one shared fetch of the module's cut (every arm card asks the same query), and a link
 *  to the regime tab carrying the current query string. */
export function ArmRegime({ module, tag }: { module: RegimeCutsModule; tag: string }) {
  const { data, isLoading } = useRegimeCuts(module);
  const { search } = useLocation();
  return (
    <>
      <div className="card-subhead">
        regime{" "}
        <Link to={`/${module}/regime${search}`} style={{ fontSize: 11, textTransform: "none" }}>
          full regime cuts ⤢
        </Link>
      </div>
      {isLoading || data === undefined ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : (
        <RegimeSummary payload={data} tag={tag} />
      )}
    </>
  );
}
