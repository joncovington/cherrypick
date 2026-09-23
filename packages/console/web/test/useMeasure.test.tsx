import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { useMeasure } from "../src/lib/useMeasure";

/**
 * The fallback is not a detail — it is what every server render and every test in this package
 * sees, because `ResizeObserver` does not exist here and effects do not run under
 * `renderToString`. If it were 0 (or undefined), every swept chart would serve a `viewBox` of
 * `0 0 0 N` to the SSR route tests and to the first paint before the observer fires, and a chart
 * drawn zero pixels wide is indistinguishable from a chart with no data.
 */

function Probe({ fallback }: { fallback: number }) {
  const [ref, width] = useMeasure<HTMLDivElement>(fallback);
  return (
    <div ref={ref}>
      <svg viewBox={`0 0 ${width} 10`} />
    </div>
  );
}

describe("useMeasure", () => {
  it("returns the fallback where there is nothing to measure, so the viewBox is a real number", () => {
    expect(renderToString(<Probe fallback={1150} />)).toContain('viewBox="0 0 1150 10"');
  });

  it("carries the caller's own design width rather than a shared default", () => {
    expect(renderToString(<Probe fallback={720} />)).toContain('viewBox="0 0 720 10"');
  });
});
