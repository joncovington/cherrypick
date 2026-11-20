/**
 * `LIVE_PATH_MODULES` in liveLock.ts is a mirror of the orchestrator's `config.LIVE_PATH_MODULES`,
 * which the orchestrator's own test keeps equal to the packages that ship a `live_loop.py`. If the
 * copies drift, the lock hero calls a module that can trade live "paper only · no live path".
 *
 * Asks this checkout's orchestrator (its `src` first on the path, not whatever is installed), and
 * skips visibly when Python cannot answer.
 */

import { spawnSync } from "node:child_process";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { LIVE_PATH_MODULES } from "../src/services/liveLock.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const ORCH_SRC = path.join(REPO, "packages", "orchestrator", "src");

function orchestratorSet(): string[] | null {
  const out = spawnSync(
    "python",
    [
      "-c",
      "import json, sys; sys.path.insert(0, sys.argv[1]); from cherrypick.orchestrator import config; print(json.dumps(sorted(config.LIVE_PATH_MODULES)))",
      ORCH_SRC,
    ],
    { encoding: "utf-8", timeout: 60_000 },
  );
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout.trim().split("\n").pop() ?? "") as string[];
  } catch {
    return null;
  }
}

const theirs = orchestratorSet();

describe.skipIf(theirs === null)("the console's live-path list agrees with the orchestrator's", () => {
  it("names the same modules", () => {
    expect([...LIVE_PATH_MODULES].sort()).toEqual(theirs);
  });
});
