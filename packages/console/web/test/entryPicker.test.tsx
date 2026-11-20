import { describe, it, expect } from "vitest";
import type { BuilderEntrySchema } from "@console/shared";
import { grouped, presetStart } from "../src/pages/Builder/presets";
import { withFilterGates } from "../src/pages/Builder/BuilderFields";

const SCHEMA: BuilderEntrySchema = {
  entries: {
    daily_time: { label: "Daily at a time", group: "Time", about: "Same minute each session.", suits: "any structure", settings: ["at", "window"], defaults: {} },
    rsi_pullback: { label: "Pullback in an uptrend", group: "Trend and mean reversion", about: "RSI(2) under 10.", suits: "short puts", settings: ["rsi_n", "at"], defaults: { at: "15:50" } },
    custom: { label: "Custom", group: "Custom", about: "Any entry.", suits: "", settings: ["at", "window"], defaults: {} },
  },
  filters: {
    vix_band: { label: "VIX within a band", group: "Volatility", about: "VIX between two levels.", suits: "", settings: ["vix_min", "vix_max"], defaults: {} },
  },
  settings: {
    at: { type: "time", default: "10:00", label: "enter at · ET" },
    window: { type: "int", min: 1, max: 60, default: 5, label: "minutes to enter" },
    rsi_n: { type: "int", min: 2, max: 50, default: 2, label: "RSI length" },
    vix_min: { type: "number", min: 0, max: 100, default: 15, label: "at least" },
    vix_max: { type: "number", min: 0, max: 100, default: 30, label: "at most" },
  },
};

describe("the entry presets", () => {
  it("start a preset from its own defaults over the module's", () => {
    expect(presetStart(SCHEMA, SCHEMA.entries["rsi_pullback"]!)).toEqual({ rsi_n: 2, at: "15:50" });
    expect(presetStart(SCHEMA, SCHEMA.entries["daily_time"]!)).toEqual({ at: "10:00", window: 5 });
  });

  it("group in the module's order", () => {
    expect(grouped(SCHEMA.entries).map(([g]) => g)).toEqual(["Time", "Trend and mean reversion", "Custom"]);
  });
});

describe("filling the entry again", () => {
  it("replaces the gates it added where they stand as added, and keeps every other gate", () => {
    const mine = { gate: "mid_price", min: 0.1 };
    const added = { gate: "vix_level", min: 15, max: 30 };
    const edited = { gate: "above_ma", kind: "sma", n: 100 };
    const next = [{ gate: "vol_index_rank", min: 30 }];
    const gates = withFilterGates([mine, added, edited], [JSON.stringify(added), JSON.stringify({ gate: "above_ma", kind: "sma", n: 200 })], next);
    expect(gates).toEqual([mine, edited, ...next]);
    expect(withFilterGates(undefined, [], next)).toEqual(next);
  });
});
