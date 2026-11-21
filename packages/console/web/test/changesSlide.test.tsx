import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ChangeRow, ModuleChanges } from "@console/shared";
import { ChangesBody, gapWords, howWords, readingWords } from "../src/components/changes/ChangesSlide";
import { MEIC_SLIDES, EARNINGS_SLIDES, CALENDARS_SLIDES, CONTANGO_SLIDES } from "../src/lightbox/navGroups";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const GAP = { per_session: 400, per_session_ci: [100, 700] as [number, number], per_trade: -3, per_trade_ci: [-6, -0.5] as [number, number], level: 0.95, first: "2026-10-01", last: "2026-11-20" };

function row(over: Partial<ChangeRow>): ChangeRow {
  return {
    kind: "arm",
    name: "bp-10k",
    description: null,
    id: null,
    arm: "bp-10k",
    against: "control",
    status: null,
    gap: GAP,
    reading: "leaning",
    basis: "per_trade",
    direction: "worse",
    sessions: 14,
    sessions_to_decision: 6,
    ...over,
  };
}

const DATA: ModuleChanges = {
  module: "meic",
  status: "ok",
  reason: null,
  min_sessions: 10,
  counts: {},
  withheld: 0,
  error: null,
  rows: [
    row({}),
    row({ kind: "advised", name: "entry-window-1330", description: "Late entries carry the tail.", id: "exp-2026-10-21-meic-1", arm: "advised:entry-window-1330", reading: "closed", status: "expired", direction: "better" }),
    row({ kind: "declared", name: "c1: vix high", arm: "par-c1-ic-pt50", against: "its held-out sessions", gap: null, reading: "waiting", basis: null, direction: null, sessions: null, sessions_to_decision: null, source: { kind: "idea", result: "ideas-c1", licensed: false } }),
  ],
};

describe("the changes tab", () => {
  it("reads each row on its basis, in words", () => {
    expect(gapWords(row({}))).toBe("-$3.00 a trade [-$6.00, -$0.50]");
    expect(gapWords(row({ basis: "per_session" }))).toBe("$400.00 a session [$100.00, $700.00]");
    expect(readingWords(row({}))).toBe("leaning worse");
    expect(readingWords(row({ reading: "too_early", direction: "better" }))).toBe("too early");
    expect(readingWords(row({ kind: "declared", reading: "closed", held_out: { at: null, verdict: "no_verdict", ci: null, use: 1 } }))).toBe("tested: no verdict");
    expect(howWords(row({ kind: "advised", against: "control" }))).toEqual({ kind: "advisor experiment", against: "vs control" });
  });

  it("counts the open changes and keeps the closed ones apart", () => {
    const out = html(<ChangesBody data={DATA} />);
    expect(out).toContain("under test");
    expect(out).toContain(">closed<");
    expect(out).toContain("Late entries carry the tail.");
    expect(out).toContain("exp-2026-10-21-meic-1");
    expect(out).toContain("waiting on its test");
    expect(out).toContain("6 more");
  });

  it("slices one arm's changes for its page, without the module's tiles", () => {
    const out = html(<ChangesBody data={DATA} arm="par-c1-ic-pt50" />);
    expect(out).toContain("c1: vix high");
    expect(out).not.toContain("bp-10k");
    expect(out).not.toContain("too early</");
    expect(html(<ChangesBody data={DATA} arm="nobody" />)).toContain("Nothing is under test for it");
  });

  it("says how many declared items are withheld, and why", () => {
    expect(html(<ChangesBody data={{ ...DATA, withheld: 2 }} />)).toContain("2 declared items are not shown: their runs are licensed");
  });

  it("is a tab in the modules that test changes", () => {
    for (const slides of [MEIC_SLIDES, EARNINGS_SLIDES, CALENDARS_SLIDES, CONTANGO_SLIDES]) {
      expect(slides.map((s) => s.id as string)).toContain("changes");
    }
  });
});
