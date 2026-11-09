import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ExternalReportsPayload } from "@console/shared";

/**
 * The External reports page, rendered with the hook mocked (a hook that never resolves under a server
 * render would only ever test the loading state). What matters: the overview lists every file, valid
 * and invalid; a report's tab draws each section as a card titled by its heading with the mark a
 * capture waits for; an invalid file is a tab saying why and nothing of its content; a missing id is
 * a plain "no such report"; and the tabs follow the files (a new file, a new tab).
 */

let payload: ExternalReportsPayload;
vi.mock("../src/lib/api", () => ({
  useExternalReports: () => ({ data: payload, isLoading: false, isError: false }),
}));
const { ExternalReportsPage, barPercent, barScales } = await import("../src/pages/External/ExternalReportsPage");

const report = (over: Record<string, unknown> = {}) => ({
  id: "demo", title: "Demo report", source: "my script", asOf: "2026-11-09T16:30:00-05:00", staleAfterHours: 24,
  summary: "one line", ageHours: 1, stale: false,
  sections: [
    { heading: "Closes", table: { columns: ["date", "close"], rows: [["d1", 1.5], ["d2", null]] } },
    { heading: "Notes", text: "plain words" },
  ],
  ...over,
});
const set = (reports: unknown[], invalid: ExternalReportsPayload["invalid"] = [], directoryExists = true) => {
  payload = { reports, invalid, directoryExists } as unknown as ExternalReportsPayload;
};

function render(path = "/reports/external"): string {
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/reports/external" element={<ExternalReportsPage />} />
        <Route path="/reports/external/:reportId" element={<ExternalReportsPage />} />
      </Routes>
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

describe("overview", () => {
  it("lists every report with title, source, as_of and state, and each invalid file with its reason", () => {
    set(
      [report(), report({ id: "old", title: "Old report", stale: true, ageHours: 90 })],
      [{ id: "bad", reasons: ["title: must not be empty", "stale_after_hours: must be above 0"] }],
    );
    const html = render();
    expect(html).toContain('href="/reports/external/demo"');
    expect(html).toContain("Demo report");
    expect(html).toContain("my script");
    expect(html).toContain("2026-11-09T16:30:00-05:00");
    expect(html).toContain("chip-ok");
    expect(html).toContain("chip chip-warn");
    expect(html).toContain('href="/reports/external/bad"');
    expect(html).toContain("title: must not be empty; stale_after_hours: must be above 0");
    expect(html).toContain("chip chip-bad");
  });

  it("an empty folder says where the guide is", () => {
    set([], [], false);
    const html = render();
    expect(html).toContain("Nothing here yet");
    expect(html).toContain("docs/guide/external-reports.md");
  });
});

describe("a report's tab", () => {
  it("draws the summary and each section as a card titled by its heading, with the capture mark", () => {
    set([report()]);
    const html = render("/reports/external/demo");
    expect(html).toContain("one line");
    expect(html.match(/<section class="gcard span-12 h-96 external-card">/g)).toHaveLength(2);
    expect(html).toMatch(/gcard-label">Closes</);
    expect(html).toMatch(/gcard-label">Notes</);
    expect(html).toContain("<svg");
    expect(html).toContain("<th>date</th>");
    expect(html).toContain("—"); // null is a dash, never 0
    expect(html).toContain('aria-current="page"');
  });

  it("marks a stale report", () => {
    set([report({ stale: true, ageHours: 40 })]);
    const html = render("/reports/external/demo");
    expect(html).toContain("chip chip-warn");
    expect(html).toContain("stale after 24 h");
  });

  it("an invalid file's tab says why and draws none of the report", () => {
    set([], [{ id: "bad", reasons: ["title: must not be empty"] }]);
    const html = render("/reports/external/bad");
    expect(html).toContain("does not meet the report contract");
    expect(html).toContain("title: must not be empty");
    expect(html).not.toContain("gcard");
  });

  it("a missing id is a plain no-such-report state, not an error", () => {
    set([report()]);
    const html = render("/reports/external/gone");
    expect(html).toContain("No such report");
    expect(html).toContain("gone");
    expect(html).toContain('href="/reports/external"');
    expect(html).not.toContain("could not be read");
  });
});

describe("bars", () => {
  const barred = (rows: unknown[][], bars: number[] = [1]) =>
    report({ sections: [{ heading: "Net", table: { columns: ["name", "net"], rows, ...(bars.length > 0 ? { bars } : {}) } }] });

  it("scales within the column, and a null has no bar", () => {
    expect(barScales({ rows: [["a", 10], ["b", 5], ["c", null]], bars: [1] }).get(1)).toEqual({ max: 10, signed: false });
    expect(barScales({ rows: [["a", 10], ["b", -20]], bars: [1] }).get(1)).toEqual({ max: 20, signed: true });
    expect(barPercent(5, { max: 10, signed: false })).toBe(50);
    expect(barScales({ rows: [["a", null]], bars: [1] }).size).toBe(0); // nothing to scale
    expect(barScales({ rows: [["a", 0]], bars: [1] }).size).toBe(0);
    expect(barScales({ rows: [["a", 3]] }).size).toBe(0); // no hint, no bars
  });

  it("draws a bar beside a number that stays as text, negatives left of a zero line in the other colour", () => {
    set([barred([["a", 10], ["b", -5], ["c", null]])]);
    const html = render("/reports/external/demo");
    expect(html.match(/class="external-bar external-bar-signed"/g)).toHaveLength(2); // the null has none
    expect(html).toContain("external-bar-pos");
    expect(html).toContain("external-bar-neg");
    expect(html).toContain('style="width:100%"'); // 10 is the column's largest
    expect(html).toContain('style="width:50%"');
    expect(html).toContain('aria-hidden="true"'); // the picture is hidden; the number is not
    expect(html).toMatch(/<\/span>10<\/td>/);
    expect(html).toMatch(/<\/span>-5<\/td>/);
  });

  it("an all-positive column is a single-sided bar, and no hint draws none", () => {
    set([barred([["a", 4], ["b", 2]])]);
    expect(render("/reports/external/demo")).toContain('class="external-bar"');
    expect(render("/reports/external/demo")).not.toContain("external-bar-signed");
    set([barred([["a", 4]], [])]);
    expect(render("/reports/external/demo")).not.toContain("external-bar");
  });
});

describe("tabs follow the files", () => {
  it("a new file appearing adds its tab, and removing it takes the tab away", () => {
    set([report()]);
    const before = render();
    expect(before).toContain('href="/reports/external/demo"');
    expect(before).not.toContain('href="/reports/external/fresh"');
    set([report(), report({ id: "fresh", title: "Fresh report" })]);
    const after = render();
    expect(after).toContain('href="/reports/external/fresh"');
    expect(after.match(/class="external-tab"/g)).toHaveLength(3); // overview + two reports
    set([report()]);
    expect(render()).not.toContain('href="/reports/external/fresh"');
  });

  it("an invalid file gets a tab too, marked", () => {
    set([report()], [{ id: "bad", reasons: ["x"] }]);
    expect(render()).toContain("external-tab external-tab-bad");
  });
});
