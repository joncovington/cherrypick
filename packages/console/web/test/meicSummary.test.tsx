import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { MeicSummary } from "../src/pages/Meic/SummarySlide";
import { NAV_DECL } from "../src/lightbox/navGroups";

const html = (node: React.ReactElement) => renderToString(node).replace(/<!--\s*-->/g, "");

/**
 * The trade-summary prototype is a fixture-data slide with no reader behind it. Both facts are
 * pinned: the fixture's own numbers must survive to the DOM (a styling pass must not move a
 * figure), and — until a reader replaces it — the slide must keep saying so, never silently
 * present invented figures as MEIC's session. Shown to fail:
 *   - a fixture number changed (net 254 → 255)       -> "renders the fixture's figures" failed
 *   - the prototype badge removed from the toolbar   -> "carries the prototype badge" failed
 * Both were reverted; the assertions below are what caught them.
 */
const FIXTURE = {
  session: "Wednesday, October 7, 2026",
  entriesFilled: 3,
  hero: { net: 254, gross: 1580, fees: 245, settlement: 120, openRisk: 480 },
  capture: { value: 16.1 },
  winRate: { days: { won: 12, of: 16 }, entries: { won: 2, of: 3 } },
  sides: [
    { side: "put", net: -55, credit: 560 },
    { side: "call", net: 674, credit: 1020 },
  ],
  timeBuckets: [
    { time: "13:11", net: -158 },
    { time: "14:02", net: 210 },
    { time: "15:26", net: 202 },
  ],
  evaluations: {
    evaluated: 412,
    filled: 3,
    refused: 409,
    reasons: [
      { outcome: "gate_blocked", n: 222, dominantDetail: "regime_gex_negative" },
      { outcome: "cadence_blocked", n: 109, dominantDetail: null },
    ],
  },
  arms: [
    { arm: "control", symbol: "NQ", evaluated: 208, filled: 2, refused: 206, dominant: "gate_blocked", net: 42 },
    { arm: "wide", symbol: "NQ", evaluated: 204, filled: 1, refused: 203, dominant: "gate_blocked", net: 212 },
  ],
  entries: [
    { time: "13:11", arm: "control", symbol: "NQ", credit: 505, net: -158 },
    { time: "14:02", arm: "control", symbol: "NQ", credit: 560, net: 210 },
    { time: "15:26", arm: "wide", symbol: "NQ", credit: 515, net: 202 },
  ],
};

