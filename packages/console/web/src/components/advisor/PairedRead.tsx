import type { AdvisorPaired } from "@console/shared";

import { fmtCash } from "../../lib/format";

/**
 * The verdict's paired read of an advised book against its base, as `packages/advisor` computed it
 * (`verdicts._paired`, through `core.compare.pair_with_control`). Rendered, never re-derived: every
 * number is the stored verdict's.
 *
 * Deliberately no tone and no chip on the interval or the p-value. Whether an interval clears zero
 * is a fact the sentence states; whether that is enough to act on is the qualification's call and
 * the reader's, and colouring it would be a verdict this console does not compute.
 */
export function PairedRead({ paired }: { paired: AdvisorPaired | null | undefined }) {
  if (paired === undefined) {
    return <p className="muted paired-read">paired read: not on this verdict yet (computed from the next evening pass)</p>;
  }
  if (paired === null || paired.sessions === 0) {
    return <p className="muted paired-read">paired read: no session both books closed</p>;
  }
  const level = `${Math.round(paired.ci_level * 100)}%`;
  const on = paired.paired_on === "entry_session" ? "entered" : "traded";
  const ci = paired.ci;
  const p = paired.sign_test_p;
  const more = paired.sessions_to_decision;
  const dropped = paired.advised_only_sessions + paired.base_only_sessions;
  return (
    <p className="muted paired-read">
      paired over {paired.sessions} session{paired.sessions === 1 ? "" : "s"} both books {on}: advised − base{" "}
      {fmtCash(paired.mean_diff)} a session
      {ci !== null && (
        <>
          , {level} interval [{fmtCash(ci[0])}, {fmtCash(ci[1])}]
        </>
      )}
      {ci === null && <> (one session: no interval)</>}
      {" · "}advised ahead on {paired.advised_better}, base on {paired.base_better}
      {paired.ties > 0 && <>, {paired.ties} tied</>}
      {p !== null && <> (sign test p = {p.toFixed(4)})</>}
      {" · "}
      {more === null
        ? "sessions to decision: —"
        : more === 0
          ? "the interval already excludes zero"
          : `about ${more} more session${more === 1 ? "" : "s"} for the interval to exclude zero at this mean and spread`}
      {dropped > 0 && <> · {dropped} session{dropped === 1 ? "" : "s"} only one book closed, not paired</>}
    </p>
  );
}
