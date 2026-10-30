import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { GexPayload, GexRegimeRow } from "@console/shared";
import { RecordedToday, fmtRecorded } from "../src/lightbox/manifests/GexLightbox";
import { flowTableId } from "../src/pages/Flow/FlowPage";
import { incomeListFrom } from "../src/pages/Screeners/OwnListTab";

/**
 * Batch 2 of docs/console-review-plan.md: Flow's four site tables became one `tables` tab, and GEX's
 * `history` became a line on `gex`. What each fold must keep: an old address opens its own table, and
 * a symbol the recorder stopped writing never reads as recorded today.
 */

const text = (html: string) => html.replace(/<!-- -->/g, "");

describe("Flow tables", () => {
  it("opens the table an old tab's address names, and the first table otherwise", () => {
    expect(flowTableId("spreads")).toBe("spreads");
    expect(flowTableId("voloi")).toBe("voloi");
    expect(flowTableId("tables")).toBe("birdseye");
    expect(flowTableId(null)).toBe("birdseye");
  });
});

describe("Screeners income lists", () => {
  it("opens the list `?list=` names, else the one an old tab's address names, else short puts", () => {
    expect(incomeListFrom("calls", "/screeners/income")).toBe("covered-calls");
    expect(incomeListFrom("puts", "/screeners/covered-calls")).toBe("short-puts");
    expect(incomeListFrom(null, "/screeners/covered-calls")).toBe("covered-calls");
    expect(incomeListFrom(null, "/screeners/income")).toBe("short-puts");
  });
});

function row(symbol: string, ts: string, netGex: number): GexRegimeRow {
  return { symbol, tradeDate: ts.slice(0, 10), ts, spot: 7600, netGex, netGexVol: null, zeroGamma: 7700, callWall: 7800, putWall: 7500 };
}

describe("GEX recorded today", () => {
  const NOW = new Date("2026-10-30T15:00:00Z");

  it("shows today's time, and the date of an older recording", () => {
    expect(fmtRecorded("2026-10-30T13:40:17Z", NOW)).toBe("09:40");
    expect(fmtRecorded("2026-07-31T19:58:35Z", NOW)).toBe("2026-07-31");
  });

  it("draws the chosen symbol's day oldest first, and says when it has too few points", () => {
    const data = {
      latest: [row("SPX", "2026-10-30T14:00:00Z", -3e9)],
      recent: { rows: [row("SPX", "2026-10-30T14:00:00Z", -3e9), row("XSP", "2026-10-30T13:55:00Z", 1e6), row("SPX", "2026-10-30T13:40:00Z", -1e9)], total: 3, offset: 0, limit: 500 },
      integrity: {},
    } as unknown as GexPayload;
    const html = text(renderToString(<RecordedToday symbol="SPX" data={data} />));
    expect(html).toContain("2 snapshots");
    expect(html).toContain("-1.00B at the first snapshot, -3.00B at the last");
    expect(text(renderToString(<RecordedToday symbol="XSP" data={data} />))).toContain("one snapshot for XSP today");
  });
});
