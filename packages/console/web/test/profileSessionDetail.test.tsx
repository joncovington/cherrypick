import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToString } from "react-dom/server";
import type { CandleBar } from "@console/shared";

import { buildProfile } from "../src/lib/marketProfile";
import { SessionDetail, periodStart } from "../src/pages/Profile/SessionDetail";

/**
 * The Profile tab's session detail: every TPO letter drawn once, each row's hover naming its letters'
 * times, and the session's facts read off the same profile the column drew.
 */

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(HERE, "..", "..", "..", "core", "tests", "fixtures", "marketprofile_cases.json"), "utf-8"),
) as { cases: { name: string; bars: CandleBar[] }[] };
const HAND = FIXTURE.cases[0]!.bars; // A 100.2-103.5, B 101.0-104.9, C 102.5-102.9, D 99.5-101.2, rows of 1

describe("Profile session detail", () => {
  it("names each period's start on the ET clock", () => {
    expect(["A", "B", "H", "I", "M"].map(periodStart)).toEqual(["09:30", "10:00", "13:00", "13:30", "15:30"]);
  });

  it("draws every TPO once and says what each row holds", () => {
    const p = buildProfile(HAND, { rowSize: 1, barSeconds: 1800, session: "2026-11-13" })!;
    const html = renderToString(<SessionDetail p={p} precision={2} live={false} onClose={() => {}} />);
    // one <text> per letter: 12 TPOs (D, AD, ABD, ABC, AB, B)
    const letters = [...html.matchAll(/<text[^>]*>([A-M])<\/text>/g)].map((m) => m[1]);
    expect(letters).toHaveLength(p.tpoTotal);
    expect(letters.sort().join("")).toBe("AAAABBBBCDDD");
    expect(html).toContain("101.00–102.00 · 3 TPOs: A 09:30, B 10:00, D 11:00");
    expect(html).toContain("POC");
    expect(html).toContain("99.00–104.00"); // value area 99-103, to row 103's top edge
    expect(html).toContain("100.20–104.90 (4.70)"); // the initial balance
  });

  it("marks a live session", () => {
    const p = buildProfile(HAND, { rowSize: 1, barSeconds: 1800, session: "2026-11-13" })!;
    expect(renderToString(<SessionDetail p={p} precision={2} live onClose={() => {}} />)).toContain(">live<");
  });
});
