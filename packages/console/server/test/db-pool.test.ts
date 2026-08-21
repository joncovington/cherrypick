/**
 * The pooled read-only handle must never serve a stale story.
 *
 * Pooling turns a ~2.2ms open into a ~0.025ms reuse, which is most of a short request. The whole
 * risk it introduces is that the console keeps reading through a handle opened before a module
 * wrote — so these tests are mostly about the recycling, not the speed.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it } from "vitest";

import { closePooledDbs, withReadOnlyDb } from "../src/readers/db.js";

function tmpDb(name: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-pool-"));
  const p = path.join(dir, name);
  const db = new Database(p);
  db.pragma("journal_mode = WAL");
  db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)");
  db.prepare("INSERT INTO t (v) VALUES (?)").run("first");
  db.close();
  return p;
}

const read = (p: string): string[] =>
  withReadOnlyDb<string[]>(p, [], (db) =>
    db.prepare<[], { v: string }>("SELECT v FROM t ORDER BY id").all().map((r) => r.v),
  );

afterEach(() => {
  closePooledDbs();
});

describe("the pooled read-only handle", () => {
  it("returns the fallback for a store that does not exist", () => {
    expect(read(path.join(os.tmpdir(), "definitely-absent-store.db"))).toEqual([]);
  });

  it("reads the same rows across repeated calls", () => {
    const p = tmpDb("repeat.db");
    expect(read(p)).toEqual(["first"]);
    expect(read(p)).toEqual(["first"]);
  });

  it("SEES A WRITE made after the handle was pooled", () => {
    // The failure this pooling could have introduced: a console that keeps serving whatever the
    // ledger held when the server started.
    const p = tmpDb("write-through.db");
    expect(read(p)).toEqual(["first"]);

    const w = new Database(p);
    w.prepare("INSERT INTO t (v) VALUES (?)").run("second");
    w.close();

    expect(read(p)).toEqual(["first", "second"]);
  });

  it("sees a MIGRATION made after the handle was pooled", () => {
    // Module migrations are additive and land while this process is running. A handle opened
    // before one would keep serving the older schema for the life of the server.
    const p = tmpDb("migrate.db");
    expect(read(p)).toEqual(["first"]);

    const w = new Database(p);
    w.exec("ALTER TABLE t ADD COLUMN added_later TEXT");
    w.prepare("INSERT INTO t (v, added_later) VALUES (?, ?)").run("second", "x");
    w.close();

    const rows = withReadOnlyDb<Array<Record<string, unknown>>>(p, [], (db) =>
      db.prepare<[], Record<string, unknown>>("SELECT * FROM t ORDER BY id").all(),
    );
    expect(rows.map((r) => r["v"])).toEqual(["first", "second"]);
    expect(rows[1]?.["added_later"]).toBe("x");
  });

  it("keeps the store readable after a query throws", () => {
    // A throw evicts the handle rather than leaving a wedged one pooled for every later request.
    const p = tmpDb("recover.db");
    expect(read(p)).toEqual(["first"]);

    const boom = withReadOnlyDb<string>(p, "fallback", (db) => {
      db.prepare("SELECT * FROM no_such_table").all();
      return "unreachable";
    });
    expect(boom).toBe("fallback");
    expect(read(p)).toEqual(["first"]);
  });

  it("stays read-only", () => {
    const p = tmpDb("readonly.db");
    const wrote = withReadOnlyDb<boolean>(p, false, (db) => {
      try {
        db.prepare("INSERT INTO t (v) VALUES ('nope')").run();
        return true;
      } catch {
        return false;
      }
    });
    expect(wrote).toBe(false);
    expect(read(p)).toEqual(["first"]);
  });

  it("does not block a WAL checkpoint while a handle is pooled", () => {
    // The hazard the pool had to avoid: an idle pooled handle must hold an open FILE, never an
    // open read transaction. A retained transaction would starve the checkpointer, undoing the
    // WAL work the suite's stream cache depends on.
    const p = tmpDb("checkpoint.db");
    expect(read(p)).toEqual(["first"]);

    const w = new Database(p);
    for (let i = 0; i < 200; i++) w.prepare("INSERT INTO t (v) VALUES (?)").run(`row${String(i)}`);
    const result = w.pragma("wal_checkpoint(TRUNCATE)") as Array<{ busy: number }>;
    w.close();

    expect(result[0]?.busy).toBe(0); // 0 = the checkpoint was not blocked
  });
});
