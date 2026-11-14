import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { MorningPack, MorningProfileSymbol } from "@console/shared";

import { ProfileLevelsCard } from "../src/pages/Morning/MorningPage";

/**
 * The Morning tab's market-profile card: each symbol's prior-session levels as the pack states them,
 * linking to its Profile tab, an unmeasured symbol with its reason, and nothing on an older pack.
 */

const spx: MorningProfileSymbol = {
  session: "2026-11-13",
  rowSize: 2.5,
  poc: 7557.5,
  val: 7512.5,
  vah: 7560,
  ibLow: 7499.4,
  ibHigh: 7621.78,
  high: 7621.78,
  low: 7499.4,
  close: 7560.87,
  poorHigh: false,
  poorLow: true,
  tailHigh: 21,
  tailLow: 1,
  singlePrints: 0,
  volumePoc: null,
  untestedPocs: [{ session: "2026-10-28", poc: 7735 }],
  untestedThrough: "2026-11-13",
  reason: null,
};

const pack = (profile: MorningPack["profile"]): MorningPack => ({ profile }) as unknown as MorningPack;

describe("Morning market profile card", () => {
  it("lists each symbol's levels and links it to its Profile tab", () => {
    const raw = renderToString(
      <ProfileLevelsCard
        pack={pack({ reason: null, symbols: { SPX: spx, NDX: { ...spx, session: null, reason: "no_session_recorded" } } })}
      />,
    );
    // React separates adjacent text with comments; read the text as a viewer does
    const html = raw.replace(/<!-- -->/g, "");
    expect(html).toContain('href="/charts/chart?symbol=SPX&amp;tf=15m&amp;day=2026-11-13"');
    expect(html).toContain("7512.50–7560.00");
    expect(html).toContain("tail 21 / poor");
    expect(html).toContain("7735.00 (10-28)");
    expect(html).toContain("not measured (no_session_recorded)");
    expect(html).toContain("untested through 2026-11-13");
  });

  it("draws nothing for a pack without the block", () => {
    expect(renderToString(<ProfileLevelsCard pack={pack(null)} />)).toBe("");
  });
});
