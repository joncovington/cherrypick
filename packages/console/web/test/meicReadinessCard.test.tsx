import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { LiveMeicReadiness } from "@console/shared";
import { MeicReadinessCard } from "../src/pages/Live/MeicReadinessCard";

/**
 * MEIC's live readiness card: the module's verdict drawn as given. Three states -- ready, not ready
 * with each blocker listed, and a bridge that failed, which must never read as ready.
 */

function render(r: LiveMeicReadiness | undefined): string {
  return renderToString(<MeicReadinessCard readiness={r} />)
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

const base = { ok: true as const, ready: true, blockers: [], warnings: [], openIcs: [], criticals: [] };

describe("MeicReadinessCard", () => {
  it("says Ready to arm, with the warnings still listed", () => {
    const html = render({ ...base, warnings: ["halt flag is set"] });
    expect(html).toContain("Ready to arm");
    expect(html).not.toContain("Not ready");
    expect(html).toContain("warning: halt flag is set");
    expect(html).toContain("no iron condor held");
    expect(html).toContain("none today");
  });

  it("says Not ready and lists each blocker, the held ICs and the criticals newest first", () => {
    const html = render({
      ...base,
      ready: false,
      blockers: ["orphaned orders at the broker: 1", "the supervisor is not running: nothing would tick"],
      openIcs: ["LIVE-SPX-14 SPX 7565p/7615c 10w, credit 1.8, holds call, stop 2.0x credit"],
      criticals: [
        { at: "2026-11-02T10:05:00-05:00", message: "CRITICAL older" },
        { at: "2026-11-02T10:40:00-05:00", message: "CRITICAL newer" },
      ],
    });
    expect(html).toContain("Not ready");
    expect(html).not.toContain("Ready to arm");
    expect(html).toContain("orphaned orders at the broker: 1");
    expect(html).toContain("the supervisor is not running");
    expect(html).toContain("LIVE-SPX-14 SPX 7565p/7615c");
    expect(html).toContain("10:40:00");
    expect(html.indexOf("CRITICAL newer")).toBeLessThan(html.indexOf("CRITICAL older"));
  });

  it("says readiness is unknown when the bridge failed, and never Ready to arm", () => {
    const html = render({ ok: false, error: "MEIC status unavailable — exit 1" });
    expect(html).toContain("Readiness unknown");
    expect(html).toContain("MEIC status unavailable — exit 1");
    expect(html).not.toContain("Ready to arm");
  });

  it("treats a payload with no readiness block as unknown, not ready", () => {
    const html = render(undefined);
    expect(html).toContain("Readiness unknown");
    expect(html).not.toContain("Ready to arm");
  });
});
