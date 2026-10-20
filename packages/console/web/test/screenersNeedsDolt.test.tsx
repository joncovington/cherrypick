import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { SuiteFeatures } from "@console/shared";

/**
 * A Dolt-backed Screeners tab on a machine without Dolt: the card, with the steps, in place of the
 * tab -- and the tab itself, untouched, when the suite says technicals is on or cannot be asked.
 */

let features: SuiteFeatures | undefined;
vi.mock("../src/lib/useFeatures", () => ({ useFeatures: () => features }));

const { NeedsDolt } = await import("../src/pages/Screeners/NeedsDolt");

const BASE = {
  ok: true as const,
  modules: {},
  services: {},
};

function render(): string {
  return renderToString(
    <MemoryRouter>
      <NeedsDolt slide="earnings" label="earnings">
        <p>the calendar itself</p>
      </NeedsDolt>
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

describe("NeedsDolt", () => {
  it("shows the steps in place of the tab when Dolt is not set up", () => {
    features = { ...BASE, capabilities: { dolt: false }, features: { technicals: false } };
    const html = render();
    expect(html).toContain("The earnings tab needs Dolt");
    expect(html).toContain("capabilities --detect --write");
    expect(html).toContain("INSTALL.md");
    expect(html).not.toContain("the calendar itself");
  });

  it("points at the switch when Dolt is there but technicals is off", () => {
    features = { ...BASE, capabilities: { dolt: true }, features: { technicals: false } };
    const html = render();
    expect(html).toContain("The earnings tab is turned off");
    expect(html).not.toContain("capabilities --detect");
  });

  it("renders the tab when technicals is on, or while the answer is unknown", () => {
    features = { ...BASE, capabilities: { dolt: true }, features: { technicals: true } };
    expect(render()).toContain("the calendar itself");
    features = undefined;
    expect(render()).toContain("the calendar itself");
  });
});
