import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageTimeSpreads, TimeSpreadStructure, TimeSpreadsPayload } from "@console/shared";
import { applyFilters, groupByName, joinAccount, NO_FILTERS } from "../src/lib/timeSpreadView";
import { TimeSpreadScanTable } from "../src/components/TimeSpreadScanTable";

/**
 * The time-spreads tab: the join to the account's sizing, the chips' filters, the group toggle and the
 * row detail. Made-up names and figures; the tab decides nothing, so these are about what it shows.
 */

function row(over: Partial<TimeSpreadStructure>): TimeSpreadStructure {
  const name = over.underlying ?? "AAA";
  const label = over.label ?? `put calendar 2026-11-13/2026-12-18 100/100P`;
  return {
    underlying: name,
    kind: "put calendar",
    direction: "neutral",
    label,
    front: "2026-11-13",
    back: "2026-12-18",
    spot: 101.5,
    ratio: 1.3,
    ivRank: 0.5,
    sector: "Tech",
    earningsDate: "2026-12-01",
    longLeg: { strike: 100, right: (over.kind ?? "put").includes("call") ? "C" : "P", option: `${name}-L-${label}` },
    shortLeg: { strike: 100, right: (over.kind ?? "put").includes("call") ? "C" : "P", option: `${name}-S-${label}` },
    entryQuotes: [
      [4.1, 4.3],
      [1.6, 1.7],
    ],
    debitMid: 2.5,
    debitNatural: 2.7,
    gap: null,
    debitShareOfGap: null,
    frontIv: 0.6,
    backIv: 0.45,
    netDelta: 1.2,
    thetaDay: 9.5,
    pnlFlat: 120,
    pnlUp5: -40,
    pnlDown10: -150,
    losesUnchanged: false,
    breakeven: [94.1, 106.2],
    priceStop: [91, null],
    takeProfit: 3.2,
    valueStop: 1.28,
    rollBy: "2026-11-10",
    maxLoss: 250,
    units: null,
    binding: null,
    heldName: false,
    heldSector: false,
    bpAfterPct: null,
    ...over,
  };
}

const A = row({ underlying: "AAA" });
const B = row({ underlying: "AAA", kind: "call diagonal", direction: "bullish", label: "call diagonal 110/105C", debitMid: 1.5 });
const C = row({ underlying: "BBB", kind: "put diagonal", direction: "bearish", label: "put diagonal 90/95P", debitMid: 4, ratio: 1.1 });
const D = row({ underlying: "CCC", kind: "call calendar", label: "call calendar 50/50C", debitMid: 0.8 });

function account(over: Partial<ManageTimeSpreads> = {}): ManageTimeSpreads {
  return {
    sourceSession: "2026-11-06",
    takenAt: null,
    order: null,
    edge: null,
    deltaFilter: null,
    bpCapPct: 0.6,
    fits: [{ ...A, units: 2, heldSector: true }],
    fitsTotal: 1,
    tooBig: [{ ...C, units: 0, binding: "per-trade risk", heldName: true }],
    tooBigTotal: 2,
    dropped: {},
    error: null,
    ...over,
  };
}

describe("joinAccount", () => {
  it("marks each row by underlying + label: fits with its units, or the cap that stops it", () => {
    const got = joinAccount([A, C], account());
    expect(got[0]).toMatchObject({ state: "fits", units: 2, heldSector: true });
    expect(got[1]).toMatchObject({ state: "stopped", binding: "per-trade risk", heldName: true });
  });

  it("falls back to the two option symbols when the label differs", () => {
    const renamed = { ...A, label: "renamed" };
    expect(joinAccount([renamed], account())[0]).toMatchObject({ state: "fits", units: 2 });
  });

  it("calls a row the evaluation does not itemise stopped-unlisted while its too-big total exceeds the list, else absent", () => {
    expect(joinAccount([D], account())[0]!.state).toBe("stopped-unlisted");
    expect(joinAccount([D], account({ tooBigTotal: 1 }))[0]!.state).toBe("absent");
  });

  it("leaves every row unmarked without an evaluation", () => {
    expect(joinAccount([A, C], null).map((v) => v.state)).toEqual(["unmarked", "unmarked"]);
  });

  it("does not treat a zero-unit row in fits as fitting", () => {
    expect(joinAccount([A], account({ fits: [{ ...A, units: 0 }] }))[0]!.state).toBe("stopped");
  });
});

describe("applyFilters and groupByName", () => {
  const views = joinAccount([A, B, C, D], account());
  const names = (f: Partial<typeof NO_FILTERS>) => applyFilters(views, { ...NO_FILTERS, ...f }).map((v) => v.s.label);

  it("filters by structure, right, direction, max debit and fits-only", () => {
    expect(names({ structure: "diagonal" })).toEqual([B.label, C.label]);
    expect(names({ structure: "calendar" })).toEqual([A.label, D.label]);
    expect(names({ right: "call" })).toEqual([B.label, D.label]);
    expect(names({ right: "put" })).toEqual([A.label, C.label]);
    expect(names({ direction: "bearish" })).toEqual([C.label]);
    expect(names({ maxDebit: 1.5 })).toEqual([B.label, D.label]);
    expect(names({ fitsOnly: true })).toEqual([A.label]);
    expect(names({ structure: "diagonal", right: "put" })).toEqual([C.label]);
  });

  it("groups by name in the scan's order, counting what fits", () => {
    const groups = groupByName(views);
    expect(groups.map((g) => g.underlying)).toEqual(["AAA", "BBB", "CCC"]);
    expect(groups[0]).toMatchObject({ ratio: 1.3, ivRank: 0.5, spot: 101.5, earningsDate: "2026-12-01", fitCount: 1 });
    expect(groups[0]!.rows).toHaveLength(2);
    expect(groups[1]!.fitCount).toBe(0);
  });
});

