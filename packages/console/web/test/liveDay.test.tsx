import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageLiveAccount } from "@console/shared";
import { ActionChip, VerdictsHeld } from "../src/pages/Live/manageParts";
import { LiveAccountLine } from "../src/pages/Live/LiveMarks";

/**
 * Live updates (2026-11-04). While manage's evaluation is `early` (the open's first half hour) every
 * verdict chip reads "held until 10:00", the action only in its title; the account line shows the
 * reader's day P&L and estimated net liq, names the legs the stream is not carrying, and a dash where
 * a figure would rest on a stale price. Synthetic figures.
 */

function text(html: string): string {
  return html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function account(over: Partial<ManageLiveAccount> = {}): ManageLiveAccount {
  return {
    account: "****1234",
    bwd: null,
    theta: null,
    legsWithGreeks: 4,
    legsTotal: 4,
    legsMarked: 4,
    dayPl: 390,
    netLiqEstimate: 10_324,
    legsLeftOut: 2,
    ...over,
  };
}

describe("verdicts held in the opening half hour", () => {
  it("shows the action normally, and 'held until' with the action in its title while early", () => {
    expect(text(renderToString(<ActionChip action="CLOSE" />))).toBe("close");
    const held = renderToString(
      <VerdictsHeld.Provider value="10:00">
        <ActionChip action="CLOSE" />
      </VerdictsHeld.Provider>,
    );
    expect(text(held)).toBe("held until 10:00");
    expect(held).toContain("manage says close; held until 10:00 ET");
    expect(held).not.toContain("chip-bad");
  });
});

describe("the live account line", () => {
  it("shows the day's P&L and the net liq estimate when every leg streams", () => {
    const out = text(renderToString(<LiveAccountLine a={account()} />));
    expect(out).toContain("day P&L +$390.00");
    expect(out).toContain("net liq ≈ $10324.00");
    expect(out).toContain("all 4 legs streaming");
  });

  it("names the legs not streaming and dashes what would rest on them", () => {
    const out = text(renderToString(<LiveAccountLine a={account({ legsMarked: 3, dayPl: null, netLiqEstimate: null })} />));
    expect(out).toContain("day P&L —");
    expect(out).toContain("net liq ≈ —");
    expect(out).toContain("1 of 4 legs not streaming: held at the evaluation's price");
  });

  it("out of the session says the legs are at their closing marks, and never 'not streaming'", () => {
    const out = text(renderToString(<LiveAccountLine a={account()} closed />));
    expect(out).toContain("market closed: all 4 legs at their closing marks");
    expect(out).not.toContain("streaming");
    const gap = text(renderToString(<LiveAccountLine a={account({ legsMarked: 3, dayPl: null, netLiqEstimate: null })} closed />));
    expect(gap).toContain("1 of 4 legs with no quote in four days");
  });
});
