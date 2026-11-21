import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ModulePerformanceResult } from "@console/shared";
import { ARM_JUMPS, ModuleArmPage, armHref, isControl, referenceFor } from "../src/components/armpage/ModuleArmPage";
import { PerformanceSlide } from "../src/components/performance/PerformanceSlide";
import { railSlide } from "../src/components/shell/Shell";

const html = (el: React.ReactElement, client = new QueryClient()) =>
  renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

describe("an arm's page in calendars and contango", () => {
  it("is addressed by module and arm, and framed as the module's arms tab", () => {
    expect(armHref("calendars", "friday:path")).toBe("/calendars/arm/friday%3Apath");
    expect(railSlide("calendars", "arm")).toBe("arms");
    expect(railSlide("contango", "arm")).toBe("arms");
  });

  it("knows a control by what is read against it, a weekday's control included", () => {
    const rows = [{ arm: "friday:path", against: "friday:control" }, { arm: "path", against: "control" }] as never;
    expect(isControl("friday:control", rows)).toBe(true);
    expect(isControl("control", rows)).toBe(true);
    expect(isControl("path", rows)).toBe(false);
    expect(isControl("friday:control", undefined)).toBe(true);
    // Each arm is drawn beside what it is read against; a control beside nothing.
    expect(referenceFor("friday:path", rows)).toBe("friday:control");
    expect(referenceFor("path", rows)).toBe("control");
    expect(referenceFor("friday:control", rows)).toBeNull();
    expect(referenceFor("tight-exit", undefined)).toBe("control");
  });

  it("reads against control, what it did and its analysis, in that order", () => {
    const out = html(<ModuleArmPage module="contango" name="tight-exit" />);
    const at = ARM_JUMPS.map((j) => out.indexOf(`id="${j.id}"`));
    expect(at.every((i) => i > -1)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(out).toContain('<a href="/contango/arms">arms</a> / <b>tight-exit</b>');
    expect(out).not.toContain("every other arm is read against it");
    expect(html(<ModuleArmPage module="contango" name="control" />)).toContain("every other arm is read against it");
  });

  it("points to the arms' pages where the performance slide draws no arm cards", () => {
    const perf: ModulePerformanceResult = {
      ok: true,
      module: "calendars",
      mode: "paper",
      schema: "dc_week",
      era: { key: "current", from: null, note: null, moduleEra: null },
      nRecords: 1,
      groups: [{ tag: "path", reading: { sample: 1 }, sessionNets: [["2026-11-20", 10]], tradeNets: [10] }],
      exitReasons: [],
      heldBack: [],
      pairs: [],
      breaks: [],
      excursions: { ok: false } as ModulePerformanceResult["excursions"],
      error: null,
    };
    const client = () => {
      const c = new QueryClient();
      c.setQueryData(["performance", "calendars", "current", "paper"], perf);
      return c;
    };
    expect(html(<PerformanceSlide module="calendars" armCards={false} />, client())).toContain("Each arm&#x27;s card is on its own page");
    // Narrowed to one arm, its card is the page: no pointer.
    expect(html(<PerformanceSlide module="calendars" arm="path" />, client())).not.toContain("Each arm&#x27;s card is on its own page");
  });
});
