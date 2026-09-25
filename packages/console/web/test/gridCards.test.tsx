import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { GridCard, StatTile } from "../src/components/grid/GridCard";
import { Bullet } from "../src/components/grid/Bullet";
import { DivergingBars } from "../src/components/grid/DivergingBars";
import { MemoryRouter } from "react-router-dom";
import { Spark } from "../src/components/chart/Spark";

/**
 * The card primitives, pinned on the contracts a visualization can break that a table could not.
 *
 * A table renders "no rows" for a missing measurement and everyone reads it correctly. A chart
 * renders a flat line at zero, which looks exactly like a real reading of zero — and this package
 * swallows reader failures into an empty payload by design (`withReadOnlyDb`), so "absent",
 * "failed" and "genuinely zero" all arrive here looking alike. That is why the em-dash rule and
 * the minimum-points rule are tested rather than assumed.
 */

describe("StatTile", () => {
  it("renders a missing value as an em dash, with no tone", () => {
    const html = renderToString(<StatTile label="net today" value={null} tone="pos" />);
    expect(html).toContain("—");
    expect(html).not.toContain("pnl-pos");
    expect(html).not.toContain("pnl-neg");
  });

  it("tones a real value, and only a real one", () => {
    expect(renderToString(<StatTile label="net" value="-$4.62" tone="neg" />)).toContain("pnl-neg");
  });

  it("shows no sparkline for a series too short to be one", () => {
    const withNone = renderToString(
      <StatTile label="net" value="$1">
        <Spark values={[]} />
      </StatTile>,
    );
    const withOne = renderToString(
      <StatTile label="net" value="$1">
        <Spark values={[5]} />
      </StatTile>,
    );
    const withMany = renderToString(
      <StatTile label="net" value="$1">
        <Spark values={[1, 2, 3]} />
      </StatTile>,
    );
    expect(withNone).not.toContain("<svg");
    expect(withOne).not.toContain("<svg");
    expect(withMany).toContain("<svg");
  });
});

describe("GridCard", () => {
  it("carries its size as classes, so a row of cards can align", () => {
    const html = renderToString(<GridCard label="forest" span={8} h={304} />);
    expect(html).toContain("span-8");
    expect(html).toContain("h-304");
  });

  it("links nowhere unless it has a page to link to -- and needs no router then", () => {
    const html = renderToString(<GridCard label="fee drag" span={3} h={248} />);
    expect(html).not.toContain("gcard-expand");
    expect(html).not.toContain("<a ");
  });

  it("links its title AND its ⤢ to the module page, keeping the reader's query", () => {
    // The query carries mode, date, arm and era: a link that dropped it would open the right page
    // on the wrong session. And no overlay: nothing here may announce a dialog any more.
    const html = renderToString(
      <MemoryRouter initialEntries={["/flies/session?mode=live&date=2026-09-23"]}>
        <GridCard label="net by arm" span={4} h={304} to="/flies/positions" toLabel="the positions behind net by arm" />
      </MemoryRouter>,
    );
    const hrefs = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]!.replace(/&amp;/g, "&"));
    expect(hrefs).toEqual(["/flies/positions?mode=live&date=2026-09-23", "/flies/positions?mode=live&date=2026-09-23"]);
    expect(html).toContain('aria-label="open the positions behind net by arm"');
    expect(html).not.toContain("aria-haspopup");
  });
});

describe("Bullet", () => {
  it("draws nothing for a part nobody measured", () => {
    const bare = renderToString(<Bullet min={0} max={100} value={null} />);
    expect(bare).not.toContain("bullet-value");
    expect(bare).not.toContain("bullet-band");
    expect(bare).not.toContain("bullet-marker");
  });

  it("places the value, the band and the comparison on one scale", () => {
    const html = renderToString(
      <Bullet min={0} max={100} value={68} band={[40, 60]} marker={71} />,
    );
    expect(html).toContain("width:68%");
    expect(html).toContain("left:40%");
    expect(html).toContain("left:71%");
  });
});

describe("DivergingBars", () => {
  it("gives a loss and a win of the same size the same weight", () => {
    const html = renderToString(
      <DivergingBars
        rows={[
          { label: "control", value: 100 },
          { label: "wide", value: -100 },
        ]}
        format={(v) => String(v)}
      />,
    );
    // Both reach the same distance from the shared zero, in opposite directions.
    expect((html.match(/width:50%/g) ?? []).length).toBe(2);
    expect(html).toContain("signed-bar-pos");
    expect(html).toContain("signed-bar-neg");
  });

  it("renders a null row as an em dash with no bar at all", () => {
    const html = renderToString(
      <DivergingBars rows={[{ label: "advised", value: null }]} format={(v) => String(v)} />,
    );
    expect(html).toContain("—");
    expect(html).not.toContain("signed-bar-fill");
  });

  it("leaves a measure whose sign is not a verdict untinted", () => {
    const html = renderToString(
      <DivergingBars rows={[{ label: "control", value: 17.7 }]} format={(v) => `${String(v)}%`} tone="none" />,
    );
    expect(html).toContain("diverge-neutral");
    expect(html).not.toContain("pnl-pos");
  });
});
