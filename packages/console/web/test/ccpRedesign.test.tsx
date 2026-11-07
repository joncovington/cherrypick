import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ContangoPayload, CurvePayload, PmccPayload } from "@console/shared";

vi.mock("../src/lib/prefs", () => ({
  useBoolPref: () => false,
  usePrefsSync: () => undefined,
  usePrefsVersion: () => 0,
  writePref: async () => undefined,
  getPref: () => undefined,
}));

const { CurveSession } = await import("../src/pages/Curve/CurveSession");
const { HelpTab: CurveHelp } = await import("../src/pages/Curve/HelpTab");
const { PmccSession } = await import("../src/pages/Pmcc/PmccSession");
const { HelpTab: PmccHelp } = await import("../src/pages/Pmcc/HelpTab");
const { ContangoToday, ContangoHelp, ContangoArms, ContangoCosts } = await import("../src/pages/Contango/ContangoPages");

/** Rendered the way the module frame renders a tab: inside a router and a query client. */
const html = (el: React.ReactElement) =>
  renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  ).replace(/<!--\s*-->/g, "");

const absent = { dbPresent: false } as unknown as CurvePayload;

const OLD_WRAPPERS = ["cards cards-wide", "cards cards-pairs", "grid-12", "gcard"];

describe("curve, pmcc and contango tabs", () => {
  it("lay their cards out in the portfolio tab's sections, with none of the old wrappers", () => {
    const pages: Array<[string, string]> = [
      ["curve session", html(<CurveSession data={undefined} loading />)],
      ["curve session, no store", html(<CurveSession data={absent} loading={false} />)],
      ["curve guide", html(<CurveHelp data={undefined} />)],
      ["pmcc session", html(<PmccSession data={undefined} loading symbol={null} />)],
      ["pmcc session, no store", html(<PmccSession data={{ dbPresent: false } as unknown as PmccPayload} loading={false} symbol={null} />)],
      ["pmcc guide", html(<PmccHelp data={undefined} />)],
      ["contango today", html(<ContangoToday data={undefined} metrics={undefined} loading />)],
      ["contango guide", html(<ContangoHelp data={undefined} />)],
      ["contango arms", html(<ContangoArms data={undefined} metrics={undefined} />)],
      ["contango costs", html(<ContangoCosts data={undefined} />)],
    ];
    for (const [name, out] of pages) {
      expect(out, name).toContain('class="pf-layout"');
      expect(out, name).toContain("pf-sec pf-c-");
      for (const old of OLD_WRAPPERS) expect(out, `${name}: ${old}`).not.toContain(`class="${old}`);
    }
  });

  it("asks each section a question, in the order a reader would", () => {
    const curve = html(<CurveSession data={undefined} loading />);
    expect(curve.indexOf("Where does the book stand today?")).toBeLessThan(curve.indexOf("How have the regime and the book moved lately?"));
    const guide = html(<PmccHelp data={undefined} />);
    expect(guide).toContain("What is PMCC?");
    expect(guide).toContain("What keeps the results honest?");
    const contango = html(<ContangoHelp data={undefined} />);
    expect(contango).toContain("What is contango?");
  });

  it("keeps every guide card under its question heading", () => {
    // The cards start collapsed, as they always did; their titles are what shows.
    const pmcc = html(<PmccHelp data={undefined} />);
    for (const title of ["what PMCC is", "the arms", "the honesty rules", "settlement, by symbol"]) expect(pmcc).toContain(`<h2>${title}</h2>`);
    const curve = html(<CurveHelp data={undefined} />);
    for (const title of ["what curve is", "the four arms -- one variable each", "the regime series, recorded every session"]) expect(curve).toContain(`<h2>${title}</h2>`);
    expect(html(<ContangoHelp data={undefined} />)).toContain("<h2>what contango is</h2>");
  });

  it("moves the arm comparison's caveat behind the card's ?, flagged amber", () => {
    const out = html(<ContangoArms data={undefined} metrics={undefined} />);
    expect(out).toContain("days, not trades");
    expect(out).toContain('aria-label="About arms against buy-and-hold, on daily NAV"');
    // The old standalone caption card is gone.
    expect(out).not.toContain("what the comparison can and cannot say yet");
  });

  it("shows the contango session's tiles as cards that link to their pages", () => {
    const data = { dbPresent: true, regimeSeries: [], arms: [], params: { arms: [], decisionMinutesBeforeClose: 10, decisionWindowMinutes: 5 } } as unknown as ContangoPayload;
    const out = html(<ContangoToday data={data} metrics={undefined} loading={false} />);
    expect(out).toContain("decision window");
    expect(out).toContain('href="/contango/regime"');
    expect(out).toContain('href="/contango/decisions"');
  });
});
