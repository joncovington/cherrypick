import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { SkateRecordBand, SkateRecordPayload } from "@console/shared";
import { SkateRecordView, testWords } from "../src/pages/Screeners/SkateRecord";

/**
 * The forward record's section on the Skate tab passes the record through: counts, rates and z
 * scores as the file has them, the verdict only once the file says it is decision-grade and only as
 * the file states it, the test in words from the file's thresholds, and a missing or unreadable file
 * said plainly, never as zeros.
 */

const TEST = { gapHigh: 0.05, gapLow: 0, minExpiries: 20, zOneSided: 1.645 };

function band(over: Partial<SkateRecordBand> = {}): SkateRecordBand {
  return {
    highGap: { n: 40, skated: 36, rate: 0.9, implied: 0.82, z: 1.31 },
    belowZero: { n: 30, skated: 24, rate: 0.8, implied: 0.85, z: -0.77 },
    zHighOverLow: 1.12,
    passes: null,
    tested: 6,
    ...over,
  };
}

const EARLY: SkateRecordPayload = {
  status: "ok",
  writtenAt: "2026-10-21T10:50:00.000Z",
  recorded: 118,
  added: { "2026-10-20": 118 },
  settledNow: { skated: 0, tested: 0, through: 0, waiting: 118 },
  settled: 0,
  expiries: 0,
  decisionGrade: false,
  bands: {
    fixed: band({ highGap: { n: 0, skated: 0, rate: null, implied: null, z: null }, belowZero: { n: 0, skated: 0, rate: null, implied: null, z: null }, zHighOverLow: null, tested: 0 }),
    byExpiry: band({ highGap: { n: 0, skated: 0, rate: null, implied: null, z: null }, belowZero: { n: 0, skated: 0, rate: null, implied: null, z: null }, zHighOverLow: null, tested: 0 }),
  },
  test: TEST,
};

function render(data: SkateRecordPayload | undefined, state: { isLoading?: boolean; isError?: boolean } = {}): string {
  return renderToString(<SkateRecordView data={data} isLoading={state.isLoading ?? false} isError={state.isError ?? false} />)
    .replace(/<!-- -->/g, "")
    .replace(/&#x27;/g, "'");
}

function bandRow(html: string, id: string): string {
  const m = new RegExp(`<tr data-band="${id}">(.*?)</tr>`).exec(html);
  if (m === null) throw new Error(`no row for ${id}`);
  return m[1]!;
}

describe("the forward record section", () => {
  it("says a missing record is missing and an unreadable one is a failed read, never zeros", () => {
    const none = render({ ...EARLY, status: "none", recorded: null, settled: null, expiries: null, bands: { fixed: null, byExpiry: null }, test: null, added: {}, settledNow: null, writtenAt: null });
    expect(none).toContain("No forward record written yet");
    expect(none).not.toContain("rows recorded");
    expect(none).not.toContain("data-band");
    const bad = render({ ...EARLY, status: "unreadable", recorded: null, settled: null, expiries: null, bands: { fixed: null, byExpiry: null }, test: null });
    expect(bad).toContain("could not be read");
    expect(bad).not.toContain("rows recorded");
    expect(render(undefined, { isError: true })).toContain("Could not read the forward record");
  });

  it("shows the early record as counts with no verdict and no tone", () => {
    const html = render(EARLY);
    expect(html).toContain("118 rows recorded · 0 settled · 0 of 20 distinct expiries settled");
    expect(html).toContain("not yet decision-grade (0 of 20 expiries)");
    expect(html).toContain("118 waiting on their expiry");
    expect(html).not.toContain("chip-ok");
    expect(html).not.toContain(">pass<");
    expect(html).not.toContain(">fail<");
    // An empty group has no rate: a dash, not 0%.
    expect(bandRow(html, "fixed")).toContain("—");
    expect(bandRow(html, "fixed")).not.toContain("0.0%");
  });

  it("carries no pass tone before decision-grade even when the numbers lead", () => {
    const html = render({ ...EARLY, settled: 70, expiries: 7, bands: { fixed: band({ highGap: { n: 40, skated: 40, rate: 1, implied: 0.8, z: 3.2 }, zHighOverLow: 2.9 }), byExpiry: band() } });
    expect(html).toContain("not yet decision-grade (7 of 20 expiries)");
    expect(html).not.toContain("chip-ok");
    expect(bandRow(html, "fixed")).toContain("+3.20");
    expect(bandRow(html, "fixed")).toContain("100.0%");
    expect(bandRow(html, "fixed")).toContain("vs 80.0%");
  });

  it("passes each band's verdict through as the record states it, never re-deriving it", () => {
    // fixed clears both z thresholds yet the record says it fails; by expiry clears neither yet the
    // record says it passes. Working the verdict out from the z scores would swap them.
    const html = render({
      ...EARLY,
      settled: 400,
      expiries: 21,
      decisionGrade: true,
      bands: {
        fixed: band({ highGap: { n: 90, skated: 88, rate: 0.98, implied: 0.8, z: 4.1 }, zHighOverLow: 3.3, passes: false }),
        byExpiry: band({ highGap: { n: 90, skated: 70, rate: 0.78, implied: 0.8, z: -0.4 }, zHighOverLow: 0.2, passes: true }),
      },
    });
    expect(html).toContain("21 of 20 distinct expiries settled");
    expect(html).toContain("decision-grade");
    expect(bandRow(html, "fixed")).toContain('<span class="chip chip-warn">fail</span>');
    expect(bandRow(html, "fixed")).not.toContain("pass<");
    expect(bandRow(html, "byExpiry")).toContain('<span class="chip chip-ok">pass</span>');
  });

  it("does not treat the record's flag as something to work out from the expiry count", () => {
    // 25 expiries against a 20-expiry test, but the record says not decision-grade: the section agrees with the record.
    const html = render({ ...EARLY, expiries: 25, decisionGrade: false, bands: { fixed: band({ passes: true }), byExpiry: band() } });
    expect(html).toContain("not yet decision-grade (25 of 20 expiries)");
    expect(html).not.toContain("chip-ok");
  });

  it("states a decision-grade band with no stated verdict as such", () => {
    const html = render({ ...EARLY, expiries: 20, decisionGrade: true, bands: { fixed: band({ passes: null }), byExpiry: null } });
    expect(bandRow(html, "fixed")).toContain("no verdict stated");
    expect(bandRow(html, "byExpiry")).toContain("no verdict stated");
  });

  it("states the declared test from the record's own thresholds", () => {
    expect(testWords(TEST)).toContain("once 20 distinct expiries have settled");
    expect(testWords(TEST)).toContain("above 5 points");
    expect(testWords(TEST)).toContain("below zero");
    expect(testWords(TEST)).toContain("one-sided z above 1.645");
    const moved = testWords({ gapHigh: 0.08, gapLow: -0.02, minExpiries: 30, zOneSided: 1.96 });
    expect(moved).toContain("once 30 distinct expiries");
    expect(moved).toContain("above 8 points");
    expect(moved).toContain("below -2 points");
    expect(moved).toContain("z above 1.96");
    expect(render(EARLY)).toContain("once 20 distinct expiries have settled");
  });
});
