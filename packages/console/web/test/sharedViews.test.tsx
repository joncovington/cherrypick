import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sortRows } from "../src/components/FilterSort";
import { ARM_SORTS, type ArmRailEntry } from "../src/components/Attempts";
import { AttemptsTab } from "../src/components/AttemptsTab";
import { occupancyRows, OCCUPANCY_SORTS } from "../src/components/OccupancyMap";
import { BreakdownCard, PREVIEW_ROWS, bucketSorts, widthOf } from "../src/components/summary/ModuleSummary";
import { Gauge } from "../src/components/summary/kit";

/**
 * The shared module views (2026-11-07): the summary, the attempts tab, the occupancy map and the guide
 * take the suite's layout once, for every module that uses them. These pin what moved: a long table
 * waits in its lightbox, the orderings are on size, timing and name, the sections carry their
 * questions, and a gauge keeps its figure and caption while its method sits behind the ?.
 */

const text = (html: string) => html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

function wrap(node: React.ReactNode, url = "/") {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const rows = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ name: `arm-${String(i + 1)}`, trades: 10 - i, winPct: 50, avgNet: 10 * (i + 1), net: 100 * (i + 1) }));

describe("a breakdown card", () => {
  it("shows a preview of a long table and leaves the rest to its lightbox", () => {
    const html = wrap(<BreakdownCard title="by arm" column="arm" rows={rows(9)} className="lv-12" />);
    expect(html).toContain("arm-1<");
    expect(html).toContain(`arm-${String(PREVIEW_ROWS)}<`);
    expect(html).not.toContain(`arm-${String(PREVIEW_ROWS + 1)}<`);
    expect(text(html)).toContain("all 9 arms");
    expect(text(html)).toContain(`${String(PREVIEW_ROWS)} of 9 shown`);
  });

  it("opens no lightbox for a short table, and puts the ordering behind filter and sort", () => {
    const html = wrap(<BreakdownCard title="by weekday" column="weekday" rows={rows(3)} className="fl-w6" />);
    expect(text(html)).not.toContain("all 3");
    expect(html).toContain('aria-label="Filter and sort"');
    expect(html).toContain('aria-label="About by weekday"');
    expect(html).not.toContain("th-sort");
  });

  it("orders on size and name, and its net order follows the face", () => {
    const r = rows(4);
    const by = (id: string, face: "per trade" | "total") => sortRows(r, bucketSorts(face).find((s) => s.id === id), false).map((x) => x.name);
    expect(by("read", "total")).toEqual(["arm-1", "arm-2", "arm-3", "arm-4"]);
    expect(by("size", "total")).toEqual(["arm-1", "arm-2", "arm-3", "arm-4"]);
    expect(by("net", "total")).toEqual(["arm-4", "arm-3", "arm-2", "arm-1"]);
    expect(by("name", "per trade")).toEqual(["arm-1", "arm-2", "arm-3", "arm-4"]);
  });

  it("gives the row of cards a width by how many there are", () => {
    expect([2, 3, 4, 5].map(widthOf)).toEqual(["fl-w6", "fl-w4", "fl-w3", "fl-w3"]);
  });
});

describe("a gauge", () => {
  it("keeps its figure and caption on the card and takes the ? for the method", () => {
    const html = wrap(<Gauge title="capture" rawPct={72.3} min={-50} max={50} caption="of premium sold" />);
    expect(html).toContain("72.3%");
    expect(text(html)).toContain("of premium sold");
    expect(html).toContain('aria-label="About capture"');
  });
});

function arm(name: string, over: Partial<ArmRailEntry>): ArmRailEntry {
  return { arm: name, attempts: 0, fills: 0, sessionsSeen: 1, sessionsWithFills: 0, resolvedToday: null, doubleStoppedToday: null, refusals: {}, lastRefusal: null, lastAttemptTs: null, lastFillTs: null, ...over };
}

describe("the arm cards' orderings", () => {
  const arms = [arm("b", { fills: 1, attempts: 50 }), arm("a", { fills: 3, attempts: 10, lastFillTs: "2026-11-06T10:00:00" }), arm("c", { fills: 2, attempts: 90, lastFillTs: "2026-11-06T12:00:00" })];
  const by = (id: string) => sortRows(arms, ARM_SORTS.find((s) => s.id === id), false).map((x) => x.arm);
  it("sorts on name, size and timing, never on a return", () => {
    expect(by("read")).toEqual(["b", "a", "c"]);
    expect(by("name")).toEqual(["a", "b", "c"]);
    expect(by("entries")).toEqual(["a", "c", "b"]);
    expect(by("tries")).toEqual(["c", "b", "a"]);
    expect(by("lastfill")).toEqual(["c", "a", "b"]); // an arm that never filled goes last
  });
});

describe("the occupancy card", () => {
  const legs = [
    { arm: "x", right: "P" as const, strike: 5000, sign: -1, count: 2 },
    { arm: "x", right: "P" as const, strike: 4950, sign: 1, count: 1 },
    { arm: "y", right: "C" as const, strike: 5100, sign: -1, count: 1 },
  ];
  const blocked = new Map([["x|5000", 4], ["x|4950", 1]]);
  it("sums each arm's longs, shorts and the entries its strikes refused", () => {
    expect(occupancyRows(legs, blocked)).toEqual([
      { arm: "x", longs: 1, shorts: 2, strikes: 2, ringed: 2, refused: 5 },
      { arm: "y", longs: 0, shorts: 1, strikes: 1, ringed: 0, refused: 0 },
    ]);
  });
  it("orders by name, contracts held and entries refused", () => {
    const r = occupancyRows(legs, blocked);
    const by = (id: string) => sortRows(r, OCCUPANCY_SORTS.find((s) => s.id === id), false).map((x) => x.arm);
    expect(by("name")).toEqual(["x", "y"]);
    expect(by("held")).toEqual(["x", "y"]);
    expect(by("refused")).toEqual(["x", "y"]);
  });
});

describe("the attempts tab", () => {
  it("puts each card under its question, with the strike map only where strikes are held", () => {
    const flies = text(wrap(<AttemptsTab module="flies" mode="paper" />));
    expect(flies).toContain("Which arms are entering, and which are held back?");
    expect(flies).toContain("When was each entry tried, and what stopped it?");
    expect(flies).toContain("What does each arm hold, and where would a new leg be refused?");
    const pmcc = text(wrap(<AttemptsTab module="pmcc" mode="paper" />));
    expect(pmcc).toContain("When was each entry tried, and what stopped it?");
    expect(pmcc).not.toContain("where would a new leg be refused");
  });
});
