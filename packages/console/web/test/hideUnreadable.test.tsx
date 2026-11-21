import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ExperimentGuide, ExperimentGuideEntry, RegimeCuts } from "@console/shared";
import { splitByPower, weakWords } from "../src/components/RegimeCutsTab";
import { oneDay } from "../src/pages/Meic/SummarySlide";
import { ExperimentGuideView } from "../src/components/ExperimentGuide";

const html = (el: React.ReactElement, client = new QueryClient()) =>
  renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

function dim(sessions: number, underpowered: boolean, needed: number | null) {
  return { coveragePct: 100, tagged: 10, untagged: 0, sessions, effectiveN: sessions, dailyScale: false, degenerate: false, underpowered, sessionsNeeded: needed, buckets: [], paired: [] };
}

const CUTS = {
  module: "meic",
  dimensions: ["vix", "gap"],
  arms: [
    { arm: "control", dimensions: { vix: dim(40, false, 0), gap: dim(14, true, 16) } },
    { arm: "bp-10k", dimensions: { vix: dim(14, true, 16), gap: dim(14, true, 16) } },
  ],
} as unknown as RegimeCuts;

describe("what cannot support a decision yet", () => {
  it("keeps the arms a cut can be read for, and names the rest with what each still needs", () => {
    const vix = splitByPower(CUTS, "vix");
    expect(vix.strong.map((b) => b.arm)).toEqual(["control"]);
    expect(vix.weak).toEqual([{ arm: "bp-10k", needed: 16 }]);
    // A cut no arm can be read on has no strong arm: it becomes one line.
    expect(splitByPower(CUTS, "gap").strong).toEqual([]);
    expect(weakWords(splitByPower(CUTS, "gap").weak)).toBe("control 16 more, bp-10k 16 more");
    expect(weakWords([{ arm: "old", needed: null }])).toBe("old too few so far");
  });

  it("knows a single session's window, whose splits by wing, delta and weekday say nothing yet", () => {
    expect(oneDay({ window: { from: "2026-11-20", to: "2026-11-20", label: "day" } })).toBe(true);
    expect(oneDay({ window: { from: "2026-11-01", to: "2026-11-20", label: "month" } })).toBe(false);
    expect(oneDay({ window: { from: null, to: null, label: "" } })).toBe(false);
  });

  it("folds stopped and removed arms into one past-arms line, their cards behind a toggle", () => {
    const entry = (name: string, over: Partial<ExperimentGuideEntry>): ExperimentGuideEntry =>
      ({ name, enabled: true, retired: false, removed: false, summary: null, since: null, retiredOn: null, notes: [], overrides: [], derived: [], firstSession: null, ...over }) as ExperimentGuideEntry;
    const guide: ExperimentGuide = {
      module: "calendars",
      mode: "paper",
      unit: "arm",
      groupNotes: [],
      breaks: [],
      configMissing: false,
      entries: [
        entry("control", {}),
        entry("advised:fri-noon", { enabled: false, retired: true, retiredOn: "2026-10-30", summary: "the advisor's twin" }),
        entry("old-width", { enabled: false, removed: true }),
      ],
    };
    const c = new QueryClient();
    c.setQueryData(["experiment-guide", "/api/arms/guide/calendars", "paper"], guide);
    const out = html(<ExperimentGuideView url="/api/arms/guide/calendars" mode="paper" intro="" />, c);
    expect(out).toContain("Stopped: ");
    expect(out).toContain("advised:fri-noon (2026-10-30)");
    expect(out).toContain("Gone from the config, still in the book: old-width.");
    expect(out).toContain("<summary>show their cards</summary>");
    expect(out).not.toContain("Which arms have stopped, and why?");
  });
});
