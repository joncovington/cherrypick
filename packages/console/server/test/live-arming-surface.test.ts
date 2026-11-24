import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/**
 * The Live page's arm switches hold the console to one narrow line. The console has exactly one place
 * that can start a live loop's arm or disarm, it uses the person's command and never the agent's, and
 * nothing in the arm route can touch the halt flag or an order. A source scan, in the manner of
 * `dry-run-only.test.ts`: it asserts that nothing has grown back.
 */

const SRC = path.resolve(__dirname, "..", "src");
const WEB = path.resolve(__dirname, "..", "..", "web", "src");

function allTs(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? allTs(p) : e.name.endsWith(".ts") || e.name.endsWith(".tsx") ? [p] : [];
  });
}

const read = (p: string) => fs.readFileSync(p, "utf-8");
/** Source with its comments blanked, so a sentence that names a verb is not mistaken for code that runs it. */
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const rel = (f: string) => path.relative(SRC, f).replace(/\\/g, "/");

describe("the console's arm and disarm verbs", () => {
  const files = allTs(SRC);

  it("are named in one file, and the agent's verb in none", () => {
    for (const verb of ["--confirm", "--disarm"]) {
      const hits = files.filter((f) => code(read(f)).includes(`"${verb}"`)).map(rel);
      expect(hits, verb).toEqual(["services/liveArmingBridge.ts"]);
    }
    for (const verb of ["--install-task", "--uninstall-task"]) {
      expect(files.filter((f) => code(read(f)).includes(verb)).map(rel), verb).toEqual([]);
    }
  });

  it("run the arm command from the attempt runner and from the disarm route, nowhere else", () => {
    expect(files.filter((f) => /\brunArm\(/.test(code(read(f)))).map(rel).sort()).toEqual(["services/liveArmAttempts.ts", "services/liveArmingBridge.ts"]);
    expect(files.filter((f) => /\brunDisarm\(/.test(code(read(f)))).map(rel).sort()).toEqual(["routes/liveArming.ts", "services/liveArmingBridge.ts"]);
  });

  it("never reach the halt flag from the arm route, its bridge or its attempts", () => {
    for (const f of ["routes/liveArming.ts", "services/liveArmingBridge.ts", "services/liveArmAttempts.ts"]) {
      const text = code(read(path.join(SRC, f)));
      for (const bad of [/set_halt/, /halt-live/, /callConfigCli/, /configBridge/, /unlink/, /rmSync/, /\.rm\(/, /createOrder|placeOrder|cancelOrder/]) {
        expect(bad.test(text), `${f} matches ${String(bad)}`).toBe(false);
      }
    }
  });

  it("the arm route answers only the literal word", () => {
    const text = code(read(path.join(SRC, "routes", "liveArming.ts")));
    expect(text).toContain('body["confirm"] !== "YES"');
    expect(/toUpperCase|toLowerCase|\.trim\(\)/.test(text)).toBe(false);
  });
});

describe("the page", () => {
  it("holds no arming logic of its own: it posts to the console's routes and nowhere else", () => {
    const hits = allTs(WEB).filter((f) => /--confirm|--disarm|install-task|arm_record/.test(code(read(f))));
    expect(hits.map((f) => path.relative(WEB, f).replace(/\\/g, "/"))).toEqual([]);
  });

  it("never hard-codes the disclaimer", () => {
    const hits = allTs(WEB).filter((f) => /NOT FINANCIAL ADVICE|REAL, irreversible/i.test(read(f)));
    expect(hits.map((f) => path.relative(WEB, f).replace(/\\/g, "/"))).toEqual([]);
  });
});
