import type { RegimeArm, RegimeCutsModule, RegimeCutsPayload } from "@console/shared";
import { Link, useLocation } from "react-router-dom";
import { fmtMoney, fmtPct } from "../../lib/format";
import { useRegimeCuts } from "../../lib/api";
import { armOfTag } from "../../lib/armTag";
import { Card } from "../DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../CardHelp";
import { useOpenParam } from "../../pages/Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../../pages/Flies/FliesBoxes";

/**
 * One arm's regime rows on the analysis page, from the module's NIGHTLY regime-cuts artifact
 * (`core.regimecuts`, the same payload the regime tab renders), never recomputed here.
 *
 * The artifact answers for a different window than the rest of the arm's cards: it is scoped to the
 * arm's own era (its latest measurement break) as of the session it was cut, where the cards read
 * the suite's evidence window live. So the panel names its own window and session every time it
 * draws, and shows only each dimension's single buckets -- the cross-tabs, paired tests and
 * multiplicity live on the regime tab, which this links to. The writer's `thin` and `fragile`
 * stamps pass through unchanged; a thin or fragile cell carries no tone.
 *
 * The card holds the reading in one line; the bucket table is its lightbox (2026-11-07).
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

/** The card's one line: which session the cut is of, and how much it holds for this arm. Where the cut
 *  is absent, failed or lacks the arm, the summary's own sentence says so. */
function RegimeLine({ payload, tag }: { payload: RegimeCutsPayload; tag: string }) {
  const arm = payload.status === "ok" ? payload.cuts.arms.find((a) => a.arm === armOfTag(tag)) : undefined;
  if (payload.status !== "ok" || arm === undefined) return <RegimeSummary payload={payload} tag={tag} />;
  const dims = Object.values(arm.dimensions);
  const buckets = dims.reduce((n, d) => n + d.buckets.length, 0);
  return (
    <p className="muted" style={{ fontSize: 12, margin: 0 }}>
      nightly cut of {payload.cuts.session ?? "—"}: {String(dims.length)} dimension{dims.length === 1 ? "" : "s"}, {String(buckets)}{" "}
      bucket{buckets === 1 ? "" : "s"} over {String(arm.sessions)} sessions
      {payload.stale !== null && ` — older than the ledger, which has ${payload.stale.latestSession}`}
    </p>
  );
}

const REGIME_HELP: CardHelpContent = {
  title: "Regime cuts",
  body: (
    <>
      <HelpWarn title="A different window">
        <p>
          The nightly cut is scoped to the arm&apos;s own era (its latest measurement break) as of the session it was cut. The
          other cards for this arm read the suite&apos;s evidence window live. The buckets box names the cut&apos;s window and
          session every time.
        </p>
      </HelpWarn>
      <HelpSection title="What is shown">
        <p>
          Each dimension&apos;s single buckets, from the module&apos;s nightly regime-cuts artifact and never recomputed here.
          The cross-tabs, paired tests and multiplicity live on the regime tab. The writer&apos;s thin and fragile stamps pass
          through unchanged, and a thin or fragile cell carries no tone.
        </p>
      </HelpSection>
    </>
  ),
};

/** The panel: one shared fetch of the module's cut (every arm card asks the same query). */
export function ArmRegime({ module, tag }: { module: RegimeCutsModule; tag: string }) {
  const { data, isLoading } = useRegimeCuts(module);
  const { search } = useLocation();
  const lb = useOpenParam("an");
  const box: FliesBox = {
    id: `${tag}~regime`,
    kicker: `${tag} › regime`,
    title: "Regime cuts",
    tabs: [{ id: "buckets", label: "buckets" }],
    body: () => (data === undefined ? null : <RegimeSummary payload={data} tag={tag} />),
  };
  const hasBuckets = data?.status === "ok" && data.cuts.arms.some((a) => a.arm === armOfTag(tag));
  return (
    <Card title="regime" collapseKey={`analysis-${module}-${tag}-regime`} help={REGIME_HELP} flag="different window">
      {isLoading || data === undefined ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : (
        <RegimeLine payload={data} tag={tag} />
      )}
      <div className="pf-mores pf-push">
        {hasBuckets && <BoxOpen lb={lb} id={box.id}>buckets</BoxOpen>}
        <Link className="pf-more" to={`/${module}/regime${search}`}>
          full regime cuts <span aria-hidden="true">›</span>
        </Link>
      </div>
      <FliesBoxHost lb={lb} boxes={[box]} />
    </Card>
  );
}
