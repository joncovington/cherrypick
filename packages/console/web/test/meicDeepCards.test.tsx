import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MeicDeepCards } from "../src/pages/Meic/MeicDeepCards";

/** MEIC's sessions tab bodies (2026-11-07 redesign): sections under questions, no old card grid. */

function html(mode: "paper" | "live"): string {
  return renderToString(
    <QueryClientProvider client={new QueryClient()}>
      <MeicDeepCards mode={mode} />
    </QueryClientProvider>,
  );
}

describe("the MEIC deep cards", () => {
  it("sit in sections, with the breakdowns on the 4/8 grid and no old cards wrapper", () => {
    const out = html("paper");
    expect(out).toContain("pf-sec");
    expect(out).toContain("lv-4");
    expect(out).toContain("lv-8");
    expect(out).not.toContain('class="cards"');
    expect(out).not.toContain('class="card"');
  });

  it("carry the NLV card only in live, with its unscoped caveat as the flag behind a help", () => {
    expect(html("paper")).not.toContain("account-level");
    const live = html("live");
    expect(live).toContain("account-level · unscoped");
    expect(live).not.toContain("chip-warn");
  });
});
