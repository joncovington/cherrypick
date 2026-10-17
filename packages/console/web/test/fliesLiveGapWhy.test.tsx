import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { LiveGapSession } from "@console/shared";
import { SessionWhy, WhyTable } from "../src/pages/Flies/LiveGapSlide";

const html = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

describe("WhyTable", () => {
  it("lists each group with its entries, the trading book's net and the raw reasons, then the pair gaps", () => {
    const out = html(
      <WhyTable
        label="from 2026-10-05"
        why={{
          unmatched: {
            "paper only: live limit": { entries: 15, net: 835.1, reasons: { max_open_margin_reached: 15 } },
            "paper only: gate": { entries: 16, net: -1962.69, reasons: { duplicate_structure: 9, sign_rule_conflict: 7 } },
          },
          pairs: { "entry fill": { pairs: 15, net_gap: 53.84 } },
        }}
      />,
    );
    for (const s of ["paper only: live limit", "max_open_margin_reached ×15", "$835.10", "-$1962.69", "duplicate_structure ×9 · sign_rule_conflict ×7", "largest gap: entry fill"]) {
      expect(out).toContain(s);
    }
  });

  it("draws nothing for an era with nothing to explain", () => {
    expect(html(<WhyTable label="x" why={{ unmatched: {}, pairs: {} }} />)).toBe("");
  });
});

describe("SessionWhy", () => {
  const session = {
    session: "2026-10-16",
    unmatched: [
      { which: "paper only", position: "P1", entry_time: "2026-10-16T10:05:00-04:00", side: "put", center: 7700, completed: true, net: -80, other_side_reason: "before_open_gate", other_side_at: null, category: "timing" },
    ],
    pairs: [{ live_position: "L1", live_entry_time: "2026-10-16T11:00:00-04:00", side: "call", center: 7720, net_gap: 25, category: "entry fill" }],
  } as unknown as LiveGapSession;

  it("shows each unmatched entry with the other side's reason, and each pair's gap category", () => {
    const out = html(<SessionWhy s={session} />);
    for (const s of ["10:05", "paper only", "put 7700", "timing", "before_open_gate", "largest gap: entry fill"]) expect(out).toContain(s);
  });

  it("says when every entry matched", () => {
    const out = html(<SessionWhy s={{ ...session, unmatched: [], pairs: [] } as LiveGapSession} />);
    expect(out).toContain("every entry matched");
  });
});
