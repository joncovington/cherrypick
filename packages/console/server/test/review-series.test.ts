import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readReview } from "../src/readers/review.js";

/**
 * The per-session series behind the review sparklines and the Overview calendar strip. Both come
 * out of the SAME pass that computes the era totals, so the guard that matters is that they agree
 * with those totals — a line that disagrees with the number above it is worse than no line. The
 * second guard is the suite series' null-is-not-zero rule: a session whose modules were all
 * unreadable must be ABSENT (a gap in the strip), never present as a flat zero day.
 */

let tmp: string;
let config: ConsoleConfig;

function writeFacts(session: string, modules: Record<string, unknown>): void {
  fs.writeFileSync(
    path.join(tmp, "review", `eod-${session}.json`),
    JSON.stringify({ status: "final", fact_version: 3, modules }),
  );
}

function okModule(net: number, closed: number): Record<string, unknown> {
  return { ok: true, results: { net, closed, gross: net, cost: 0, wins: net > 0 ? closed : 0 } };
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-review-series-"));
  fs.mkdirSync(path.join(tmp, "review"), { recursive: true });
  fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({}));
  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  } as ConsoleConfig;
});

describe("review per-session series", () => {
  it("the module trend sums to the era total shown above it", () => {
    writeFacts("2026-08-20", { meic: okModule(100, 2), flies: okModule(-40, 3) });
    writeFacts("2026-08-21", { meic: okModule(-25, 1), flies: okModule(60, 2) });
    const { era } = readReview(config);
    expect(era.trendByModule["meic"]?.map((t) => t.net)).toEqual([100, -25]);
    const meicSum = (era.trendByModule["meic"] ?? []).reduce((a, t) => a + t.net, 0);
    expect(meicSum).toBeCloseTo(era.netByModule["meic"] as number, 2);
    const fliesSum = (era.trendByModule["flies"] ?? []).reduce((a, t) => a + t.net, 0);
    expect(fliesSum).toBeCloseTo(era.netByModule["flies"] as number, 2);
  });

  it("the suite series sums every readable module per session, in order", () => {
    writeFacts("2026-08-20", { meic: okModule(100, 2), flies: okModule(-40, 3) });
    writeFacts("2026-08-21", { meic: okModule(-25, 1), flies: okModule(60, 2) });
    const { era } = readReview(config);
    expect(era.suiteDaily).toEqual([
      { session: "2026-08-20", net: 60, closed: 5 },
      { session: "2026-08-21", net: 35, closed: 3 },
    ]);
  });

  it("a session with nothing readable is a GAP, never a zero day", () => {
    writeFacts("2026-08-20", { meic: okModule(100, 2) });
    writeFacts("2026-08-21", { meic: { ok: false, reason: "ledger unreadable" } });
    writeFacts("2026-08-24", { meic: okModule(10, 1) });
    const { era } = readReview(config);
    expect(era.suiteDaily.map((d) => d.session)).toEqual(["2026-08-20", "2026-08-24"]);
    // ...and the unreadable session contributes nothing to the module's line either.
    expect(era.trendByModule["meic"]?.map((t) => t.session)).toEqual(["2026-08-20", "2026-08-24"]);
  });

  it("an unreadable module does not suppress the readable ones that session", () => {
    writeFacts("2026-08-20", { meic: okModule(100, 2), flies: { ok: false, reason: "no store" } });
    const { era } = readReview(config);
    expect(era.suiteDaily).toEqual([{ session: "2026-08-20", net: 100, closed: 2 }]);
    expect(era.trendByModule["flies"]).toBeUndefined();
  });
});
