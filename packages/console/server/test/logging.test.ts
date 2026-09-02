/**
 * The supervisor spawns the console with stdout on DEVNULL, so this file is the only record of why
 * a restart happened. It has to survive the two things that would quietly cost it: unbounded growth
 * on a machine that never reboots, and a write error taking the server down with it.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { rotateIfLarge, KEEP_BACKUPS } from "../src/logging.js";

function tmpLog(bytes: number): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-log-test-"));
  const file = path.join(dir, "console.log");
  fs.writeFileSync(file, "x".repeat(bytes));
  return file;
}

describe("log rotation", () => {
  it("leaves a small file alone", () => {
    const file = tmpLog(10);
    rotateIfLarge(file, 1000);
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.existsSync(`${file}.1`)).toBe(false);
  });

  it("shifts the live file to .1 once it passes the cap", () => {
    const file = tmpLog(2000);
    rotateIfLarge(file, 1000);
    expect(fs.existsSync(file)).toBe(false); // the caller appends, recreating it
    expect(fs.statSync(`${file}.1`).size).toBe(2000);
  });

  it("caps the number of backups instead of growing forever", () => {
    const file = tmpLog(2000);
    for (let i = 0; i < KEEP_BACKUPS + 3; i++) {
      fs.writeFileSync(file, "y".repeat(2000));
      rotateIfLarge(file, 1000);
    }
    expect(fs.existsSync(`${file}.${KEEP_BACKUPS}`)).toBe(true);
    expect(fs.existsSync(`${file}.${KEEP_BACKUPS + 1}`)).toBe(false);
  });

  it("is a no-op when there is no log yet", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-log-test-"));
    expect(() => rotateIfLarge(path.join(dir, "nope.log"), 10)).not.toThrow();
  });
});
