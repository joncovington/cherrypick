import { describe, it, expect } from "vitest";
import { armOfTag, displayName, experimentOfTag } from "../src/lib/armTag";

describe("an arm's name on a card, its experiment id beneath", () => {
  it("splits a stamped tag into the arm and the experiment", () => {
    expect(armOfTag("advised:entry-window-1330@exp-2026-10-21-meic-1")).toBe("advised:entry-window-1330");
    expect(experimentOfTag("advised:entry-window-1330@exp-2026-10-21-meic-1")).toBe("exp-2026-10-21-meic-1");
    expect(experimentOfTag("control")).toBeNull();
  });

  it("names a group by its arm, and by its whole tag only where two shown together share the arm", () => {
    const all = ["control", "advised:late@exp-1", "advised:twice@exp-2", "advised:twice@exp-3"];
    expect(displayName("advised:late@exp-1", all)).toBe("advised:late");
    expect(displayName("control", all)).toBe("control");
    expect(displayName("advised:twice@exp-2", all)).toBe("advised:twice@exp-2");
  });
});
