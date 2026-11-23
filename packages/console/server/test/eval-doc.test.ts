import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearEvalDocs, readEvalDoc } from "../src/readers/evalDoc.js";
import { readManageBandsHistory } from "../src/readers/manage.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * The evaluation file is read once per change. Parsing is counted through `readFileSync`, which the
 * reader calls once per parse, so a second read of an unchanged file that does not call it again is a
 * cache hit.
 */
let dir: string;

function write(session: string, doc: unknown, mtime?: number): string {
  const file = path.join(dir, `eval-${session}.json`);
  fs.writeFileSync(file, JSON.stringify(doc), "utf-8");
  if (mtime !== undefined) fs.utimesSync(file, mtime, mtime);
  return file;
}

function reads(spy: ReturnType<typeof vi.spyOn>, session: string): number {
  return spy.mock.calls.filter((c) => String(c[0]).endsWith(`eval-${session}.json`)).length;
}

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "evaldoc-"));
  clearEvalDocs();
});

afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(dir, { recursive: true, force: true });
});

describe("readEvalDoc", () => {
  it("parses an unchanged file once however often it is read", () => {
    write("2026-11-20", { accounts: [{ account: "****1111" }] }, 1_700_000_000);
    const spy = vi.spyOn(fs, "readFileSync");
    const first = readEvalDoc(dir, "2026-11-20");
    const second = readEvalDoc(dir, "2026-11-20");
    expect(first).not.toBeNull();
    expect(second).toBe(first);
    expect(reads(spy, "2026-11-20")).toBe(1);
  });

  it("reads again when the file's mtime changes", () => {
    const file = write("2026-11-20", { n: 1 }, 1_700_000_000);
    expect(readEvalDoc(dir, "2026-11-20")).toEqual({ n: 1 });
    fs.writeFileSync(file, JSON.stringify({ n: 2 }), "utf-8"); // same size
    fs.utimesSync(file, 1_700_000_100, 1_700_000_100);
    const spy = vi.spyOn(fs, "readFileSync");
    expect(readEvalDoc(dir, "2026-11-20")).toEqual({ n: 2 });
    expect(reads(spy, "2026-11-20")).toBe(1);
  });

  it("reads again when the file's size changes", () => {
    const file = write("2026-11-20", { n: 1 }, 1_700_000_000);
    expect(readEvalDoc(dir, "2026-11-20")).toEqual({ n: 1 });
    fs.writeFileSync(file, JSON.stringify({ n: 1, more: "x" }), "utf-8");
    fs.utimesSync(file, 1_700_000_000, 1_700_000_000); // same mtime, new size
    expect(readEvalDoc(dir, "2026-11-20")).toEqual({ n: 1, more: "x" });
  });

  it("does not cache a file it could not parse", () => {
    const file = path.join(dir, "eval-2026-11-20.json");
    fs.writeFileSync(file, '{"accounts": [', "utf-8"); // a truncated write
    expect(readEvalDoc(dir, "2026-11-20")).toBeNull();
    fs.writeFileSync(file, JSON.stringify({ accounts: [] }), "utf-8");
    expect(readEvalDoc(dir, "2026-11-20")).toEqual({ accounts: [] });
  });

  it("is null for a session with no file", () => {
    expect(readEvalDoc(dir, "2026-11-20")).toBeNull();
  });

  it("hands out a frozen object, so a reader cannot change what the next request sees", () => {
    write("2026-11-20", { accounts: [{ account: "****1111" }] });
    const doc = readEvalDoc(dir, "2026-11-20")!;
    expect(Object.isFrozen(doc)).toBe(true);
    expect(Object.isFrozen((doc["accounts"] as unknown[])[0])).toBe(true);
    expect(() => {
      (doc as Record<string, unknown>)["x"] = 1;
    }).toThrow(TypeError);
  });

  it("keeps only the newest few sessions parsed", () => {
    for (let d = 1; d <= 6; d++) write(`2026-11-0${d}`, { d }, 1_700_000_000);
    for (let d = 1; d <= 6; d++) readEvalDoc(dir, `2026-11-0${d}`);
    const spy = vi.spyOn(fs, "readFileSync");
    readEvalDoc(dir, "2026-11-06");
    expect(reads(spy, "2026-11-06")).toBe(0);
    readEvalDoc(dir, "2026-11-01");
    expect(reads(spy, "2026-11-01")).toBe(1);
  });
});

describe("readManageBandsHistory", () => {
  const band = { value: 1, low: 0, high: 2, state: "in" };
  const bandsDoc = (net: number) => ({
    accounts: [{ account: "****1111", portfolio: { net_liq: net, bands: { delta: band } } }],
  });
  const config = () => ({ paths: { manageDir: dir } }) as unknown as ConsoleConfig;

  it("reads each stored evaluation once across polls and again only when one changes", () => {
    write("2026-11-18", bandsDoc(1000), 1_700_000_000);
    const today = write("2026-11-19", bandsDoc(2000), 1_700_000_000);
    const spy = vi.spyOn(fs, "readFileSync");
    const a = readManageBandsHistory(config());
    const b = readManageBandsHistory(config());
    expect(b).toEqual(a);
    expect(a.accounts[0]!.sessions.map((s) => s.netLiq)).toEqual([1000, 2000]);
    expect(reads(spy, "2026-11-18")).toBe(1);
    expect(reads(spy, "2026-11-19")).toBe(1);
    fs.writeFileSync(today, JSON.stringify(bandsDoc(3000)), "utf-8");
    fs.utimesSync(today, 1_700_000_500, 1_700_000_500);
    const c = readManageBandsHistory(config());
    expect(c.accounts[0]!.sessions.map((s) => s.netLiq)).toEqual([1000, 3000]);
    expect(reads(spy, "2026-11-18")).toBe(1);
    expect(reads(spy, "2026-11-19")).toBe(2);
  });
});
