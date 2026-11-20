import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { readPortfolio, readPortfolioSources, setPortfolioCaller } from "../src/readers/portfolio.js";
import { MODULE_ERA } from "../src/readers/performance.js";
import type { ConsoleConfig } from "../src/config.js";
import type { PortfolioReading } from "@console/shared";

/** Compare's portfolio tab: each chosen arm resolved to its module's ledger, schema and era. */

function fakeConfig(): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-portfolio-test-"));
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(orchestratorConfig, JSON.stringify({ data_epoch: { date: "2026-08-21", note: "n" } }));
  const dir = (m: string) => path.join(home, "data", m);
  return {
    port: 0,
    paths: {
      cherrypick: home, streamCacheDb: "", watchdogLast: "", orchestratorConfig, consoleData: "",
      meicDir: dir("meic"), fliesDir: dir("flies"), earningsDir: dir("earnings"), calendarsDir: dir("calendars"),
      pmccDir: dir("pmcc"), curveDir: dir("curve"), bwbDir: dir("bwb"), contangoDir: dir("contango"),
      gexDir: "", reviewDir: "", manageDir: "", overviewDir: "", advisorDir: "", adviceDir: "", meicRiskConfig: "",
      fliesConfig: "", pmccConfigCandidates: [], calendarsConfigCandidates: [], curveConfigCandidates: [],
    },
  };  // fmt: skip
}

const ok = { ok: true, failures: [] } as unknown as PortfolioReading;

afterEach(() => setPortfolioCaller());

describe("readPortfolio", () => {
  it("resolves each arm to its module's ledger, schema and era, over the suite window", async () => {
    const seen: string[][] = [];
    setPortfolioCaller((argv) => {
      seen.push(argv);
      return Promise.resolve(ok);
    });
    await readPortfolio(fakeConfig(), { arms: ["flies:control", "pmcc:shield", "earnings:strat_test:iron_fly"], period: "week", weights: "booked", mode: "paper" });
    const argv = seen[0] as string[];
    const manifest = JSON.parse(argv[argv.indexOf("--manifest") + 1] as string) as Array<Record<string, unknown>>;
    expect(manifest.map((m) => [m["key"], m["schema"], m["tag"], m["era"]])).toEqual([
      ["flies:control", "fly_book", "control", null],
      ["pmcc:shield", "pmcc", "shield", MODULE_ERA.pmcc],
      ["earnings:strat_test:iron_fly", "earnings", "strat_test:iron_fly", null],
    ]);
    expect(String(manifest[0]?.["db"])).toContain(`${path.sep}flies${path.sep}`);
    expect(argv.slice(argv.indexOf("--start"), argv.indexOf("--start") + 2)).toEqual(["--start", "2026-08-21"]);
    expect(argv).toContain("week");
    expect(argv).toContain("booked");
  });

  const listing = () =>
    Promise.resolve({
      ok: true,
      unreadable: [],
      sources: [
        { id: "automations/builder-mine-2611161730", kind: "builder" as const, label: "builder-mine-2611161730", arms: ["mine"],
          window: ["2025-11-17", "2026-11-16"] as [string, string], licensed: false, generated_at: null, path: "/bt/automations/builder-mine-2611161730.json" },
        { id: "results/research/item75/study-all", kind: "study" as const, label: "item 75 study-all", arms: ["iron-condor-1345"],
          window: ["2022-05-16", "2026-11-13"] as [string, string], licensed: true, generated_at: null, path: "/bt/results/research/item75/study-all.json" },
      ],
    });  // fmt: skip

  it("resolves a backtest arm to its listed file, from the earliest chosen run's first session", async () => {
    const seen: string[][] = [];
    setPortfolioCaller((argv) => (seen.push(argv), Promise.resolve(ok)), listing, () => Promise.resolve(true));
    const out = await readPortfolio(fakeConfig(), {
      arms: ["flies:control", "bt:automations/builder-mine-2611161730#mine", "bt:results/research/item75/study-all#iron-condor-1345"],
      period: "day", weights: "equal_risk", mode: "paper",
    });  // fmt: skip
    const argv = seen[0] as string[];
    const manifest = JSON.parse(argv[argv.indexOf("--manifest") + 1] as string) as Array<Record<string, unknown>>;
    expect(manifest.slice(1)).toEqual([
      { key: "bt:automations/builder-mine-2611161730#mine", kind: "backtest", path: "/bt/automations/builder-mine-2611161730.json", arm: "mine", licensed: false },
      { key: "bt:results/research/item75/study-all#iron-condor-1345", kind: "backtest", path: "/bt/results/research/item75/study-all.json", arm: "iron-condor-1345", licensed: true },
    ]);
    expect(argv[argv.indexOf("--start") + 1]).toBe("2022-05-16");
    expect(out.failures).toEqual([]);
  });

  it("never reads a path it was not listed, nor a licensed source while the results gate is closed", async () => {
    const seen: string[][] = [];
    setPortfolioCaller((argv) => (seen.push(argv), Promise.resolve(ok)), listing, () => Promise.resolve(false));
    const out = await readPortfolio(fakeConfig(), {
      arms: ["bt:../../etc/passwd#x", "bt:results/research/item75/study-all#iron-condor-1345", "bt:automations/builder-mine-2611161730#other"],
      period: "day", weights: "equal_risk", mode: "paper",
    });  // fmt: skip
    const argv = seen[0] as string[];
    expect(JSON.parse(argv[argv.indexOf("--manifest") + 1] as string)).toEqual([]);
    expect(out.failures.map((f) => f.key)).toEqual([
      "bt:../../etc/passwd#x",
      "bt:results/research/item75/study-all#iron-condor-1345",
      "bt:automations/builder-mine-2611161730#other",
    ]);
    const listed = await readPortfolioSources();
    expect(listed.sources.map((s) => s.id)).toEqual(["automations/builder-mine-2611161730"]);
    expect(listed.sources[0]).not.toHaveProperty("path");
  });

  it("names an arm it cannot resolve rather than dropping it", async () => {
    setPortfolioCaller(() => Promise.resolve(ok));
    const out = await readPortfolio(fakeConfig(), { arms: ["curve:control", "nope:x"], period: "day", weights: "equal_risk", mode: "live" });
    // curve keeps no live ledger; "nope" is no module.
    expect(out.failures.map((f) => f.key)).toEqual(["curve:control", "nope:x"]);
  });
});
