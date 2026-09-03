import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { ExcursionsCard } from "../src/components/performance/ExcursionsCard";
import type { ExcursionsResult } from "../src/lib/api";

const text = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

const OK: ExcursionsResult = {
  ok: true,
  error: null,
  data: {
    positions: [
      { id: "p1", tag: "control", symbol: "VXX", mae: -20.0, mfe: 40.0, n: 4 },
      { id: "p2", tag: "noflip", symbol: "VXX", mae: -5.0, mfe: 10.0, n: 3 },
    ],
    maeDistribution: { median: -12.5, n: 2 },
    mfeDistribution: { median: 25.0, n: 2 },
  },
};

describe("ExcursionsCard", () => {
  it("renders the distribution tiles and one legend entry per tag", () => {
    const html = text(<ExcursionsCard excursions={OK} />);
    expect(html).toContain("$12.50"); // mae median magnitude
    expect(html).toContain("$25.00"); // mfe median
    expect(html).toContain("control");
    expect(html).toContain("noflip");
  });

  it("plots one point per position with a hover title carrying the real numbers", () => {
    const html = text(<ExcursionsCard excursions={OK} />);
    expect(html).toContain("<circle");
    expect(html).toContain("MAE -$20.00, MFE $40.00");
  });

  it("shows the error message rather than a crash when unavailable", () => {
    const html = text(<ExcursionsCard excursions={{ ok: false, data: null, error: "excursions unavailable — no mark path" }} />);
    expect(html).toContain("excursions unavailable");
    expect(html).not.toContain("<circle");
  });

  it("shows an honest empty state, not a crash, with zero positions", () => {
    const empty: ExcursionsResult = {
      ok: true,
      error: null,
      data: { positions: [], maeDistribution: { median: null, n: 0 }, mfeDistribution: { median: null, n: 0 } },
    };
    const html = text(<ExcursionsCard excursions={empty} />);
    expect(html).toContain("no closed position has a usable mark path");
    expect(html).not.toContain("<circle");
  });
});
