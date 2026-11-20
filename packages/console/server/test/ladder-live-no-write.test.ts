import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/**
 * The ladder's live view reads and never acts. Arming is `/live-ladder-start` (a human's YES in a
 * terminal), so nothing the console serves for the live ladder may arm, disarm, halt, place or change
 * anything: the one process it spawns is `live_loop --status`, the one store it opens is read only, the
 * routes are GETs, and the page has no control. `dry-run-only.test.ts` holds the SDK order calls out of
 * the whole package; this holds the ladder's live surface to its own narrower line.
 */

const SERVER = path.resolve(__dirname, "..");
const SRC = path.join(SERVER, "src");
const WEB = path.resolve(SERVER, "..", "web", "src");

const read = (p: string) => fs.readFileSync(p, "utf-8");
const READER = read(path.join(SRC, "readers", "ladderLive.ts"));
const BRIDGE = read(path.join(SRC, "services", "ladderLiveBridge.ts"));
const PAGE = read(path.join(WEB, "pages", "Ladder", "LadderLive.tsx"));
const ROUTES = read(path.join(SRC, "routes", "modules.ts"));

function allTs(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? allTs(p) : e.name.endsWith(".ts") || e.name.endsWith(".tsx") ? [p] : [];
  });
}

/** Source with its comments blanked, so a sentence that names a verb is not mistaken for code that runs it. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("the ladder's live surface has no write path", () => {
  it("spawns live_loop with --status and with nothing else", () => {
    const argvs = [...code(BRIDGE).matchAll(/spawnModuleCliAsync\(\s*\[([^\]]*)\]/g)].map((m) => m[1]!.replace(/\s+/g, " ").trim());
    expect(argvs).toEqual(['"-m", "cherrypick.ladder.live_loop", "--status"']);
    for (const verb of ["--arm", "--disarm", "--install-task", "--uninstall-task", "--once", "--confirm", "--halt"]) {
      expect(code(BRIDGE).includes(verb), `the bridge names ${verb}`).toBe(false);
    }
  });

  it("is the only place in the console that runs the ladder's live loop", () => {
    const offenders = allTs(SRC)
      .filter((f) => code(read(f)).includes("ladder.live_loop"))
      .map((f) => path.relative(SRC, f).replace(/\\/g, "/"));
    expect(offenders).toEqual(["services/ladderLiveBridge.ts"]);
  });

  it("opens the live ledger read only and issues no statement that changes it", () => {
    expect(READER).toContain("readOnlyDb(");
    for (const bad of [/\bINSERT\b/i, /\bUPDATE\s+\w+\s+SET\b/i, /\bDELETE\s+FROM\b/i, /\bDROP\b/i, /\bALTER\b/i, /\.exec\(/, /writeFile/, /unlink/, /new Database\(/, /readonly:\s*false/]) {
      expect(bad.test(code(READER)), `the reader matches ${String(bad)}`).toBe(false);
    }
    expect(/writeFile|appendFile|unlink|rename|spawn|execFile/.test(code(READER))).toBe(false);
  });

  it("serves every live-ladder route as a GET", () => {
    const lines = ROUTES.split("\n").filter((l) => l.includes("/api/ladder/live"));
    expect(lines.length).toBeGreaterThanOrEqual(2);
    for (const l of lines.filter((x) => /app\.(get|post|put|patch|delete)\(/.test(x))) {
      expect(l, l).toMatch(/app\.get\(/);
    }
  });

  it("gives the page no control: no button, form, input, fetch, mutation or handler", () => {
    for (const bad of [/<button/i, /<form/i, /<input/i, /<select/i, /<textarea/i, /\bfetch\(/, /useMutation/, /postJson|putJson|deleteJson|sendJson/, /\bonClick\b/, /\bonSubmit\b/, /\bonChange\b/, /method:\s*["'](POST|PUT|PATCH|DELETE)/i]) {
      expect(bad.test(code(PAGE)), `the live page matches ${String(bad)}`).toBe(false);
    }
  });

  it("names arming only to say where it is done", () => {
    expect(PAGE).toContain("/live-ladder-start");
    for (const bad of [/\barm(ing)?\s*=/, /setHalt|clearHalt|RESUME LIVE/, /enable_live_trading/]) {
      expect(bad.test(code(PAGE)), String(bad)).toBe(false);
    }
  });
});
