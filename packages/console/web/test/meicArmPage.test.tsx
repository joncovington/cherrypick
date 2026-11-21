import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MEIC_ARM_JUMPS, MeicArmPage, meicArmHref } from "../src/pages/Meic/MeicArmPage";
import { railSlide } from "../src/components/shell/Shell";
import { recordQuestion } from "../src/components/performance/PerformanceSlide";

const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  )
    .replace(/<!--\s*-->/g, "")
    .replace(/ data-discover="true"/g, "");

const page = (name: string) => html(<MeicArmPage name={name} mode="paper" era={null} resolvedEra="advisor" symbol={null} day={null} reasons={[]} />);

describe("a MEIC arm's page", () => {
  it("is addressed by the arm's name and framed as the arms tab", () => {
    expect(meicArmHref("bp-10k")).toBe("/meic/arm/bp-10k");
    expect(meicArmHref("advised:late window")).toBe("/meic/arm/advised%3Alate%20window");
    expect(railSlide("meic", "arm")).toBe("arms");
    expect(recordQuestion("bp-10k")).toBe("How has bp-10k done?");
    expect(recordQuestion(null)).toBe("How has each arm done?");
  });

  it("reads its sections in the order every arm page uses, each with its jump", () => {
    const out = page("bp-10k");
    const at = MEIC_ARM_JUMPS.map((j) => out.indexOf(`id="${j.id}"`));
    expect(at.every((i) => i > -1)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    for (const j of MEIC_ARM_JUMPS) expect(out).toContain(`href="#${j.id}"`);
    expect(out).toContain('<a href="/meic/arms">arms</a> / <b>bp-10k</b>');
    expect(out).toContain('href="/meic/arms?arm=bp-10k"');
  });

  it("says the control is the control rather than a change of itself", () => {
    expect(page("control")).toContain("every other arm is read against it");
    expect(page("bp-10k")).not.toContain("every other arm is read against it");
  });
});
