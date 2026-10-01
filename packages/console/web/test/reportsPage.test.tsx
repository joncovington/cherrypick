import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { MorningPayload, ReviewPayload } from "@console/shared";

/**
 * The console's first component tests, and they exist because of the failure they catch: a page
 * that throws at render is a BLANK page in the browser, and tsc, the server tests and the
 * production build all pass cleanly while it happens. Nothing rendered a page.
 *
 * Server-rendered rather than DOM-rendered: `renderToString` needs no jsdom and no
 * testing-library, so this costs the package zero new dependencies.
 *
 * **The data hooks are mocked, and that is the point.** An earlier version of this file rendered
 * with live hooks, which under a server render never resolve — so every test only ever exercised
 * the loading state, and a crash in the loaded state (reading a field off an absent block) sailed
 * straight through a green suite into a blank page. A render test that never renders the data is
 * not a render test.
 *
 * **Renders `MorningPage`/`ReviewPage` directly, not through `ReportsLightbox`** (2026-09): the
 * lightbox portals to `document.body` via `createPortal`, which has nowhere to mount under
 * `renderToString` and deliberately renders null there (see `LightboxFrame`'s own comment) — a
 * real render crash inside the report content would be invisible if this test went through that
 * wrapper. `routes.test.tsx` covers that the `/reports` route itself resolves; this file covers
 * that the actual report content survives every shape of missing data, which is the crash class
 * this file exists for.
 */

const PACK_BASE = {
  session: "2026-08-17",
  factVersion: 2,
  generatedAt: "2026-08-17T12:20:00+00:00",
  readings: {
    spx: { value: 7798.99, basis: "prior", session: "2026-08-14", asOf: null, source: "stream_cache:SPX", label: "S&P 500 (SPX)", priorClose: 7744.6, priorChangePct: 0.7 },
  },
  levels: null,
  sectors: null,
  gates: [],
  phase: { phase: "yellow", reason: "one gate unmeasured", gatesTotal: 5, gatesMeasured: 4, gatesMet: 3 },
  calendar: null,
};

const DEPLOYMENT = {
  score: 71.3,
  zone: "full",
  signals: [
    { id: "vix_level", label: "VIX percentile", status: "measured", score: 82.4, value: 14.2, weight: 0.25, detail: "18th percentile" },
    { id: "credit", label: "Credit proxy", status: "unknown", score: null, value: null, weight: 0.15, detail: "too little history" },
  ],
  signalsMeasured: 4,
  signalsTotal: 5,
  weightsRenormalized: true,
  deferred: ["factor_crowding"],
  reason: null,
  note: "a recorded measurement -- feeds no gate, no phase, no sizing",
};

let morning: MorningPayload;
// `era` is not optional to the page — it reads `data.era.sessions` unguarded — and casting
// this fixture through `unknown` is what let an incomplete one compile. Kept minimal but complete.
const review = {
  sessions: [],
  current: null,
  note: null,
  era: { eraFrom: null, eraNote: null, sessions: 0, from: null, to: null },
} as unknown as ReviewPayload;

vi.mock("../src/lib/api", () => ({
  useMorningReport: () => ({ data: morning, isLoading: false, isError: false }),
  useReview: () => ({ data: review, isLoading: false, isError: false }),
}));

const { MorningPage } = await import("../src/pages/Morning/MorningPage");
const { ReviewPage } = await import("../src/pages/Review/ReviewPage");

function setMorning(deployment: unknown): void {
  const current = deployment === "absent" ? { ...PACK_BASE } : { ...PACK_BASE, deployment };
  morning = { sessions: ["2026-08-17"], current, note: null } as unknown as MorningPayload;
}

