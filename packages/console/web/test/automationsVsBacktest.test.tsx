import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutomationReadingRow } from "@console/shared";
import { ordered, readingChip, VsBacktestTable } from "../src/pages/Automations/AutomationsVsBacktest";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const BASE: AutomationReadingRow = {
  name: "plain",
  group: "g",
  pinned: null,
  withheld: false,
  error: null,
  state: null,
  paper: null,
  sample: null,
  reference: null,
};

const READ: AutomationReadingRow = {
  ...BASE,
  name: "read",
  pinned: "builder-read-2611201000",
  state: "below",
  paper: { trades: 9, counts: { inside: 6, below: 2, above: 1 }, net: -120, mean: -13.33, entry_rate: 0.64 },
  sample: { needed: 40, basis: "declared", have: 9 },
  reference: { trades: 1206, mean: 35, entry_rate: 0.71, min_trades: null },
};

describe("automations against their backtests", () => {
  it("chips each state in words, never a code", () => {
    expect(readingChip(READ)).toEqual({ cls: "chip chip-warn", words: "below its band" });
    expect(readingChip({ ...READ, state: "inside" }).words).toBe("on track");
    expect(readingChip({ ...READ, state: "too_few" })).toEqual({ cls: "chip", words: "too few to read" });
    expect(readingChip(BASE).words).toBe("no reference");
    expect(readingChip({ ...READ, state: "no_reference" }).words).toBe("run too short");
    expect(readingChip({ ...READ, withheld: true, state: null }).words).toBe("licensed, hidden");
    expect(readingChip({ ...READ, error: "no Builder run" }).words).toBe("reading failed");
  });

  it("lists pinned automations first, each part in the Builder's order", () => {
    const rows = [BASE, READ, { ...BASE, name: "later" }, { ...READ, name: "read2" }];
    expect(ordered(rows).map((r) => r.name)).toEqual(["read", "read2", "plain", "later"]);
  });

  it("lays each row out as served: trades of the sample, places, paper beside the run, linking to its page", () => {
    const out = html(<VsBacktestTable data={{ automations: [BASE, READ], error: null }} />);
    const text = out.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("read g below its band 9 of 40 6 · 2 · 1 -$13.33 $35.00 64% 71%");
    expect(text).toContain("plain g no reference — — — — — —");
    expect(out).toContain('href="/automations/automation/read#automation-working"');
  });
});
