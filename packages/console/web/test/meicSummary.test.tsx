import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { NAV_DECL } from "../src/lightbox/navGroups";
import type { MeicSummarySlice } from "@console/shared";

/**
 * The trade-summary slide reads `useMeicSummary` — the one-read endpoint (readers/meic.ts, "The
 * summary slide's one read"). The tests mock that hook (the chartPage pattern: no jsdom here, so
 * renderToString over a mocked query hook) and pin what the CONTRACT promises:
 *
 *   - the payload's own figures survive to the DOM verbatim (a styling pass must not move a
 *     number, and the slide must not re-derive money the reader already computed);
 *   - a card whose slice is empty says "no rows in this window" — never a zero, which would read
 *     as "traded and broke even";
 *   - the prototype badge is GONE: the fixture phase is over, and a badge claiming fixture data
 *     over real figures would be its own lie. Shown to fail during the wiring when the badge was
 *     still rendered above real numbers.
 */

const { useMeicSummaryMock } = vi.hoisted(() => ({ useMeicSummaryMock: vi.fn() }));
vi.mock("../src/lib/api", () => ({
  useMeicSummary: useMeicSummaryMock,
}));

const { MeicSummary } = await import("../src/pages/Meic/SummarySlide");

const html = (node: React.ReactElement) =>
  renderToString(node).replace(/<!--\s*-->/g, "").replace(/&#x27;/g, "'");

/** The page's TEXT as a reader sees it — tags stripped, for assertions that span inline elements.
 *  Takes the RENDERED string (from html() or render()), not an element. */
const text = (page: string) => page.replace(/<[^>]+>/g, "");

/** The slide reads ?range= off the URL now — render inside a router with the URL stated. */
function render(url: string, node: React.ReactElement): string {
  return html(
    <MemoryRouter initialEntries={[url]}>
      {node}
    </MemoryRouter>,
  );
}

const PROPS = {
  mode: "paper" as const,
  day: null,
  onDayStep: () => {},
  canStepDay: true,
};

function slice(over: Partial<MeicSummarySlice> = {}): MeicSummarySlice {
  return {
    mode: "paper",
    window: { from: "2026-10-09", to: "2026-10-09", label: "day" },
    era: null,
    hero: {
      trades: 10,
      sessions: 1,
      gross: 1938.75,
      fees: 512.87,
      settlement: 24,
      slippage: 0,
      net: 1401.88,
      credit: 1938.75,
      winPct: 80,
      wins: 8,
      daysWon: 1,
      daysLost: 0,
      openTrades: 0,
      openRisk: 0,
    },
    capturePct: 50,
    captureRawPct: 72.3,
    legs: [
      { side: "put", credit: 1200, expired: 9, stopped: 1, settled: 0, other: 0, stopCost: 150 },
      { side: "call", credit: 738.75, expired: 10, stopped: 0, settled: 0, other: 0, stopCost: 0 },
    ],
    pairs: { trades: 10, clean: 9, cleanPct: 90, doubleStopPct: 0, breakevenBarPct: 26.5 },
    entryTime: [
      { hour: "9", label: "09:00-10:00", offSession: false, net: 900, count: 6 },
      { hour: "10", label: "10:00-11:00", offSession: false, net: 501.88, count: 4 },
    ],
    evaluations: {
      evaluated: 772,
      filled: 10,
      refused: 762,
      outcomes: [
        { outcome: "window_blocked", n: 640, dominantDetail: "outside_entry_window" },
        { outcome: "gate_blocked", n: 80, dominantDetail: "max_open_bp_reached" },
        { outcome: "cadence_blocked", n: 42, dominantDetail: "entry_spacing_wait" },
      ],
      market: { gexNegPct: 61.4, medianIvRank: 0.38, quality: "normal" },
    },
    arms: [
      { arm: "control", symbol: "SPX", evaluated: 386, filled: 9, refused: 377, dominant: "window_blocked", net: 1231.27 },
      { arm: "bp-1000", symbol: "SPX", evaluated: 386, filled: 1, refused: 385, dominant: "gate_blocked", net: 170.61 },
    ],
    delta: [
      { bucket: "0.10-0.15", trades: 4, winPct: 75, avgNet: 310.5 },
      { bucket: "0.15-0.20", trades: 6, winPct: 83.3, avgNet: 32.1 },
    ],
    wing: [{ bucket: "50-wide", trades: 10, winPct: 80, avgNet: 140.2 }],
    weekday: [{ bucket: "Fri", trades: 10, winPct: 80, avgNet: 140.2 }],
    entries: {
      rows: [
        { time: "15:26", arm: "bp-1000", symbol: "SPX", credit: 515, structure: "expired OTM", net: 202, provisional: false },
        { time: "09:46", arm: "control", symbol: "SPX", credit: 505, structure: "stopped/closed", net: -158, provisional: false },
      ],
      total: 10,
    },
    ...over,
  };
}

beforeEach(() => {
  useMeicSummaryMock.mockReset();
  useMeicSummaryMock.mockReturnValue({ data: slice(), isLoading: false, isError: false, error: null });
});

describe("MeicSummary (trade summary, the one-read slide)", () => {
  it("renders the payload's figures verbatim — no re-derivation", () => {
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).toContain("+$1,401.88"); // hero net, whole-position dollars
    expect(page).toContain("+$1,938.75"); // gross captured
    expect(page).toContain("72.3%"); // capture label: the UNclamped value
    expect(page).toContain("sessions 1/1"); // days arc
    expect(page).toContain("trades 8/10"); // trades arc
    expect(page).toContain("80.0%"); // capture... no — the gauge value; and win% rows
    expect(page).toContain("credit +$1,200.00"); // put wing's credit sold
    expect(page).toContain("clean 90.0%");
    expect(page).toContain("double-stop 0.0%");
    expect(page).toContain("fee bar 26.5% of credit");
  });

  it("labels the window the reader states — the day's session by name, wider ranges verbatim", () => {
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).toContain("Friday, October 9, 2026"); // 2026-10-09, the day window
    useMeicSummaryMock.mockReturnValue({
      data: slice({ window: { from: "2026-10-05", to: null, label: "week of 2026-10-05" } }),
      isLoading: false,
      isError: false,
      error: null,
    });
    const week = render("/", <MeicSummary {...PROPS} />);
    expect(week).toContain("week of 2026-10-05");
  });

  it("keeps the refusal ledger honest: payload order, dominant detail, neutral bars", () => {
    const page = text(render("/", <MeicSummary {...PROPS} />));
    expect(page).toContain("10 filled of 772 evaluated");
    expect(page).toContain("762 refused");
    expect(page).toContain("outside_entry_window"); // dominant detail carried, not re-derived
    expect(page).toContain("at refusal: GEX− 61% of refusals · median IV rank 0.38 · session quality normal");
  });

  it("by arm carries identity and the dominant refusal; net is the reader's own", () => {
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).toContain("+$1,231.27");
    expect(page).toContain("+$170.61");
    expect(page).toContain("window blocked");
    // The comparison card states that it does not narrow.
    expect(page).toContain("the comparison card does not narrow");
  });

  it("a missing payload says so for the whole slide — never a wall of zeros", () => {
    useMeicSummaryMock.mockReturnValue({ data: null, isLoading: false, isError: false, error: null });
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).toContain("no rows in this window");
    expect(page).not.toContain("+$");
    expect(page).not.toContain("net p&l");
  });

  it("an error page reads as a failure, not as an empty window", () => {
    useMeicSummaryMock.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error("HTTP 503") });
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).toContain("the summary read failed");
    expect(page).toContain("HTTP 503");
    expect(page).not.toContain("no rows in this window");
  });

  it("the range selection reaches the query, and the slide keeps the day contract", () => {
    // Render once and inspect the query args the hook received.
    render("/", <MeicSummary {...PROPS} />);
    const arg = useMeicSummaryMock.mock.calls[0][0];
    expect(arg.range).toBe("day");
    expect(arg.mode).toBe("paper");
    expect(arg.day).toBeNull();
    expect(arg.symbol).toBeNull();
    expect(arg.profile).toBeNull();
  });

  it("the range rides the URL — ?range=week selects the week segment and reaches the query", () => {
    const page = render("/?range=week", <MeicSummary {...PROPS} />);
    expect(page).toContain('aria-selected="false" class="">day</button>'); // day off
    expect(page).toContain('aria-selected="true" class="ts-seg-on">week</button>'); // week on
    const arg = useMeicSummaryMock.mock.calls[0][0];
    expect(arg.range).toBe("week");
  });

  it("an unrecognised range falls through to the day, like the header's day select", () => {
    render("/?range=fortnight", <MeicSummary {...PROPS} />);
    const arg = useMeicSummaryMock.mock.calls[0][0];
    expect(arg.range).toBe("day");
  });

  it("the prototype badge is gone — real figures must not carry it", () => {
    const page = render("/", <MeicSummary {...PROPS} />);
    expect(page).not.toContain("fixture");
    expect(page).not.toContain("prototype");
  });

  it("nav still pins the summary slide in MEIC's today group", () => {
    // NAV_DECL is a Record keyed by module; the today group's ids carry the slide order.
    const group = NAV_DECL.meic.groups.find((g) => g.label === "today");
    expect(group?.ids).toContain("summary");
  });
});
