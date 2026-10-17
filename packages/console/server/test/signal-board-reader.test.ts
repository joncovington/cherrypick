import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { readSignalBoard } from "../src/readers/signalBoard.js";

/**
 * The signal board's reader renames the board file's fields and decides nothing. What it must get
 * right: the newest session by default and a named one on request; a tie's null side kept; a tag of
 * a method it does not know dropped, not shown under some other name; and no folder read as no board.
 */

let tmp: string;
let config: ConsoleConfig;

function write(day: string, doc: unknown): void {
  const p = path.join(tmp, "technicals", "board", `${day}.json`);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(doc));
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-signal-board-"));
  config = { paths: { technicalsDir: path.join(tmp, "technicals") } } as unknown as ConsoleConfig;
});

describe("readSignalBoard", () => {
  it("reads no folder as no board", () => {
    expect(readSignalBoard(config)).toMatchObject({ day: null, days: [], rows: [] });
  });

  it("reads the newest session by default, carries every field, and drops unknown methods", () => {
    write("2026-10-15", { session: "2026-10-15", rows: [{ symbol: "OLD", side: "bullish", count: 1, tags: [] }] });
    write("2026-10-16", {
      session: "2026-10-16",
      names: 382,
      methods: { stage: { label: "RS stage", approximate: false }, rotation: { label: "Sector rotation", approximate: true } },
      not_counted: ["fundamentals", "put buying"],
      revisions_through: "2026-10-17T22:11:24+00:00",
      rows: [
        {
          symbol: "CAH",
          sector: "Healthcare",
          side: "bullish",
          count: 2,
          bullish: 2,
          bearish: 1,
          tags: [
            { method: "stage", label: "Confirmed outperformer", detail: "" },
            { method: "rotation", label: "Money rotating into the sector", detail: "XLV leading" },
            { method: "flow", label: "Institutional call buying", detail: "$5M" },
          ],
        },
        { symbol: "TIE", sector: null, side: null, count: 1, bullish: 1, bearish: 1, tags: [] },
        { sector: "Energy" },
      ],
    });
    const p = readSignalBoard(config);
    expect(p.day).toBe("2026-10-16");
    expect(p.days).toEqual(["2026-10-16", "2026-10-15"]);
    expect(p).toMatchObject({ names: 382, notCounted: ["fundamentals", "put buying"], revisionsThrough: "2026-10-17T22:11:24+00:00" });
    expect(p.methods.rotation).toEqual({ label: "Sector rotation", approximate: true });
    expect(p.rows.map((r) => r.symbol)).toEqual(["CAH", "TIE"]);
    expect(p.rows[0]!.tags.map((t) => t.method)).toEqual(["stage", "rotation"]);
    expect(p.rows[1]).toMatchObject({ side: null, bullish: 1, bearish: 1 });
    expect(readSignalBoard(config, "2026-10-15").rows.map((r) => r.symbol)).toEqual(["OLD"]);
  });
});
