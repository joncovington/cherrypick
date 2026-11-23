import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { MeasurementBreak } from "@console/shared";
import { CoverageLine, IntegrityAlerts, SchemaDriftNote } from "../src/components/IntegrityParts";

/** The pieces the module integrity strips share. Synthetic figures. */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

const BREAK: MeasurementBreak = { date: "2026-01-02", key: "tick_cadence", note: "cadence changed" } as MeasurementBreak;

describe("CoverageLine", () => {
  it("names the reads, the refusals and the share, and warns only when something was refused", () => {
    const some = renderToString(<CoverageLine count={1204} noun="marks" refused={3} share={0.0025} />);
    expect(text(some)).toBe("1,204 marks · 3 refused (0.3%)");
    expect(some).toContain("integrity-warn");
    const none = renderToString(<CoverageLine count={10} noun="ticks" refused={0} share={0} refusedLabel="unmeasured" />);
    expect(text(none)).toBe("10 ticks · 0 unmeasured (0.0%)");
    expect(none).not.toContain("integrity-warn");
  });

  it("shows a dash for a share that was never computed", () => {
    expect(text(renderToString(<CoverageLine count={0} noun="marks" refused={0} share={null} />))).toContain("(—)");
  });
});

describe("SchemaDriftNote and IntegrityAlerts", () => {
  it("counts the columns the console does not know and names them", () => {
    const html = text(renderToString(<SchemaDriftNote drift={["a", "b"]} />));
    expect(html).toContain("Schema drift. The ledger holds 2 columns this console build does not know (a, b).");
    expect(text(renderToString(<SchemaDriftNote drift={["a"]} />))).toContain("holds 1 column this");
  });

  it("draws nothing when there is neither drift nor a break", () => {
    expect(renderToString(<IntegrityAlerts drift={[]} breaks={[]} />)).toBe("");
  });

  it("draws drift and breaks together in one block", () => {
    const html = renderToString(<IntegrityAlerts drift={["x"]} breaks={[BREAK]} />);
    expect(html).toContain("integrity-integrity-alerts");
    expect(text(html)).toContain("Schema drift.");
    expect(text(html)).toContain("Measurement breaks.");
    expect(text(html)).toContain("tick_cadence");
  });
});
