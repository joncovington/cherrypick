import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { LiveArmChoice, LiveArmLoop, LiveArmPayload } from "@console/shared";
import { CONFIRMATION, LiveArmCard, LoopRow, settingChanges } from "../src/pages/Config/LiveArm";

/**
 * Config > live. Pinned: a loop the orchestrator will change shows its arms, with the one it runs; a
 * locked loop says why in words and its picker is disabled; a loop the page cannot change (another login,
 * the ladder) shows the arm it runs and the reason, with no picker; a staged change shows what it changes
 * and asks for the typed phrase with the save disabled; and with no live loop there is one line, not an
 * empty card.
 */

const arm = (name: string, settings: LiveArmChoice["settings"] = {}): LiveArmChoice => ({ arm: name, twin: `live-shadow-${name}`, settings });

const MEIC: LiveArmLoop = {
  loop: "meic",
  label: "meic · SPX",
  symbol: "SPX",
  current: "pm-200",
  editable: true,
  note: null,
  arms: [arm("pm-1400", { min_iv_rank: 0.2, entry_window_start: "14:00" }), arm("pm-200", { min_iv_rank: 0.1, per_side_stop_management: true })],
  blockers: [],
  canChange: true,
};
const MARGIN: LiveArmLoop = { loop: "meic.margin", label: "meic · XSP · margin", symbol: "XSP", current: "pm-xsp", editable: false, note: "Another login's arm is edited by hand: its live block is guarded as a whole.", arms: [], blockers: [], canChange: false };

function render(payload: LiveArmPayload): string {
  const qc = new QueryClient();
  qc.setQueryData(["config-live-arm"], payload);
  return renderToString(
    <QueryClientProvider client={qc}>
      <LiveArmCard />
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");
}

function row(loop: LiveArmLoop, initialPicked: string | null = null): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <LoopRow loop={loop} initialPicked={initialPicked} />
    </QueryClientProvider>,
  ).replace(/<!-- -->/g, "");
}

describe("settingChanges", () => {
  it("lists each plain setting whose value differs, in key order, with unset where an arm lacks it", () => {
    const [a, b] = MEIC.arms.slice().reverse() as [LiveArmChoice, LiveArmChoice];
    expect(settingChanges(a, b)).toEqual([
      { key: "entry_window_start", from: "unset", to: "14:00" },
      { key: "min_iv_rank", from: "0.1", to: "0.2" },
      { key: "per_side_stop_management", from: "true", to: "unset" },
    ]);
    expect(settingChanges(a, a)).toEqual([]);
    expect(settingChanges(undefined, b).length).toBe(2);
  });
});

describe("the live arms card", () => {
  it("shows the loops, the arm each runs, and a picker only where the page can change it", () => {
    const html = render({ loops: [MEIC, MARGIN], bridge: "ok", error: null });
    expect(html).toContain("meic · SPX");
    expect(html).toContain("runs pm-200");
    expect(html).toContain("pm-200 (current)");
    expect(html).toContain("flat and settled");
    expect(html).toContain("meic · XSP · margin");
    expect(html).toContain("runs pm-xsp");
    expect(html).toContain("edited by hand");
    // One picker: the loop the page cannot change has none.
    expect((html.match(/<select/g) ?? []).length).toBe(1);
    expect(html).not.toContain(CONFIRMATION);
  });

  it("locks the picker and says why in words while the loop is not flat and settled", () => {
    const html = render({ loops: [{ ...MEIC, canChange: false, blockers: ["1 position(s) open", "today's session is not settled"] }], bridge: "ok", error: null });
    expect(html).toContain("locked right now");
    expect(html).toContain("1 position(s) open");
    expect(html).toContain("today&#x27;s session is not settled");
    expect(html).toMatch(/<select[^>]*disabled/);
    expect(html).not.toContain("flat and settled");
  });

  it("says when no arm has a paper twin, and when the orchestrator cannot be reached", () => {
    const none = render({ loops: [{ ...MEIC, arms: [], canChange: false, note: "No arm has a paper twin yet, so there is nothing to choose between." }], bridge: "ok", error: null });
    expect(none).toContain("No arm has a paper twin yet");
    const down = render({ loops: [{ ...MEIC, editable: false, canChange: false, arms: [], note: "The orchestrator could not be reached, so the arm cannot be changed from here." }], bridge: "unavailable", error: "config bridge unavailable" });
    expect(down).toContain("nothing here can be changed");
    expect(down).not.toContain("<select");
  });

  it("draws one line, not an empty card, when no live loop is configured", () => {
    const html = render({ loops: [], bridge: "ok", error: null });
    expect(html).toContain("No live loop is configured on this machine.");
    expect(html).not.toContain("live arms");
  });
});

describe("a staged change", () => {
  it("shows what changes and the twin that will measure it, and asks for the phrase with Save disabled", () => {
    const html = row(MEIC, "pm-1400");
    expect(html).toContain("Change meic · SPX from pm-200 to pm-1400?");
    expect(html).toContain("live-shadow-pm-1400");
    expect(html).toContain("takes effect at the next arming and arms nothing");
    expect(html).toContain("kill criteria are per arm");
    expect(html).toContain("entry_window_start");
    expect(html).toContain(`Type <strong>${CONFIRMATION}</strong> to confirm`);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Save the arm/);
    expect(html).toContain("Discard");
  });

  it("draws no confirmation when the choice is the arm already running", () => {
    expect(row(MEIC, "pm-200")).not.toContain(CONFIRMATION);
    expect(row(MEIC)).not.toContain(CONFIRMATION);
  });

  it("never offers a staged change on a loop the page cannot change", () => {
    const html = row({ ...MARGIN, arms: [arm("pm-xsp"), arm("other")] }, "other");
    expect(html).not.toContain(CONFIRMATION);
    expect(html).not.toContain("<select");
  });
});
