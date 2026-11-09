import { describe, it, expect } from "vitest";
import { structureLegs, type StructureLeg } from "../src/pages/Flies/structure";

/**
 * A flies position's legs, from its kind, side, centre and wings, as the module builds them
 * (`book.entry_leg_strikes`, `fly.bwb_strikes`). The order chain on a card names these contracts, so
 * a wrong wing here is a wrong trade on screen; a structure the row cannot spell out has none.
 */

const chain = (legs: StructureLeg[]) => legs.map((l) => `${l.buy ? "BTO" : "STO"} ${l.n} ${l.strike}${l.right}`);

describe("structureLegs", () => {
  it("draws a fly as its wings once and its body twice", () => {
    expect(chain(structureLegs("fly", "put", 100, 10))).toEqual(["BTO 1 90P", "STO 2 100P", "BTO 1 110P"]);
    expect(chain(structureLegs("fly", "call", 100, 10, 10))).toEqual(["BTO 1 90C", "STO 2 100C", "BTO 1 110C"]);
  });

  it("puts a vertical's wing on the side the module protects", () => {
    expect(chain(structureLegs("short_vertical", "put", 100, 5))).toEqual(["STO 1 100P", "BTO 1 95P"]);
    expect(chain(structureLegs("short_vertical", "call", 100, 5))).toEqual(["STO 1 100C", "BTO 1 105C"]);
    // debit-first buys the wing on the other side: a call below the centre, a put above it
    expect(chain(structureLegs("long_vertical", "call", 100, 5))).toEqual(["BTO 1 95C", "STO 1 100C"]);
    expect(chain(structureLegs("long_vertical", "put", 100, 5))).toEqual(["BTO 1 105P", "STO 1 100P"]);
  });

  it("puts a bwb's near wing on the protected side and its far wing on the risk side", () => {
    expect(chain(structureLegs("bwb", "put", 100, 5, 15))).toEqual(["BTO 1 85P", "STO 2 100P", "BTO 1 105P"]);
    expect(chain(structureLegs("bwb", "call", 100, 5, 15))).toEqual(["BTO 1 95C", "STO 2 100C", "BTO 1 115C"]);
  });

  it("sells both bodies of an iron fly at the centre", () => {
    expect(chain(structureLegs("iron_fly", null, 100, 10))).toEqual(["BTO 1 90P", "STO 1 100P", "STO 1 100C", "BTO 1 110C"]);
  });

  it("draws nothing it cannot spell out from the row", () => {
    expect(structureLegs("fly", "put", 100, 5, 15)).toEqual([]);
    expect(structureLegs("bwb", "put", 100, 5, null)).toEqual([]);
    expect(structureLegs("fly", null, 100, 10)).toEqual([]);
    expect(structureLegs("fly", "put", null, 10)).toEqual([]);
    expect(structureLegs("condor", "put", 100, 10)).toEqual([]);
  });
});
