import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReportsPage } from "../src/pages/Reports/ReportsPage";

/**
 * The console's first component test, and it exists because of the failure it catches: a page that
 * throws at render is a BLANK page in the browser, and every check the suite had — tsc, the server
 * tests, the production build — passes cleanly while it happens. Nothing rendered a page.
 *
 * Server-rendered rather than DOM-rendered: `renderToString` needs no jsdom and no testing-library,
 * so this costs the package zero new dependencies, and a render-time throw fails the test just the
 * same. The data hooks resolve to their loading state here, which is fine — what is under test is
 * that the page mounts, picks its tab and puts both reports' chrome on screen.
 */

function render(path: string): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ReportsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("the Reports page renders", () => {
  it("defaults to the morning report, with both tabs on screen", () => {
    const html = render("/reports");
    expect(html).toContain("Morning report");
    expect(html).toContain("EOD");
    expect(html).not.toBe("");
  });

  it("shows the EOD review when the tab says so", () => {
    const html = render("/reports?tab=eod");
    expect(html).toContain("Suite review");
    expect(html).toContain("Morning");
  });

  it("an unfamiliar tab falls back to morning rather than an empty page", () => {
    expect(render("/reports?tab=nonsense")).toContain("Morning report");
  });

  it("marks the active tab for assistive tech, not just visually", () => {
    expect(render("/reports")).toContain('role="tablist"');
    expect(render("/reports?tab=eod")).toContain('aria-selected="true"');
  });
});
