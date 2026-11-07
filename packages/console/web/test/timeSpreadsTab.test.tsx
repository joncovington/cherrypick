import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageTimeSpreads, TimeSpreadStructure, TimeSpreadsPayload } from "@console/shared";
import { applyFilters, groupByName, joinAccount, NO_FILTERS } from "../src/lib/timeSpreadView";
import { writePref } from "../src/lib/prefs";

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
const { TimeSpreadsTab, scanHelp, scanModel, SPREAD_VIEW_SORTS } = await import("../src/pages/Screeners/TimeSpreadsTab");

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

const text = (html: string) => html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'");
const plain = (html: string) => text(html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

/** The names on the page, in order: each card's aria-label starts with its symbol and its label. */
const labels = (html: string) => [...html.matchAll(/aria-label="([A-Z]{3}): ([^"]+)"/g)].map((m) => `${m[1]} ${m[2]}`);

describe("the time-spreads tab", () => {
  it("keeps the no-edge and nothing-placed wording behind the ?, an amber flag on the card", () => {
    payload = base();
    const html = render();
    expect(html).toContain('<button type="button" class="help-flag" title="Open the ? for the full text">no tested edge</button>');
    expect(html).toContain('aria-label="About time spreads"');
    expect(html).not.toContain("Nothing here is placed.");
    expect(html).not.toContain("re-quote before sending");
    expect(html).not.toContain("<details");
    expect(html).toContain("4 of 4");
    expect(html).not.toMatch(/<button[^>]*>(place|stage|send)/i);
    const help = plain(renderToString(<>{scanHelp(payload).body}</>));
    expect(help).toContain("item 54");
    expect(help).toContain("re-quote before sending");
    expect(help).toContain("Nothing here is placed.");
  });

  it("marks what fits, the stopping cap, and a held name", () => {
    payload = base();
    const html = render();
    expect(html).toContain("fits ×2");
    expect(html).toContain("per-trade risk");
    expect(html).toContain(">sector held<");
    expect(html).toContain(">held name<");
    expect(plain(renderToString(<>{scanHelp(payload).body}</>))).toContain("1 fit, 2 too big");
  });

  it("says when there is no sizing and shows the cards unmarked, with no fit chips", () => {
    payload = base({ account: null, accountNote: "no manage evaluation for 2026-11-06, so nothing is sized to the account" });
    const html = render();
    expect(html).toContain("unmarked: no account sizing");
    expect(plain(renderToString(<>{scanHelp(payload).body}</>))).toContain("no manage evaluation for 2026-11-06");
    expect(plain(renderToString(<>{scanHelp(payload).body}</>))).toContain("cards are shown unmarked");
    expect(html).not.toContain("fits ×");
    expect(html).toContain("4 of 4");
  });

  it("applies the filters from the URL, and says what is on in the summary", () => {
    payload = base();
    expect(render("/x?structure=diagonal")).toContain("2 of 4");
    expect(render("/x?right=call&direction=bullish")).toContain("1 of 4");
    expect(render("/x?maxdb=1.5")).toContain("2 of 4");
    expect(render("/x?fits=1")).toContain("1 of 4");
    expect(render("/x?structure=diagonal&fits=1")).toContain("no structure matches these filters");
    expect(render("/x?fits=1")).toContain(">fits only<");
    expect(render("/x?structure=diagonal&right=put")).toContain('<span class="fs-badge">2</span>');
    expect(render("/x")).not.toContain("fs-badge");
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
    // The choice sits beside cards / rows, and says it is pressed.
    expect(html).toMatch(/aria-label="grouping"[^>]*><button[^>]*aria-pressed="true"[^>]*>by name</);
  });

  it("without sizing a group header counts rows, not fits", () => {
    payload = base({ account: null, accountNote: "none" });
    expect(render("/x?group=1")).toContain("2 rows");
  });

  it("draws each structure as the portfolio tab's trade card, with its legs, quotes and management levels in it", () => {
    payload = base();
    const html = text(render());
    expect(html).toContain('class="pos-card tc-card"');
    expect(html).toContain("tc-grid");
    expect(html).not.toContain("<table");
    // The sized structure is at its fitted size; the debit is whole-position dollars, a debit −.
    expect(html).toContain("-$500.00"); // 2.50 × 100 × 2 units
    expect(html).toContain("2.50 db at mid × 2");
    expect(html).toContain("2 2026-11-13 100P @ 1.65");
    expect(html).toContain("2 2026-12-18 100P @ 4.20");
    expect(html).toContain("1.6 / 1.7");
    expect(html).toContain("4.1 / 4.3");
    // What the expanded row held: the take-profit and stops, the bands, the roll date.
    expect(html).toContain("take profit 3.20 cr · value stop 1.28 cr");
    expect(html).toContain("break-even $94.10 / $106.20");
    expect(html).toContain("price stop $91.00 / none");
    expect(html).toContain("roll by 2026-11-10");
    expect(html).toContain("max loss $500");
  });

  it("shows what the scan has when a structure has no quotes", () => {
    const bare = joinAccount([row({ entryQuotes: null, longLeg: null, shortLeg: null })], null);
    expect(scanModel(bare[0]!, 100).foot.join(" ")).toContain("roll by 2026-11-10");
    expect(scanModel(bare[0]!, 100).legs.every((l) => l.text === "—")).toBe(true);
  });

  it("draws the max loss as the bar against the largest on the list, and fades what does not fit", () => {
    const views = joinAccount([A, C], account());
    const big = scanModel(views[0]!, 500); // A fits ×2: 250 × 2 = 500, the largest
    const small = scanModel(views[1]!, 500); // C stopped: one unit, 250
    expect(big.bar).toBe(100);
    expect(small.bar).toBe(50);
    expect(big.faded).toBeFalsy();
    expect(small.faded).toBe(true);
    expect(small.label).toContain("stopped by per-trade risk");
  });

  it("is cards by default and rows on request", () => {
    payload = base();
    expect(render()).toContain("tc-grid");
    void writePref("view.time-spreads", "rows");
    try {
      const html = render();
      expect(html).toContain("tr-list");
      expect(html.match(/class="tr-row"/g)).toHaveLength(4);
    } finally {
      void writePref("view.time-spreads", undefined);
    }
  });

  it("keeps the scan's order by default and offers no sort by credit, theta or take-profit", () => {
    payload = base();
    expect(labels(render()).map((l) => l.slice(0, 3))).toEqual(["AAA", "AAA", "BBB", "CCC"]);
    expect(SPREAD_VIEW_SORTS.map((s) => s.id)).toEqual(["ratio", "symbol", "front", "debit", "maxloss"]);
  });
});
