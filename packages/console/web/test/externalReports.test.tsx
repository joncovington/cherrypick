import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ExternalFormat, ExternalReportsPayload } from "@console/shared";
import { formatValue } from "../src/lib/externalFormat";

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
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const { ExternalReportsPage, barPercent, barScales, stackWidths } = await import("../src/pages/External/ExternalReportsPage");

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
    expect(html).toMatch(/>10<span class="external-bar/);
    expect(html).toMatch(/>-5<span class="external-bar/);
  });

  it("an all-positive column is a single-sided bar, and no hint draws none", () => {
    set([barred([["a", 4], ["b", 2]])]);
    expect(render("/reports/external/demo")).toContain('class="external-bar"');
    expect(render("/reports/external/demo")).not.toContain("external-bar-signed");
    set([barred([["a", 4]], [])]);
    expect(render("/reports/external/demo")).not.toContain("external-bar");
  });
});

describe("bar options and the source foot", () => {
  const scored = (bar: unknown) =>
    report({
      sections: [
        {
          heading: "Scored",
          table: { columns: ["name", { label: "score", format: "signed", tone: "sign", bar }], rows: [["a", 50], ["b", -25], ["c", null]] },
        },
      ],
    });

  it("a fixed scale draws against its own ends, not the column's largest", () => {
    const table = { columns: ["n", { label: "s", bar: { scale: [-100, 100] as [number, number], track: true, own_column: true } }], rows: [["a", 50], ["b", -25]] };
    const scale = barScales(table).get(1)!;
    expect(scale).toMatchObject({ min: -100, max: 100, signed: true, track: true, own: true });
    expect(barPercent(50, scale)).toBe(50);
    expect(barPercent(-25, scale)).toBe(25);
    expect(barPercent(500, scale)).toBe(100);
    const lopsided = barScales({ ...table, columns: ["n", { label: "s", bar: { scale: [-50, 100] as [number, number] } }] }).get(1)!;
    expect(barPercent(-25, lopsided)).toBe(50);
    expect(barPercent(25, lopsided)).toBe(25);
  });

  it("without a scale a column bar keeps the column's largest magnitude, and the list is shorthand", () => {
    const table = { rows: [["a", 10], ["b", 5]], columns: ["n", { label: "s", bar: {} }] };
    expect(barScales(table).get(1)).toEqual({ max: 10, signed: false });
    expect(barScales({ rows: table.rows, bars: [1] }).get(1)).toEqual({ max: 10, signed: false });
  });

  it("an own-column bar sits in a column of its own after the number, on a track", () => {
    set([scored({ scale: [-100, 100], track: true, own_column: true })]);
    const html = render("/reports/external/demo");
    expect(html).toContain('<th class="external-barcol"></th>');
    expect(html.match(/<td class="external-barcol">/g)).toHaveLength(3);
    expect(html).toContain("external-bar external-bar-signed external-bar-track");
    expect(html).toContain('style="width:50%"');
    expect(html).toMatch(/>\+50<\/td><td class="external-barcol">/); // the number alone in its cell
    expect(html).not.toMatch(/>\+50<span class="external-bar/);
  });

  it("a bar with no own column stays beside the number", () => {
    set([scored({ scale: [-100, 100] })]);
    const html = render("/reports/external/demo");
    expect(html).not.toContain("external-barcol");
    expect(html).toMatch(/\+50<span class="external-bar/);
  });

  it("a report may hide the source foot; the card keeps its capture mark and the overview keeps the source", () => {
    set([report({ showSourceFoot: false })]);
    const tab = render("/reports/external/demo");
    expect(tab).not.toContain("my script, as of");
    expect(tab).not.toContain("gcard-foot");
    expect(tab).toContain("external-mark");
    expect(render("/reports/external")).toContain("my script");
    set([report({ showSourceFoot: true })]);
    expect(render("/reports/external/demo")).toContain("my script, as of");
  });
});

describe("presentation hints", () => {
  const one = (section: Record<string, unknown>) => {
    set([report({ sections: [{ heading: "Hinted", ...section }] })]);
    return render("/reports/external/demo");
  };
  const table = (columns: unknown[], rows: unknown[]) => ({ table: { columns, rows } });

  it("formats a column, right-aligned, and the figure stays as text", () => {
    const html = one(table([{ label: "premium", format: "money" }, "name"], [[24040000, "x"], [null, "y"]]));
    expect(html).toContain('<th class="external-right">premium</th>');
    expect(html).toContain('<td class="external-right">$24.04M</td>');
    expect(html).toContain("<td class=\"external-right\">—</td>");
  });

  it("a sign tone colours by sign, and a cell's own tone beats the column's", () => {
    const html = one(table([{ label: "score", format: "signed", tone: "sign" }], [[76], [-44], [0], [{ value: 5, tone: "warn" }]]));
    expect(html).toContain('class="external-right external-tone-pos">+76');
    expect(html).toContain('class="external-right external-tone-neg">−44');
    expect(html).toContain('<td class="external-right">0</td>');
    expect(html).toContain('class="external-right external-tone-warn">+5');
  });

  it("a cell object shows its text over its value, a muted suffix and a marker", () => {
    const html = one(table(["a"], [[{ value: 1, text: "shown", suffix: { text: "sweep" }, marker: "square" }]]));
    expect(html).toContain("external-marker external-marker-square");
    expect(html).toContain("shown");
    expect(html).not.toContain(">1<");
    expect(html).toContain('<span class="external-suffix external-tone-muted"> sweep</span>');
  });

  it("a row's emphasis is a class on the row", () => {
    const html = one(table(["a"], [{ cells: ["x"], emphasis: "strong" }, { cells: ["y"], emphasis: "dim" }, ["z"]]));
    expect(html).toContain('<tr class="external-row-strong">');
    expect(html).toContain('<tr class="external-row-dim">');
    expect(html).toContain("<tr><td>z</td></tr>");
  });

  it("a numbered table counts its rows from 1", () => {
    const html = one({ ...table(["a"], [["x"], ["y"]]), numbered: true });
    expect(html).toContain('<th class="external-right external-narrow">#</th>');
    expect(html).toContain('<td class="external-right external-index">2</td>');
  });

  it("a stacked column draws a legend in the header and one bar per row, proportions as the tooltip", () => {
    const stack = [{ label: "1 lot" }, { label: "2-10" }];
    const html = one(table([{ label: "size", stack }], [[[3, 1]], [null], [[0, 0]]]));
    expect(html).toContain("external-legend");
    expect(html).toContain("1 lot");
    expect(html).toContain('title="1 lot 75% · 2-10 25%"');
    expect(html.match(/class="external-stack"/g)).toHaveLength(1); // null and all-zero are dashes
    expect(html).toContain("external-stack-seg external-stack-1");
    expect(html).toContain("external-stack-seg external-stack-2");
  });

  it("stack widths are in proportion, a sliver is kept visible, and nothing to draw is empty", () => {
    expect(stackWidths([1, 1])).toEqual([80, 80]);
    const w = stackWidths([1000, 1, 0]);
    expect(w[2]).toBe(0);
    expect(w[1]).toBeGreaterThanOrEqual(3);
    expect(w.reduce((a, b) => a + b, 0)).toBeCloseTo(160, 5);
    expect(stackWidths([0, null])).toEqual([]);
  });

  it("a key and a footnote draw under the table, tags in their tone", () => {
    const html = one({
      ...table(["a"], [["x"]]),
      key: [{ tag: "V>OI", tone: "warn", label: "volume over OI", text: "explains it" }],
      footnote: "a note",
    });
    expect(html).toContain('<span class="external-key-tag external-tone-warn">V&gt;OI</span> volume over OI');
    expect(html).toContain("<dd>explains it</dd>");
    expect(html).toContain('<p class="external-footnote">a note</p>');
  });

  it("a plain report draws none of it", () => {
    set([report()]);
    const html = render("/reports/external/demo");
    for (const gone of ["external-legend", "external-key", "external-footnote", "external-marker", "external-row-"]) {
      expect(html).not.toContain(gone);
    }
  });
});

describe("formats read as the shared table says", () => {
  const cases = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "packages", "core", "tests", "fixtures", "external_report_cases.json"), "utf8"),
  ).formats as Array<[ExternalFormat, string | number | null, string]>;
  for (const [fmt, value, shown] of cases) {
    it(`${fmt} ${String(value)} is ${shown}`, () => {
      expect(formatValue(fmt, value)).toBe(shown);
    });
  }
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
