import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BwbLiveGap, BwbVolEvidence, ImpliedVarEvidence, QuoteHistoryCoverage } from "@console/shared";
import { BwbLiveGapSlide } from "../src/pages/Bwb/LiveGapSlide";
import { BwbVolSlide } from "../src/pages/Bwb/VolSlide";
import { DataEvidenceCards } from "../src/pages/System/DataEvidenceCards";

/**
 * bwb's live-vs-paper and entry-vol pages and System › data's evidence cards render the modules'
 * reads as given. The live ledger is empty today, so the empty state must say so and still list the
 * gate-named refusals; a lower-bound implied vol carries its "≥"; an unmeasured row is never given
 * a side; an impliedvar refusal is shown as its reason.
 */

function render(entries: Array<[unknown[], unknown]>, el: ReactElement): string {
  const client = new QueryClient();
  for (const [key, value] of entries) client.setQueryData(key, value);
  return renderToString(<QueryClientProvider client={client}>{el}</QueryClientProvider>);
}

describe("the bwb live vs paper slide", () => {
  it("states an empty live ledger honestly and names a refusal's gate", () => {
    const gap: BwbLiveGap = {
      ok: true,
      ledger: "ok",
      arm: "bounce",
      sessions: [],
      refusals: { regime_inverted: { attempts: 4, sessions: 1, first: "2026-10-13", last: "2026-10-13", gate: "live.vix9d_vix_max" } },
    };
    const html = render([[["bridged", "bwb-live-gap"], { ok: true, data: gap, error: null }]], <BwbLiveGapSlide />);
    expect(html).toContain("has taken no position yet");
    expect(html).toContain("regime_inverted");
    expect(html).toContain("live.vix9d_vix_max");
  });
});

describe("the bwb entry vol slide", () => {
  it("marks a cut-off strip as a lower bound and keeps an unmeasured row unsided", () => {
    const row = {
      position_id: "SPX:bounce:2026-10-12",
      arm: "bounce",
      entry_session: "2026-10-12",
      expiration: "2026-10-16",
      status: "open",
      entry_vix9d: 12,
      entry_vix9d_age: 9.6,
      entry_vix: 15.28,
      entry_vix_age: 10.8,
      entry_vix3m: 17.91,
      entry_vix3m_age: 10.8,
      entry_regime_reason: null,
      vix9d_vix: 0.7853,
      vix_vix3m: 0.8532,
      gate_state: "open" as const,
      entry_iv_vol: 11.429,
      entry_iv_complete: 0,
      entry_iv_reason: null,
      net_pnl: null,
    };
    const vol: BwbVolEvidence = {
      entryContext: {
        ok: true,
        data: { ok: true, ledger: "ok", positions: [row, { ...row, position_id: "b", entry_vix9d: null, vix9d_vix: null, gate_state: "unmeasured", entry_regime_reason: "VIX9D:stale" }] },
        error: null,
      },
      regimeSplit: {
        ok: true,
        data: { ok: true, ledger: "ok", gate: "VIX9D/VIX < 1 at entry", arms: { control: { unmeasured: { positions: 3, net_pnl: 12.5, fired: 0, net_per_trade: 4.17, first_session: "2026-09-16", last_session: "2026-10-08" } } } },
        error: null,
      },
      ivPremium: { ok: false, data: null, error: "bwb evidence unavailable — exit 1" },
    };
    const html = render([[["bridged", "bwb-vol"], vol]], <BwbVolSlide />);
    expect(html).toContain("≥ 11.43");
    expect(html).toContain("unmeasured");
    expect(html).toContain("VIX9D:stale");
    expect(html).toContain("$12.50");
    expect(html).toContain("bwb evidence unavailable");
  });
});

describe("System › data evidence", () => {
  it("draws a missing session as missing and an impliedvar refusal as its reason", () => {
    const coverage: QuoteHistoryCoverage = {
      ok: true,
      db: "gex_history.db",
      exists: true,
      recorded_from: "2026-10-13",
      since: "2026-10-13",
      until: "2026-10-14",
      gap_seconds: 60,
      symbols: {
        SPX: {
          recorded: 1,
          missing: ["2026-10-14"],
          sessions: [
            { session: "2026-10-13", recorded: true, samples: 1560, first: "09:30:05", last: "16:00:00", gaps: [{ from: "11:00:00", to: "11:04:00", seconds: 240 }], max_gap_s: 240, no_spot: 2, no_spot_reasons: { spot_stale: 2 }, strikes_median: 80 },
            { session: "2026-10-14", recorded: false, samples: 0, first: null, last: null, gaps: [], max_gap_s: null, no_spot: 0, no_spot_reasons: {}, strikes_median: null },
          ],
        },
      },
    };
    const iv: ImpliedVarEvidence = {
      checks: {
        VIX: { ok: true, data: { ok: false, index: "VIX", days: 30, as_of: 0, published: 15.28, published_at: 0, reason: "not_bracketed", offered: [["2026-10-16", 4]] }, error: null },
      },
    };
    const html = render(
      [
        [["bridged", "quote-history"], { ok: true, data: coverage, error: null }],
        [["bridged", "impliedvar"], iv],
      ],
      <DataEvidenceCards />,
    );
    expect(html).toContain("1<!-- --> of <!-- -->2<!-- --> sessions recorded");
    expect(html).toContain("missing <!-- -->2026-10-14");
    expect(html).toContain("1560");
    expect(html).toContain("not_bracketed");
  });
});
