import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { AdvisorApplyStatus } from "@console/shared";

import { ApplyBanner, CountingCaveat, EnactmentCell } from "../src/pages/Advisor/AdvisorPage";

/** React SSR separates adjacent text nodes with `<!-- -->`; strip it so assertions read as prose. */
const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

/**
 * The two places the 2026-08-25 incident has to be legible.
 *
 * The reader tests assert the payload and the ui-check drives the live page, but neither covers a
 * state the live store has not reached yet: `counted` rows are written by the evening pass, so the
 * "did not count" branch had no data to render on the day it was written. Rendering it here is what
 * stops it shipping unseen.
 */

function applyStatus(over: Partial<AdvisorApplyStatus> = {}): AdvisorApplyStatus {
  return {
    module: "meic",
    nextSession: "2026-08-26",
    artifactWritten: true,
    artifactProposals: [{ param: "regime_gex_block_negative", value: true, rationale: "exp" }],
    artifactRejected: [],
    consumerDecision: { day: "2026-08-25", params: null, reason: "advice_disabled" },
    disabledReason: null,
    enactment: null,
    ...over,
  };
}


describe("the apply banner", () => {
  it("says on the COLLAPSED head that an artifact did not land", () => {
    // The card is collapsed by default, so the head is the only thing most readers ever see. On
    // 2026-08-25 two modules sat inside it reading "written" beside "advice_disabled" and the head
    // said nothing at all.
    const html = text(
      <ApplyBanner
        status={[
          applyStatus({ enactment: { session: "2026-08-25", status: "not_enacted", detail: "the loop recorded {}", experimentId: "exp-1", decisionReason: "advice_disabled", scoredAt: null } }),
          applyStatus({ module: "flies", enactment: { session: "2026-08-25", status: "enacted", detail: null, experimentId: "exp-2", decisionReason: null, scoredAt: null } }),
        ]}
      />,
    );
    expect(html).toContain("1 not applied");
    expect(html).toContain("chip-warn");
  });

  it("says all applied when every artifact landed", () => {
    const html = text(
      <ApplyBanner
        status={[applyStatus({ enactment: { session: "2026-08-25", status: "enacted", detail: null, experimentId: "exp-1", decisionReason: null, scoredAt: null } })]}
      />,
    );
    expect(html).toContain("all applied");
    expect(html).not.toContain("not applied");
  });

  it("does not warn before the advisor has scored the session", () => {
    // An unscored session and a dropped artifact are different facts. Borrowing the warning chip
    // for the first would cry wolf every morning before the advisor's first slot runs.
    const html = text(<ApplyBanner status={[applyStatus({ enactment: null })]} />);
    expect(html).not.toContain("not applied");
    expect(html).not.toContain("chip-warn");
  });
});

describe("one module's enactment cell", () => {
  it("says 'not scored yet' rather than borrowing a failure", () => {
    const html = text(<EnactmentCell status={applyStatus({ enactment: null })} />);
    expect(html).toContain("not scored yet");
    expect(html).not.toContain("chip-warn");
  });

  it("shows the advisor's own account of why it did not land", () => {
    // `detail` verbatim as `enactment.reconcile` composes it, reason suffix and all — the cell
    // renders the advisor's sentence rather than assembling its own from the parts.
    const html = text(
      <EnactmentCell
        status={applyStatus({
          enactment: {
            session: "2026-08-25",
            status: "not_enacted",
            detail:
              "the loop recorded {} against an artifact admitting" +
              " {'regime_gex_block_negative': True} (reason: advice_disabled)",
            experimentId: "exp-1",
            decisionReason: "advice_disabled",
            scoredAt: null,
          },
        })}
      />,
    );
    expect(html).toContain("not applied");
    expect(html).toContain("regime_gex_block_negative");
    expect(html).toContain("advice_disabled");
  });

  it("is quiet when nothing was issued", () => {
    const html = text(
      <EnactmentCell
        status={applyStatus({
          enactment: { session: "2026-08-25", status: "no_artifact", detail: null, experimentId: null, decisionReason: null, scoredAt: null },
        })}
      />,
    );
    expect(html).toContain("nothing issued");
  });
});

