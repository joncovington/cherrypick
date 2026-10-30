import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { CompareDoc, ComparePayload } from "@console/shared";

/**
 * The Compare page, rendered with the loaded document (the hook is mocked, as in
 * `reportsPage.test.tsx`: a render test that only ever sees the loading state is not one).
 *
 * The fixture is the server's (`server/test/fixtures/compare-era.json`): the live era document of
 * 2026-10-12, trimmed to five flies arms (trade lists cut to 12), curve's one advised arm, pmcc with
 * no arm in its era, and an absent and a failed module in the bridge's shape.
 *
 * What is pinned: the sort in the URL changes the row order; a null renders as an em dash and
 * never as `$0.00`; no class on the page is a threshold verdict, and the figures that WOULD be a
 * verdict if toned (interval ends, t bound, p) carry no tone at all; the histogram opens from the
 * URL and refuses under two trades; a failed bridge says why rather than drawing an empty table.
 */

const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(import.meta.dirname, "..", "..", "server", "test", "fixtures", "compare-era.json"), "utf-8"),
) as Record<string, unknown>;
const { ok: _ok, ...DOC } = FIXTURE;

let payload: ComparePayload = { ok: true, scope: "era", data: DOC as unknown as CompareDoc, error: null };

vi.mock("../src/pages/Compare/useCompare", () => ({
  useCompare: () => ({ data: payload, isLoading: false, isError: false }),
}));

const { ComparePage } = await import("../src/pages/Compare/ComparePage");
const { histogramBins } = await import("../src/pages/Compare/NetHistogram");

/** Markup with React's text-node separators removed, so a sentence reads as one string. */
function render(url: string): string {
  return renderToString(
    <MemoryRouter initialEntries={[url]}>
      <ComparePage />
    </MemoryRouter>,
  ).replace(/<!-- -->/g, "");
}

/** One module's section of the markup. */
function section(html: string, module: string): string {
  const start = html.indexOf(`data-module="${module}"`);
  expect(start, `no section for ${module}`).toBeGreaterThan(-1);
  const end = html.indexOf("data-module=", start + 1);
  return html.slice(start, end === -1 ? undefined : end);
}

function armOrder(html: string, module: string): string[] {
  return [...section(html, module).matchAll(/<tr[^>]*data-arm="([^"]+)"/g)].map((m) => m[1] ?? "");
}

/**
 * One arm's cells by column id, the arm opened so its expanded line is in the markup: since
 * 2026-10-30 most statistics sit there, not in the row (docs/console-review-plan.md batch 3).
 */