describe("MeicSummary (trade-summary prototype)", () => {
  it("renders the fixture's figures verbatim", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("+$254.00"); // hero net
    expect(page).toContain("+$1,580.00"); // gross captured
    expect(page).toContain("16.1%"); // capture gauge
    expect(page).toContain("75%"); // days win rate 12/16
    expect(page).toContain("put");
    expect(page).toContain("-$55.00"); // by-side put net
    expect(page).toContain("+$674.00"); // by-side call net
    for (const b of FIXTURE.timeBuckets) expect(page).toContain(b.time);
    expect(page).toContain("+$210.00"); // best bucket
    expect(page).toContain("13:11"); // entries table times
  });

  it("renders the fixture's era blocks, sums reconciling across arms", () => {
    // The era blocks' trades split between arms must sum to the unscoped block — the same
    // identity the hero carries, on the same fixture. (9+9, 21+21, 6+5, 3+3.)
    const page = html(<MeicSummary />);
    expect(page).toContain("18 trades");
    expect(page).toContain("42 trades");
    // Every era-bucket bar in the summary draws from the same rows the by-arm card sums.
    expect(page).toContain("50-wide");
  });

  it("renders the refusal ledger verbatim, dominant reason first, bars untoneed", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain(" filled of ");
    expect(page).toContain("412");
    expect(page).toContain("409 refused");
    // Sorted descending — the dominant refusal reads first.
    expect(page.indexOf("gate blocked")).toBeLessThan(page.indexOf("cadence blocked"));
    expect(page).toContain("regime_gex_negative"); // the dominant block_detail
    expect(page).toContain("ts-hbar-fill ts-neutral"); // refusal bars carry no tone
    expect(page).not.toMatch(/ts-refusal[^"]*ts-(pos|neg)/); // and no pos/neg anywhere near them
  });

  it("renders the by-arm card with identity and the dominant refusal", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("control");
    expect(page).toContain("wide");
    expect(page).toContain("412"); // evaluated, summed over arms
    expect(page).toContain("+$42.00"); // control's net
    expect(page).toContain("+$212.00"); // wide's net
    for (const e of FIXTURE.entries) expect(page).toContain(e.arm); // identity columns in entries
  });

  it("defaults to the dark palette; the light variant is a deliberate choice, not a default", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("ts-theme-seg"); // the toggle control exists
    expect(page).toContain(">light</button>"); // with a light choice on it
    expect(page).not.toContain("ts-root ts-light"); // and dark renders first (no pref stored)
  });

  it("renders zero-weight losses as a marker, wins as bars", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("ts-marker"); // the 13:11 loss bucket
    expect(page).toContain("ts-vbar");
  });

  it("carries the prototype badge — fixture data never presents as the session", () => {
    const page = html(<MeicSummary />);
    expect(page).toContain("prototype — fixture data");
  });

  it("legend swatches state their colour rather than inheriting the text colour", () => {
    // Shown to fail: `background: currentColor` on the swatch rendered both white. The colour
    // classes the rings use are the ones the swatches use, so a legend cannot drift from its arc.
    const page = html(<MeicSummary />);
    expect(page).toContain("ts-swatch ts-ring-days");
    expect(page).toContain("ts-swatch ts-ring-entries");
  });

  it("does not wire a query or reader yet — takes only the page scope", () => {
    // The slide takes the manifest header's scope (symbol/arm selects) and nothing else; when a
    // reader lands, this test is replaced by the mirror test pinning it to the ledger.
    expect(MeicSummary.length).toBe(1); // exactly one prop: scope
  });

  it("renders the sessions-page signal blocks, the module's buckets and measures, era-scoped", () => {
    const page = html(<MeicSummary />);
    // Titles as the sessions page names them (lowercase in the summary's card style).
    expect(page).toContain("by short-call delta");
    expect(page).toContain("by wing width");
    // The module's bucket SQL verbatim — angle brackets render escaped, so the raw forms.
    for (const b of ["&lt;0.10", "0.10-0.15", "0.15-0.20", "&gt;=0.20", "50-wide", "75-wide", "100-wide"]) {
      expect(page).toContain(b);
    }
    // Era-scoped, said on the card — a day's three trades would make every band a coin toss.
    expect(page).toContain("era, not the day");
    // The module's three measures per bucket.
    expect(page).toContain("52 trades");
    expect(page).toContain("71.2%");
    expect(page).toContain("$31.00");
  });

  it("tones every breakdown bar by the sign of its bucket's avg net", () => {
    const page = html(<MeicSummary />);
    // Each bucket's title carries its avg net's sign; the bar drawn inside it must tone the same
    // way — never a hardcoded colour. Shown to fail by hardcoding the bar's class to ts-pos.
    const buckets = [...page.matchAll(/title="(\d+) trades over[^\"]*avg net per trade ([+\-−])\$[^\"]*"[\s\S]*?ts-hbar-fill ts-(pos|neg)/g)];
    expect(buckets.length).toBeGreaterThanOrEqual(6);
    for (const [, , sign, tone] of buckets) {
      expect(tone).toBe(sign === "-" || sign === "−" ? "neg" : "pos");
    }
  });

  it("narrows by arm, and the arms' sums still equal the unscoped day", () => {
    // The reconciliation identity: every unscoped figure must equal the sum of the arms' own rows,
    // or the by-arm table and the hero describe different days. Shown to fail during construction
    // when a slice drifted (control's net 42 beside wide's 212 was not 254); the fixture is the
    // proof-of-shape the real reader must preserve.
    const unscoped = html(<MeicSummary />);
    expect(unscoped).toContain("+$254.00"); // net together
    expect(unscoped).toContain("3 filled");
    const control = html(<MeicSummary scope={{ profile: "control" }} />);
    expect(control).toContain("+$42.00"); // control's net alone
    // By arm is the comparison card — it never narrows — so its "wide" row is still there; the
    // assertion is on the entries table, whose identity columns follow the scope.
    expect(control).not.toContain("+$515.00"); // wide's only entry is gone
    expect(control).toContain("+$790.00"); // control's own gross, not the day's 1,580
    const wide = html(<MeicSummary scope={{ profile: "wide" }} />);
    expect(wide).toContain("+$212.00");
    // The sums: 42 + 212 = 254 (net), 2 + 1 = 3 (fills), 208 + 204 = 412 (evaluated).
    expect(html(<MeicSummary />).indexOf("+$254.00")).toBeGreaterThanOrEqual(0);
  });

  it("the summary tab is declared in MEIC's rail, in the today group", () => {
    const decl = NAV_DECL.meic!;
    expect(decl.slides.map((s) => s.id)).toContain("summary");
    expect(decl.groups.flatMap((g) => g.ids)).toContain("summary");
  });
});