describe("why an experiment's session count is what it is", () => {
  it("names the sessions that did not count, so the number is not bare", () => {
    const html = text(
      <CountingCaveat
        journal={[
          { session: "2026-08-25", event: "counted", detail: { enacted: false }, createdAt: "" },
          { session: "2026-08-24", event: "counted", detail: { enacted: true }, createdAt: "" },
        ]}
      />,
    );
    expect(html).toContain("1 session did not count");
    expect(html).toContain("2026-08-25");
    expect(html).not.toContain("2026-08-24");
  });

  it("pluralises, because '1 sessions' reads as a bug in the page", () => {
    const html = text(
      <CountingCaveat
        journal={[
          { session: "2026-08-20", event: "counted", detail: { enacted: false }, createdAt: "" },
          { session: "2026-08-25", event: "counted", detail: { enacted: false }, createdAt: "" },
        ]}
      />,
    );
    expect(html).toContain("2 sessions did not count");
  });

  it("reports a re-derived count with both numbers", () => {
    const html = text(
      <CountingCaveat
        journal={[
          {
            session: null,
            event: "recounted",
            detail: { sessions_run_recorded: 3, sessions_run_derived: 1 },
            createdAt: "",
          },
        ]}
      />,
    );
    expect(html).toContain("re-derived from what the loops recorded: 3 → 1");
  });

  it("renders nothing when nothing was dropped or re-derived", () => {
    expect(text(<CountingCaveat journal={[]} />)).toBe("");
    expect(
      text(
        <CountingCaveat
          journal={[{ session: "2026-08-25", event: "enacted", detail: {}, createdAt: "" }]}
        />,
      ),
    ).toBe("");
  });
});

describe("the carried state", () => {
  // Same reason this file exists at all: the live store has not reached this state on a
  // CHECKPOINTED session yet (carry is only ever claimed for the current session, and today is
  // scored after its checkpoint runs), so the branch would otherwise ship unrendered.
  const carried = (detail: string) =>
    applyStatus({
      module: "calendars",
      enactment: {
        session: "2026-08-27",
        status: "carried",
        detail,
        experimentId: "exp-2026-08-20-calendars-1",
        decisionReason: null,
        scoredAt: null,
      },
    });

  it("renders as its own chip, not as applied and not as a warning", () => {
    const html = text(<EnactmentCell status={carried("frozen on open positions")} />);
    expect(html).toContain("carried");
    expect(html).toContain("frozen on open positions");
    // The distinction the chip exists to draw: a session that DECIDED vs one that inherited.
    expect(html).not.toContain("applied");
    expect(html).not.toContain("chip-warn");
  });

  it("never counts toward the banner's dropped-artifact alarm", () => {
    // calendars enters weekly, so it carries four days in five. If carry reached this counter the
    // head would cry wolf permanently and the 2026-08-25 case would be invisible inside the noise.
    const html = text(
      <ApplyBanner
        status={[
          carried("frozen on open positions"),
          applyStatus({ module: "flies", enactment: { session: "2026-08-27", status: "enacted", detail: null, experimentId: "exp-2", decisionReason: null, scoredAt: null } }),
        ]}
      />,
    );
    expect(html).not.toContain("not applied");
    expect(html).toContain("all applied");
  });

  it("still shows a genuinely dropped artifact beside a carried one", () => {
    const html = text(
      <ApplyBanner
        status={[
          carried("frozen on open positions"),
          applyStatus({ enactment: { session: "2026-08-27", status: "not_enacted", detail: "the loop recorded no decision", experimentId: "exp-1", decisionReason: null, scoredAt: null } }),
        ]}
      />,
    );
    expect(html).toContain("1 not applied");
  });
});
