import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { Spark } from "../src/components/chart/Spark";

/**
 * Three contracts, each of which is a way a sparkline can lie.
 *
 * A one-point series drawn as a line is a trend that does not exist. A row of independently
 * scaled sparks reads as a row of equals whatever their magnitudes. And a cumulative series that
 * ends below zero drawn in the winning colour is the plainest misread available at this size.
 */

function points(html: string): Array<[number, number]> {
  const m = /points="([^"]+)"/.exec(html);
  if (m === null) return [];
  return m[1]!.split(" ").map((p) => {
    const [x, y] = p.split(",");
    return [Number(x), Number(y)] as [number, number];
  });
}

describe("Spark", () => {
  it("draws nothing under minPoints — a dot is not a trend", () => {
    expect(renderToString(<Spark values={[5]} />)).toBe("");
    expect(renderToString(<Spark values={[]} />)).toBe("");
    expect(renderToString(<Spark values={[1, 2]} />)).not.toBe("");
  });

  it("a shared domain places a flat series where the domain says, not at the axis", () => {
    // Self-scaled, a flat series has no extent at all and collapses onto one edge.
    const alone = points(renderToString(<Spark values={[0, 0]} height={26} />));
    // Given the row's shared extent, the same series sits at its true position: the middle.
    const shared = points(renderToString(<Spark values={[0, 0]} domain={[-100, 100]} height={26} />));
    expect(shared.every(([, y]) => y === 13)).toBe(true);
    expect(alone[0]![1]).not.toBe(13);
  });

  it("clamps outside a shared domain rather than silently rescaling it", () => {
    const html = renderToString(<Spark values={[0, 500]} domain={[-100, 100]} height={26} />);
    // The top of the band, not a new top.
    expect(points(html)[1]![1]).toBe(2);
    expect(html).toContain("clamped to the shared scale");
  });

  it("colours a cumulative series by where it ends, not by its last step", () => {
    // Steps: +10 then -30 — the last step is down and so is the total.
    expect(renderToString(<Spark values={[10, -30]} mode="cumulative" />)).toContain("var(--err)");
    // Steps: -10 then +30 — the total ends up, which is what a reader takes from the line.
    expect(renderToString(<Spark values={[-10, 30]} mode="cumulative" />)).toContain("var(--ok)");
  });

  it("draws a dot per point only when the caller supplies the titles for them", () => {
    const bare = renderToString(<Spark values={[1, 2, 3]} />);
    expect(bare).not.toContain("<circle");
    const dotted = renderToString(
      <Spark values={[1, 2, 3]} pointTitles={["a", "b", "c"]} />,
    );
    expect((dotted.match(/<circle/g) ?? []).length).toBe(3);
    expect(dotted).toContain("b");
  });
});
