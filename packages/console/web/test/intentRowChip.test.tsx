import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageIntentAsk, ManageTrade } from "@console/shared";
import { RowIntentChip, asksIntent } from "../src/pages/Live/IntentStrip";
import { PositionRows } from "../src/pages/Live/PositionCards";

/**
 * The table view of Positions folds each trade into a row, and the card's question lives inside the opened
 * row. A trade with an open question carries a marker on its collapsed row, so the question is not hidden
 * behind a click; a trade with none carries nothing. (The "answered" text needs the browser's saved state,
 * which a server render never has; the browser check covers it.)
 */

const ASK: ManageIntentAsk = {
  kind: "wheel",
  asked: true,
  current: null,
  options: ["wheel", "no_wheel"],
  verdicts: {},
  suggested: null,
  evidence: [],
  note: null,
  key: "trades.XYZ-261001-1.intent",
  scope: "trade",
};

const trade = (ask: ManageIntentAsk | null): ManageTrade =>
  ({ tradeId: "XYZ-261001-1", intentAsk: ask }) as unknown as ManageTrade;

const chip = (ask: ManageIntentAsk | null) =>
  renderToString(<RowIntentChip t={trade(ask)} />);

const row = (id: string, ask: ManageIntentAsk | null): ManageTrade =>
  ({
    tradeId: id,
    shape: "naked put",
    intent: null,
    pnlNet: -10,
    capture: -0.1,
    nearestShortDte: 30,
    nearestShortExpiry: "2027-01-15",
    action: "HOLD",
    intentAsk: ask,
  }) as unknown as ManageTrade;

describe("the table view's collapsed rows", () => {
  it("mark the trade that has a question and only that trade", () => {
    const html = renderToString(
      <PositionRows
        trades={[row("ASKED-1", ASK), row("QUIET-1", null)]}
        inSession={true}
        onOpen={() => undefined}
      />,
    );
    const rows = html.split('class="tr-item"').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain("intent?");
    expect(rows[1]).not.toContain("intent?");
  });
});

describe("the marker on a collapsed position row", () => {
  it("shows while manage asks, in the warning colour", () => {
    expect(chip(ASK)).toContain("intent?");
    expect(chip(ASK)).toContain("chip-warn");
  });

  it("shows nothing for a trade manage is not asking about", () => {
    expect(chip(null)).toBe("");
    expect(chip({ ...ASK, asked: false })).toBe("");
  });

  it("shows nothing when there is nothing to answer it with", () => {
    expect(chip({ ...ASK, options: [] })).toBe("");
  });

  it("shows nothing when the strip would not show: a key the save route could not take", () => {
    const bad = { ...ASK, key: "positions" };
    expect(asksIntent(trade(bad))).toBe(false);
    expect(chip(bad)).toBe("");
  });

  it("agrees with the strip on a put that waits for its ID (a name-scoped ask)", () => {
    const waiting = {
      ...ASK,
      scope: "name" as const,
      key: "positions.XYZ.intent",
    };
    expect(asksIntent(trade(waiting))).toBe(true);
    expect(chip(waiting)).toContain("intent?");
  });
});