let payload: TimeSpreadsPayload;
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  useTimeSpreads: () => ({ data: payload, isLoading: false, isError: false, dataUpdatedAt: 0 }),
}));
const { TimeSpreadsTab } = await import("../src/pages/Screeners/TimeSpreadsTab");

function base(over: Partial<TimeSpreadsPayload> = {}): TimeSpreadsPayload {
  return {
    day: "2026-11-06",
    days: ["2026-11-06"],
    takenAt: null,
    complete: true,
    problem: null,
    order: "ordered by the front/back IV ratio: a hypothesis, untested (item 54)",
    universe: 10,
    read: 10,
    passing: 3,
    shortlist: ["AAA", "BBB", "CCC"],
    terms: [],
    structures: [A, B, C, D],
    dropped: {},
    account: account(),
    accountNote: null,
    error: null,
    ...over,
  };
}

function render(url = "/screeners/time-spreads"): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <TimeSpreadsTab />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

describe("the time-spreads tab", () => {
  it("keeps the no-edge and nothing-placed wording, and shows a flat table by default", () => {
    payload = base();
    const html = render();
    expect(html).toContain("No tested edge.");
    expect(html).toContain("item 54");
    expect(html).toContain("re-quote before sending");
    expect(html).toContain("Nothing here is placed.");
    expect(html).not.toContain("<details");
    expect(html).toContain("4 of 4");
    expect(html).not.toMatch(/<button[^>]*>(place|stage|send)/i);
  });

  it("marks what fits, the stopping cap, and a held name", () => {
    payload = base();
    const html = render();
    expect(html).toContain("fits ×2");
    expect(html).toContain("per-trade risk");
    expect(html).toContain("sector held");
    expect(html).toContain(" held<");
    expect(html).toContain("1 fit, 2 too big");
  });

  it("says when there is no sizing and shows the rows unmarked, with no fit chips", () => {
    payload = base({ account: null, accountNote: "no manage evaluation for 2026-11-06, so nothing is sized to the account" });
    const html = render();
    expect(html).toContain("no manage evaluation for 2026-11-06");
    expect(html).toContain("rows are shown unmarked");
    expect(html).not.toContain("fits ×");
    expect(html).toContain("4 of 4");
  });

  it("applies the chips from the URL", () => {
    payload = base();
    expect(render("/x?structure=diagonal")).toContain("2 of 4");
    expect(render("/x?right=call&direction=bullish")).toContain("1 of 4");
    expect(render("/x?maxdb=1.5")).toContain("2 of 4");
    expect(render("/x?fits=1")).toContain("1 of 4");
    expect(render("/x?structure=diagonal&fits=1")).toContain("no structure matches these filters");
    expect(render("/x?fits=1")).toContain('aria-pressed="true"');
  });

  it("groups by name only when asked, one collapsible block a name with its header figures", () => {
    payload = base();
    const html = render("/x?group=1");
    expect(html.match(/<details/g)).toHaveLength(3);
    expect(html).toContain("ratio ×1.30");
    expect(html).toContain("IV rank 50%");
    expect(html).toContain("spot $101.50");
    expect(html).toContain("earnings 2026-12-01");
    expect(html).toContain("1 of 2 fit");
    expect(render("/x?group=1&structure=diagonal").match(/<details/g)).toHaveLength(2);
  });

  it("without sizing a group header counts rows, not fits", () => {
    payload = base({ account: null, accountNote: "none" });
    expect(render("/x?group=1")).toContain("2 rows");
  });
});

describe("the row detail", () => {
  const views = joinAccount([A], account());
  const draw = (initiallyOpen: string[]) =>
    renderToString(<TimeSpreadScanTable rows={views} initiallyOpen={initiallyOpen} />).replace(/<!-- -->/g, "");

  it("is closed until the row is opened", () => {
    const html = draw([]);
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("management levels");
  });

  it("shows the management levels and both legs with option symbols and quotes once open", () => {
    const html = draw([`${A.underlying}|${A.label}`]);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("breakeven band $94.10 / $106.20");
    expect(html).toContain("price stop band $91.00 / none");
    expect(html).toContain("close if the stock closes beyond it");
    expect(html).toContain("take profit 3.20 cr");
    expect(html).toContain("value stop 1.28 cr");
    expect(html).toContain("roll by 2026-11-10");
    expect(html).toContain("max loss");
    expect(html).toContain(`AAA-L-${A.label}`);
    expect(html).toContain("bid 4.10 / ask 4.30");
    expect(html).toContain("bid 1.60 / ask 1.70");
  });

  it("shows what the scan has when the row has no quotes", () => {
    const bare = joinAccount([row({ entryQuotes: null, longLeg: null, shortLeg: null })], null);
    const html = renderToString(<TimeSpreadScanTable rows={bare} initiallyOpen={["AAA|" + bare[0]!.s.label]} />);
    expect(html).toContain("roll by");
  });
});
