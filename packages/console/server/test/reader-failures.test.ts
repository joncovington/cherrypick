/**
 * `withReadOnlyDb` returns `fallback` for two different things, and only one of them is fine.
 *
 * "This module has never run here" is legitimate and must stay silent — a fresh machine would
 * otherwise light up with warnings about every module it has not installed. "The query threw" is a
 * defect, and until 2026-08-26 it was indistinguishable from the first: same return value, same HTTP
 * 200, nothing on the page, nothing in the log. Two real defects lived in that gap. `/api/flies/meta`
 * served `{arms: [], dates: [], symbols: []}` off one bad column in a UNION, and a day resolver
 * naming a table an older ledger lacks read as "no latest session", so a tab meant to show one day
 * answered for every day in its era. Both looked healthy.
 *
 * What is NOT changed here is what any caller receives. Separating the two return paths would change
 * semantics at ~65 call sites; this makes the second case observable and leaves the contract alone.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  clearReaderFailures,
  closePooledDbs,
  listReaderFailures,
  setReaderFailureLogger,
  withReadOnlyDb,
} from "../src/readers/db.js";

let tmp: string;
let store: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "reader-fail-"));
  store = path.join(tmp, "paper_trades.db");
  const db = new Database(store);
  db.exec("CREATE TABLE things (id INTEGER, name TEXT); INSERT INTO things VALUES (1, 'a')");
  db.close();
  clearReaderFailures();
  setReaderFailureLogger(undefined);
});

afterEach(() => {
  closePooledDbs();
  clearReaderFailures();
  setReaderFailureLogger(undefined);
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("an absent store", () => {
  it("is not a failure — a module may legitimately never have run here", () => {
    const out = withReadOnlyDb(path.join(tmp, "never-ran.db"), "fallback", () => "read");
    expect(out).toBe("fallback");
    expect(listReaderFailures()).toEqual([]);
  });

  it("does not log", () => {
    const lines: string[] = [];
    setReaderFailureLogger((m) => lines.push(m));
    withReadOnlyDb(path.join(tmp, "never-ran.db"), null, () => "read");
    expect(lines).toEqual([]);
  });
});

describe("a query that throws", () => {
  it("still serves the fallback — the contract at ~65 call sites is unchanged", () => {
    const out = withReadOnlyDb(store, "fallback", (db) => db.prepare("SELECT nope FROM things").get());
    expect(out).toBe("fallback");
  });

  it("is recorded, with the path and the reason", () => {
    withReadOnlyDb(store, null, (db) => db.prepare("SELECT nope FROM things").get());

    const [failure] = listReaderFailures();
    expect(failure.path).toBe(store);
    expect(failure.error).toMatch(/nope/);
    expect(failure.count).toBe(1);
  });

  it("is logged", () => {
    const lines: string[] = [];
    setReaderFailureLogger((m) => lines.push(m));

    withReadOnlyDb(store, null, (db) => db.prepare("SELECT nope FROM things").get());

    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain(store);
    expect(lines[0]).toContain("fallback");
  });

  it("counts repeats but does not log the same error again", () => {
    // The SPA polls several endpoints every few seconds. A wedged reader logging every poll would
    // bury itself, which is the same failure as logging nothing.
    const lines: string[] = [];
    setReaderFailureLogger((m) => lines.push(m));

    for (let i = 0; i < 5; i++) {
      withReadOnlyDb(store, null, (db) => db.prepare("SELECT nope FROM things").get());
    }

    expect(listReaderFailures()[0].count).toBe(5);
    expect(lines).toHaveLength(1);
  });

  it("logs again when the error CHANGES, because that is new information", () => {
    const lines: string[] = [];
    setReaderFailureLogger((m) => lines.push(m));

    withReadOnlyDb(store, null, (db) => db.prepare("SELECT nope FROM things").get());
    withReadOnlyDb(store, null, (db) => db.prepare("SELECT alsonope FROM things").get());

    expect(lines).toHaveLength(2);
  });

  it("records per store, so one broken ledger does not mask another", () => {
    const second = path.join(tmp, "other.db");
    const db = new Database(second);
    db.exec("CREATE TABLE t (x INTEGER)");
    db.close();

    withReadOnlyDb(store, null, (d) => d.prepare("SELECT nope FROM things").get());
    withReadOnlyDb(second, null, (d) => d.prepare("SELECT nope FROM t").get());

    expect(listReaderFailures().map((f) => f.path).sort()).toEqual([second, store].sort());
  });
});

describe("a working read", () => {
  it("records nothing", () => {
    const out = withReadOnlyDb(store, null, (db) => db.prepare("SELECT name FROM things").get());
    expect(out).toMatchObject({ name: "a" });
    expect(listReaderFailures()).toEqual([]);
  });

  it("does not clear an earlier failure — the defect happened and the record should say so", () => {
    withReadOnlyDb(store, null, (db) => db.prepare("SELECT nope FROM things").get());
    withReadOnlyDb(store, null, (db) => db.prepare("SELECT name FROM things").get());
    expect(listReaderFailures()).toHaveLength(1);
  });
});
