import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { FIELDS } from "../src/pages/Config/fieldMeta";

/**
 * Every field the Config page offers must edit a key some code actually reads. The page once offered
 * "MEIC loop interval" (`/loop_interval_minutes`), which nothing had read since the supervisor took
 * over the tick cadence: an operator could change it and nothing would happen, which reads as a
 * control that works. The check is `git grep` for the key's last segment in the suite's Python and
 * the console's own TypeScript, leaving out this page's files and tests, which name every key.
 */
const REPO = path.resolve(__dirname, "../../../..");

function readByCode(key: string): boolean {
  try {
    const out = execFileSync(
      "git",
      ["grep", "-l", "-w", key, "--", "*.py", "*.ts", ":!packages/console/web/src/pages/Config/*", ":!*test*", ":!*/tests/*"],
      { cwd: REPO, encoding: "utf8" },
    );
    return out.trim().length > 0;
  } catch {
    return false; // git grep exits 1 when nothing matches
  }
}

describe("the Config page's fields", () => {
  it("each edits a key some code reads", () => {
    // A dynamic field edits `${under}/<arm>${child}` for each arm, so the key code must read is the
    // child's last segment.
    const dead = FIELDS.map((f) => `${f.target}:${f.pointer ?? f.dynamic?.child ?? ""}`).filter((id) => {
      const key = id.split("/").filter(Boolean).pop() ?? "";
      return !/^\d+$/.test(key) && !readByCode(key);
    });
    expect(dead).toEqual([]);
  });
});
