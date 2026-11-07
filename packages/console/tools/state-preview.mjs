#!/usr/bin/env node
/**
 * Shoot every made-up state in `tools/states/` (2026-11-07): the pages as they look in states the
 * live data has not got today, such as an open MEIC condor with its mark or a halted loop. Each
 * scenario runs through `ui-check.mjs --fixture`, which answers the scenario's API paths with its
 * JSON and refuses every write, at each of its widths, and checks its `expect` texts are on the page.
 *
 *   pnpm state-preview                       # every scenario, pictures to the OS temp folder
 *   pnpm state-preview --only meic-live-open --out ./shots
 *
 * The pictures are for looking at locally. They are made-up data on real pages: never post them
 * anywhere a reader could take them for the book.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATES = path.join(HERE, "states");

function parseArgs(argv) {
  const out = { out: path.join(os.tmpdir(), "cherrypick-state-preview"), only: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") out.out = argv[++i];
    else if (argv[i] === "--only") out.only = argv[++i];
    else throw new Error(`unknown argument: ${argv[i]}`);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const names = fs
  .readdirSync(STATES)
  .filter((f) => f.endsWith(".json"))
  .map((f) => f.slice(0, -5))
  .filter((n) => args.only === null || n === args.only);
if (names.length === 0) {
  console.error(`state-preview: no scenario${args.only === null ? "s" : ` named ${args.only}`} in ${STATES}`);
  process.exit(1);
}
fs.mkdirSync(args.out, { recursive: true });

let failed = 0;
for (const name of names) {
  const file = path.join(STATES, `${name}.json`);
  const scenario = JSON.parse(fs.readFileSync(file, "utf-8"));
  for (const width of scenario.widths ?? [1440, 390]) {
    const shot = path.join(args.out, `${name}-${width}.png`);
    const argv = [path.join(HERE, "ui-check.mjs"), "--route", scenario.route, "--fixture", file, "--viewport", `${width}x2400`, "--shot", shot];
    for (const text of scenario.expect ?? []) argv.push("--expect", text);
    const done = spawnSync(process.execPath, argv, { encoding: "utf-8", env: { ...process.env, MSYS_NO_PATHCONV: "1" } });
    const ok = done.status === 0 && fs.existsSync(shot);
    if (!ok) failed++;
    console.log(`${ok ? "ok  " : "FAIL"} ${name} @${width} -> ${shot}`);
    if (!ok) console.log((done.stdout + done.stderr).trim().split(/\r?\n/).slice(-6).map((l) => `     ${l}`).join("\n"));
  }
}
process.exit(failed === 0 ? 0 : 1);