function cells(module: string, arm: string): Record<string, string> {
  const s = section(render(`/compare?arm=${module}/${arm}`), module);
  const at = s.indexOf(`data-arm="${arm}"`);
  const next = s.indexOf("data-arm=", at + 1);
  const block = s.slice(at, next === -1 ? undefined : next);
  return Object.fromEntries([...block.matchAll(/<td[^>]*data-col="([^"]+)"[^>]*>([\s\S]*?)<\/td>/g)].map((m) => [m[1] ?? "", m[2] ?? ""]));
}

const VS = ["vs_sessions", "vs_mean", "vs_per_entry", "vs_per_risk", "vs_t", "vs_p", "vs_more", "vs_unpaired"];

describe("the arm table", () => {
  it("keeps the bridge's order with no sort", () => {
    expect(armOrder(render("/compare"), "flies")).toEqual([
      "control",
      "debit-first-down",
      "debit-first-up",
      "range-high",
      "advised:miss-stop-90",
    ]);
  });

  it("sorts by the column the URL names, both ways", () => {
    expect(armOrder(render("/compare?sort=net&dir=desc"), "flies")).toEqual([
      "debit-first-up",
      "range-high",
      "control",
      "advised:miss-stop-90",
      "debit-first-down",
    ]);
    expect(armOrder(render("/compare?sort=net&dir=asc"), "flies")).toEqual([
      "debit-first-down",
      "advised:miss-stop-90",
      "control",
      "range-high",
      "debit-first-up",
    ]);
  });

  it("sinks a null to the bottom whichever way it sorts", () => {
    // control has no vs_control; range-high has one paired session and no interval.
    const desc = armOrder(render("/compare?sort=vs_more&dir=desc"), "flies");
    const asc = armOrder(render("/compare?sort=vs_more&dir=asc"), "flies");
    expect(desc.slice(-2).sort()).toEqual(["control", "range-high"]);
    expect(asc.slice(-2).sort()).toEqual(["control", "range-high"]);
  });

  it("marks the sorted header", () => {
    const html = render("/compare?sort=net&dir=asc");
    expect(html).toMatch(/aria-sort="ascending"[^>]*data-sort="net"/);
  });

  it("renders a null as an em dash, never $0.00", () => {
    // control's own row: every vs-control cell is null.
    const control = cells("flies", "control");
    for (const id of VS) expect(control[id]).toContain("—");
    expect(Object.values(control).join("")).not.toContain("$0.00");
    // range-high: one paired session, so no interval, no t bound, no projection.
    const range = Object.values(cells("flies", "range-high")).join("");
    expect(range).toContain('<div class="muted cmp-sub">—</div>');
    expect(range).not.toMatch(/\[\$0\.00, \$0\.00\]/);
  });

  it("states every money header's definition, costs from the bridge's own note", () => {
    const html = render("/compare");
    expect(html).toMatch(/title="costs = every modelled cost[^"]*"[^>]*data-sort="costs"/);
    expect(html).toMatch(/title="gross − costs[^"]*"[^>]*data-sort="net"/);
    // Max drawdown is in the expanded line, under its own header and definition.
    expect(render("/compare?arm=flies/control")).toMatch(/title="maximum drawdown[^"]*per-SESSION[^"]*"/);
  });

  it("says what the intervals are and that the projection is one", () => {
    const html = render("/compare");
    expect(html).toContain("95% session-bootstrap");
    expect(html).toContain("seeded with 20261012");
    expect(html).toContain("projection at the observed mean and spread");
  });
});

describe("no verdicts", () => {
  it("carries no threshold verdict class anywhere on the page", () => {
    const html = render("/compare?arm=flies/debit-first-down");
    const classes = [...html.matchAll(/class="([^"]*)"/g)].flatMap((m) => (m[1] ?? "").split(/\s+/));
    const verdict = classes.filter((c) => /ok|missing|warn|sig|pass|fail|verdict|better|worse|best|winner/i.test(c));
    expect(verdict).toEqual([]);
  });

  it("tones only by sign: interval ends, the t bound and the p carry no tone", () => {
    // advised:miss-stop-90's interval excludes zero ([-159.33, -12.40]) and its t bound is negative:
    // exactly the row a significance colour would paint. Its t bound, sign test, projection and
    // unpaired cells must carry no tone; its mean diff is toned by its sign, its interval is not.
    const c = cells("flies", "advised:miss-stop-90");
    for (const id of ["vs_t", "vs_p", "vs_more", "vs_unpaired"]) expect(c[id]).not.toMatch(/pnl-(pos|neg)/);
    expect(c["vs_mean"]).toMatch(/^<span class="pnl-neg">-\$81\.53<\/span>/);
    expect(c["vs_mean"]).toContain('<div class="muted cmp-sub">[-$159.33, -$12.40]</div>');
  });
});

describe("size-normalised figures", () => {
  it("shows net per entry with entries per session, and a dash where no capital is recorded", () => {
    const c = cells("flies", "advised:miss-stop-90");
    expect(c["per_entry"]).toContain('<span class="pnl-neg">-$23.05</span>');
    expect(c["per_entry"]).toContain('<div class="muted cmp-sub">4.7 / session</div>');
    // flies records no capital per trade: its risk-normalised cells are dashes, never $0.00.
    expect(c["vs_per_risk"]).toMatch(/^<span class="muted">—<\/span>/);
    expect(c["vs_per_risk"]).not.toContain("$0.00");
  });

  it("pairs per entry beside the dollar pair, toned by sign, its interval untoned", () => {
    const c = cells("flies", "advised:miss-stop-90");
    expect(c["vs_per_entry"]).toMatch(/^<span class="pnl-neg">-\$19\.75<\/span>/);
    expect(c["vs_per_entry"]).toContain('<div class="muted cmp-sub">[-$37.37, -$3.43]</div>');
  });

  it("renders a per-$100-risk pair when the ledger records capital", () => {
    const doc = structuredClone(DOC) as unknown as CompareDoc;
    const arm = doc.modules[0]!.arms.find((a) => a.arm === "debit-first-up")!;
    arm.net_per_100_risk = 1.5;
    arm.capital_known_trades = arm.trades - 1;
    arm.vs_control = { ...arm.vs_control!, per_100_risk: { ...arm.vs_control!.per_entry!, mean_diff: 0.42, ci: [-0.1, 0.9] } };
    const saved = payload;
    payload = { ok: true, scope: "era", data: doc, error: null };
    try {
      const c = cells("flies", "debit-first-up");
      expect(c["per_risk"]).toContain('<span class="pnl-pos">+$1.50</span>');
      expect(c["per_risk"]).toContain(`${String(arm.trades - 1)} of ${String(arm.trades)} trades`);
      expect(c["vs_per_risk"]).toMatch(/^<span class="pnl-pos">\+\$0\.42<\/span>/);
      expect(c["vs_per_risk"]).toContain('<div class="muted cmp-sub">[-$0.10, +$0.90]</div>');
    } finally {
      payload = saved;
    }
  });
});

describe("a short row, the rest in the expanded line", () => {
  const rowHeaders = (html: string) => [...section(html, "flies").matchAll(/data-sort="([^"]+)"/g)].map((m) => m[1]);

  it("keeps the money together in the row and every other statistic in the open arm's line", () => {
    const shut = render("/compare");
    expect(rowHeaders(shut)).toEqual(["arm", "trades", "gross", "costs", "net", "per_risk", "vs_sessions", "vs_mean", "vs_t"]);
    expect(Object.keys(cells("flies", "control")).sort()).toEqual(
      ["trades", "gross", "costs", "net", "per_risk", "vs_sessions", "vs_mean", "vs_t", "control", "sessions", "win", "dd", "mean", "per_entry", "more", "vs_per_entry", "vs_per_risk", "vs_p", "vs_more", "vs_unpaired"].sort(),
    );
    // Shut, an arm's statistics beyond the row are not in the markup at all.
    expect(section(shut, "flies")).not.toContain('data-col="dd"');
  });
});

describe("module status and era", () => {
  it("names absent and failed modules with the bridge's reason", () => {
    const html = render("/compare");
    expect(section(html, "contango")).toContain("No ledger on this machine: no paper ledger");
    expect(section(html, "earnings")).toContain("The read failed: OperationalError: no such column: entry_session");
  });

  it("shows the era start, its bounding break and the caveats", () => {
    const flies = section(render("/compare"), "flies");
    expect(flies).toContain("2026-10-05");
    expect(flies).toContain("completion_rule");
    const curve = section(render("/compare"), "curve");
    expect(curve).toContain("spread_width");
    expect(curve).toMatch(/2 caveats/);
  });

  it("an era with no arm yet is a filtered-out result, not an empty table", () => {
    const pmcc = section(render("/compare"), "pmcc");
    expect(pmcc).toContain("No arm has closed a trade inside this era yet");
    expect(pmcc).not.toContain("<table");
  });
});

describe("the per-trade histogram", () => {
  it("opens for the arm the URL names", () => {
    const html = render("/compare?arm=flies/debit-first-down");
    const flies = section(html, "flies");
    expect(flies).toContain('aria-expanded="true"');
    expect(flies).toContain("<svg");
    expect(flies).toMatch(/cmp-hist-(loss|gain)/);
    expect(section(html, "curve")).not.toContain("<svg");
  });

  it("refuses to draw under two trades", () => {
    const curve = section(render("/compare?arm=curve/advised:short-delta-nearer-money"), "curve");
    expect(curve).toContain("One closed trade in scope");
    expect(curve).not.toContain("<svg");
  });

  it("never lets a bin straddle zero, so a bin's tone is a fact about the bin", () => {
    for (const values of [
      [-85.94, -82.81, -64.69, 16.86, 3.2, 0],
      [-1, 1],
      [5, 5, 5],
      [-0.3, 0.1, 0.2, 7],
    ]) {
      const bins = histogramBins(values);
      expect(bins.reduce((s, b) => s + b.count, 0)).toBe(values.length);
      for (const b of bins) expect(b.lo < 0 && b.hi > 0, `${String(b.lo)}..${String(b.hi)}`).toBe(false);
    }
  });
});

describe("a failed bridge", () => {
  it("shows the reason, never an empty table", () => {
    payload = { ok: false, scope: "era", data: null, error: "arm comparison unavailable — No module named cherrypick.core.compare" };
    try {
      const html = render("/compare");
      expect(html).toContain("The comparison could not be read");
      expect(html).toContain("No module named cherrypick.core.compare");
      expect(html).not.toContain("<table");
    } finally {
      payload = { ok: true, scope: "era", data: DOC as unknown as CompareDoc, error: null };
    }
  });
});
