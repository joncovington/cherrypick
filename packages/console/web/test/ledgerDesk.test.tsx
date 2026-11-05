import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ManageLedger, ManageLedgerBucket } from "@console/shared";
import { LedgerDesk, ledgerCsv, ledgerWindow, lotLeg } from "../src/pages/Live/LedgerDesk";

/**
 * Live → wheel's ledger draws packages/manage's `ledger` and decides nothing: every figure is manage's
 * for the window shown, never a sum worked out here; the 0DTE legs left out are said with their count
 * and net; the window is the URL's (`?lw=`); a name card opens its spans. Synthetic names and figures.
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

function bucket(over: Partial<ManageLedgerBucket> = {}): ManageLedgerBucket {
  return { puts: 0, calls: 0, shares: 0, dividends: 0, fees: 0, net: 0, legs: 0, ...over };
}

function ledger(over: Partial<ManageLedger> = {}): ManageLedger {
  return {
    error: null,
    historyFrom: "2026-05-13",
    rows: 1200,
    windows: {
      "1M": bucket({ puts: 40, net: 40, legs: 2, fees: -2 }),
      ALL: bucket({ puts: 300, calls: 120, shares: -50, dividends: 12, net: 382, legs: 30, fees: -40 }),
    },
    months: [
      { month: "2026-04", puts: 0, calls: 0, shares: 0, dividends: 0, fees: 0, net: 0 },
      { month: "2026-05", puts: 100, calls: 0, shares: 0, dividends: 0, fees: -5, net: 100 },
      { month: "2026-06", puts: -80, calls: 0, shares: -50, dividends: 0, fees: -5, net: -130 },
    ],
    names: [
      { underlying: "AAA", windows: { ALL: bucket({ puts: 200, calls: 120, net: 320, legs: 9, fees: -10 }), "1M": bucket({ puts: 40, net: 40, legs: 2 }) } },
      { underlying: "BBB", windows: { ALL: bucket({ puts: -20, shares: -50, net: -70, legs: 4, fees: -6 }), "1M": bucket() } },
    ],
    lots: [
      { underlying: "AAA", symbol: "AAA   261120P00045000", right: "P", opened: "2026-10-01", closed: "2026-10-20", how: "bought back", net: 58.5, fees: -2 },
      { underlying: "AAA", symbol: "AAA", right: "D", opened: null, closed: "2026-09-15", how: "dividend", net: 12, fees: 0 },
    ],
    open: [{ underlying: "AAA", symbol: "AAA   261218C00055000", right: "C", opened: "2026-10-22", quantity: -1, net: 79 }],
    excluded: { zeroDte: { legs: 300, net: -2500, underlyings: ["SPX"] }, unmatched: { legs: 0, net: 0 } },
    ...over,
  };
}

function render(l: ManageLedger | null, url = "/live/wheel"): string {
  return text(
    renderToString(
      <MemoryRouter initialEntries={[url]}>
        <LedgerDesk ledger={l} />
      </MemoryRouter>,
    ),
  );
}

describe("the ledger", () => {
  it("shows manage's figures for the window, never a sum worked out here", () => {
    // ALL's net (382) is manage's; its parts add to 382 here, but the 1M net below is 40 with puts 40.
    const out = render(ledger());
    expect(out).toContain("net · ALL +$382.00");
    expect(out).toContain("puts +$300.00");
    expect(out).toContain("30 closed · after -$40.00 in fees");
    // A net that does not equal its parts is still shown as manage wrote it.
    const odd = render(ledger({ windows: { ALL: bucket({ puts: 10, net: 999, legs: 1 }) } }));
    expect(odd).toContain("net · ALL +$999.00");
  });

  it("takes its window from the URL, and lists only the names with something closed in it", () => {
    const out = render(ledger(), "/live/wheel?lw=1M");
    expect(out).toContain("net · 1M +$40.00");
    expect(out).toContain("AAA");
    expect(out).not.toMatch(/BBB/);
    expect(render(ledger())).toMatch(/BBB/);
    expect(ledgerWindow("bogus")).toBe("ALL");
  });

  it("says what it left out with its count and net", () => {
    expect(render(ledger())).toContain("left out: 300 0DTE legs on SPX, net -$2500.00");
    expect(render(ledger({ excluded: { zeroDte: { legs: 0, net: 0, underlyings: ["SPX"] }, unmatched: { legs: 0, net: 0 } } }))).not.toContain("left out");
  });

  it("an evaluation without a ledger draws nothing, and one whose history failed says so", () => {
    expect(render(null)).toBe("");
    expect(render(ledger({ error: "HTTP 503" }))).toContain("ledger: HTTP 503");
  });

  it("names a leg and writes the CSV as manage wrote the spans", () => {
    expect(lotLeg({ symbol: "AAA   261120P00045000", right: "P" })).toBe("2026-11-20 45P");
    expect(lotLeg({ symbol: "AAA", right: "S" })).toBe("shares");
    const csv = ledgerCsv(ledger().lots);
    expect(csv.split("\n")[0]).toBe("closed,opened,underlying,leg,how,net,fees");
    expect(csv).toContain("2026-10-20,2026-10-01,AAA,2026-11-20 45P,bought back,58.50,-2.00");
    expect(csv).toContain("2026-09-15,,AAA,dividend,dividend,12.00,0.00");
  });

  it("draws each name as a card that opens it, with manage's net and parts", () => {
    const html = renderToString(
      <MemoryRouter initialEntries={["/live/wheel"]}>
        <LedgerDesk ledger={ledger()} />
      </MemoryRouter>,
    );
    expect(html).toContain('aria-label="Open AAA&#x27;s ledger"');
    expect(text(html)).toContain("AAA +$320.00 puts +$200.00 · calls +$120.00 9 closed · fees -$10.00");
    expect(html).toMatch(/class="ldg-card is-neg"[^>]*aria-label="Open BBB/);
  });
});
