/**
 * The supervisor spawns the console with stdout on DEVNULL, so this file is the only record of why
 * a restart happened. It has to survive the two things that would quietly cost it: unbounded growth
 * on a machine that never reboots, and a write error taking the server down with it.
 */
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { rotateIfLarge, KEEP_BACKUPS, installFatalHandlers } from "../src/logging.js";

describe("fatal handlers", () => {
  // The console died with exit 1 and no record on 15 days; these pin that a fatal error is written
  // down AND still ends the process, for both ways node reports one.
  for (const [event, payload] of [
    ["uncaughtException", new Error("boom-sync")],
    ["unhandledRejection", new Error("boom-async")],
  ] as const) {
    it(`logs the stack and exits 1 on ${event}`, () => {
      const proc = new EventEmitter();
      const lines: string[] = [];
      const exits: number[] = [];
      installFatalHandlers(proc, (m) => lines.push(m), (c) => exits.push(c));
      proc.emit(event, payload);
      expect(lines).toHaveLength(1);
      expect(lines[0]).toContain(`fatal ${event}`);
      expect(lines[0]).toContain(payload.message);
      expect(lines[0]).toContain("logging.test"); // the stack, not just the message
      expect(exits).toEqual([1]);
    });
  }

  it("still exits when the logger itself throws", () => {
    const proc = new EventEmitter();
    const exits: number[] = [];
    installFatalHandlers(proc, () => { throw new Error("log down"); }, (c) => exits.push(c));
    proc.emit("unhandledRejection", "a bare string reason");
    expect(exits).toEqual([1]);
  });
});

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