function renderMorning(): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reports"]}>
        <MorningPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderReview(): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToString(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reports"]}>
        <ReviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("the report pages render", () => {
  it("MorningPage renders the morning report", () => {
    setMorning(DEPLOYMENT);
    expect(renderMorning()).toContain("Morning report");
  });

  it("ReviewPage renders the suite review", () => {
    setMorning(DEPLOYMENT);
    expect(renderReview()).toContain("Suite review");
  });
});

describe("the deployment card survives every shape of missing block", () => {
  it("renders the score and its record-only framing when the block is there", () => {
    setMorning(DEPLOYMENT);
    const html = renderMorning();
    expect(html).toContain("Deployment score");
    expect(html).toContain("71.3");
    expect(html).toContain("record-only");
    expect(html).toContain("FULL DEPLOY");
  });

  it("an unmeasured signal shows an em dash, never a zero contribution", () => {
    setMorning(DEPLOYMENT);
    const html = renderMorning();
    expect(html).toContain("Credit proxy");
    expect(html).toContain("—");
  });

  it("a null block simply omits the card", () => {
    setMorning(null);
    const html = renderMorning();
    expect(html).toContain("Morning report");
    expect(html).not.toContain("Deployment score");
  });

  it("an ABSENT block omits the card too — this is the one that blanked the page", () => {
    // A console process outlives its own rebuilds, so the server answering may predate the field
    // and omit it entirely. `deployment === null` sails past undefined; the first property read
    // then throws and React renders nothing at all.
    setMorning("absent");
    const html = renderMorning();
    expect(html).toContain("Morning report");
    expect(html).not.toContain("Deployment score");
  });

  it("a scoreless block says why instead of showing a number", () => {
    setMorning({ ...DEPLOYMENT, score: null, zone: null, reason: "only 2 of 5 signals measured" });
    expect(renderMorning()).toContain("only 2 of 5 signals measured");
  });
});

/** A futures leg as the reader shapes it; `value: null` is a leg the pack holds but could not measure. */
function future(label: string, value: number | null) {
  return {
    value,
    basis: value === null ? null : "live",
    session: null,
    asOf: null,
    source: null,
    label,
    product: null,
    priorSettle: null,
    priorSettleSession: null,
    changeVsPriorClosePct: null,
    changeReason: value === null ? "no_contract_mapped" : null,
  };
}

const PROXIES = {
  wti_proxy: { value: 71.04, basis: "prior", session: "2026-08-14", asOf: null, source: "stream_cache:USO", label: "WTI crude (ETF proxy)" },
  gold_proxy: { value: 243.18, basis: "prior", session: "2026-08-14", asOf: null, source: "stream_cache:GLD", label: "Gold (ETF proxy)" },
};

function setCommodities(premarket: unknown): string {
  const current = { ...PACK_BASE, readings: { ...PACK_BASE.readings, ...PROXIES }, premarket };
  morning = { sessions: ["2026-08-17"], current, note: null } as unknown as MorningPayload;
  return renderMorning();
}

describe("the scorecard fronts crude and gold as futures", () => {
  it("shows /CL and /GC from the pre-market tape, not the ETF proxies", () => {
    const html = setCommodities({
      futures: { cl: future("WTI crude (/CL)", 61.37), gc: future("Gold (/GC)", 3412.6) },
      indexes: {},
      measuredFutures: 2,
      recordOnly: true,
    });
    expect(html).toContain("WTI crude (/CL)");
    expect(html).toContain("Gold (/GC)");
    expect(html).toContain("3,412.60");
    expect(html).not.toContain("ETF proxy");
  });

  it("falls back to the proxy only for a future the pack does not carry", () => {
    // A pack from before /GC was on the tape: crude is the future, gold is still GLD.
    const html = setCommodities({
      futures: { cl: future("WTI crude (/CL)", 61.37) },
      indexes: {},
      measuredFutures: 1,
      recordOnly: true,
    });
    expect(html).toContain("WTI crude (/CL)");
    expect(html).not.toContain("WTI crude (ETF proxy)");
    expect(html).toContain("Gold (ETF proxy)");
  });

  it("an unmeasured future stays unmeasured rather than borrowing the proxy's number", () => {
    const html = setCommodities({
      futures: { cl: future("WTI crude (/CL)", null), gc: future("Gold (/GC)", null) },
      indexes: {},
      measuredFutures: 0,
      recordOnly: true,
    });
    expect(html).toContain("Gold (/GC)");
    expect(html).not.toContain("243.18");
    expect(html).not.toContain("71.04");
  });

  it("a pack with no tape at all still fronts both proxies", () => {
    const html = setCommodities(undefined);
    expect(html).toContain("WTI crude (ETF proxy)");
    expect(html).toContain("Gold (ETF proxy)");
  });
});
