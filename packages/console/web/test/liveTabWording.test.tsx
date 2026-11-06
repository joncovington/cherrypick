import { describe, expect, it } from "vitest";
import type { ManageTrade } from "@console/shared";
import { earningsWords } from "../src/pages/Live/PmccCards";
import { captureText } from "../src/pages/Live/PositionCards";
import { ledgerHeadWords } from "../src/pages/Live/LedgerDesk";
import { thetaBandWords } from "../src/pages/Live/LiveMarks";

describe("the PMCC short's earnings line", () => {
  it("says a report already out as such, never as after the expiry", () => {
    const words = earningsWords({ date: "2026-09-30", inside: false, past: true, why: "last report 2026-09-30, already out" });
    expect(words).toBe("2026-09-30: already reported; the next date is not known yet");
    expect(words).not.toContain("after it expires");
  });

  it("keeps inside and after for a report still to come", () => {
    expect(earningsWords({ date: "2026-10-27", inside: true, past: false, why: null })).toBe("2026-10-27: inside its life");
    expect(earningsWords({ date: "2026-12-01", inside: false, past: false, why: null })).toBe("2026-12-01: after it expires");
  });

  it("reads an evaluation from before manage said, and no date, as it did", () => {
    expect(earningsWords({ date: "2026-12-01", inside: false, past: null, why: null })).toBe("2026-12-01: after it expires");
    expect(earningsWords({ date: null, inside: null, past: null, why: "no report date in the snapshot" })).toBe(
      "no report date in the snapshot",
    );
  });
});

describe("a position card's capture", () => {
  const trade = (kind: "credit" | "debit", capture: number | null) => ({ kind, capture }) as ManageTrade;

  it("says it is before fees, beside the card's net P&L", () => {
    expect(captureText(trade("credit", 0.227))).toBe("+23% of the credit kept, before fees");
    expect(captureText(trade("debit", 0.18))).toBe("+18% on the debit, before fees");
    expect(captureText(trade("credit", null))).toBeNull();
  });
});

describe("the wheel ledger's head line", () => {
  it("states the row count and start as the account's, not the names'", () => {
    const words = ledgerHeadWords({ scope: ["XYZ"], historyFrom: "2026-05-13", rows: 1200 });
    expect(words).toBe("XYZ · read from the account's 1200 history rows since 2026-05-13 · by closing day");
    expect(ledgerHeadWords({ scope: null, historyFrom: null, rows: null })).toBe(
      "read from the account's ? history rows since ? · by closing day",
    );
  });
});

describe("the live greeks' theta band", () => {
  it("is in the row's dollars, the band times net liq as manage's limits row states it", () => {
    expect(thetaBandWords({ low: 0.001, high: 0.003 }, 20_000)).toBe("+$20.00 to +$60.00 (0.10% to 0.30% of net liq)");
  });

  it("falls back to the share without a net liq", () => {
    expect(thetaBandWords({ low: 0.001, high: 0.003 }, null)).toBe("0.10% to 0.30% of net liq");
  });
});
