import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import type { BuilderEntrySchema } from "@console/shared";
import { EntryPicker, grouped, startSettings } from "../src/pages/Builder/EntryPicker";
import { withFilterGates } from "../src/pages/Builder/BuilderForm";

const SCHEMA: BuilderEntrySchema = {
  entries: {
    daily_time: { label: "Daily at a time", group: "Time", about: "Same minute each session.", suits: "any structure", settings: ["at", "window"], defaults: {} },
    rsi_pullback: { label: "Pullback in an uptrend", group: "Trend and mean reversion", about: "RSI(2) under 10.", suits: "short puts", settings: ["rsi_n", "at"], defaults: { at: "15:50" } },
    custom: { label: "Custom", group: "Custom", about: "Any entry.", suits: "", settings: ["at", "window"], defaults: {} },
  },
  filters: {
    vix_band: { label: "VIX within a band", group: "Volatility", about: "VIX between two levels.", suits: "", settings: ["vix_min", "vix_max"], defaults: {} },
    uptrend: { label: "In an uptrend", group: "Trend", about: "Above its average.", suits: "", settings: ["ma_n"], defaults: {} },
  },
  settings: {
    at: { type: "time", default: "10:00", label: "enter at · ET" },
    window: { type: "int", min: 1, max: 60, default: 5, label: "minutes to enter" },
    rsi_n: { type: "int", min: 2, max: 50, default: 2, label: "RSI length" },
    vix_min: { type: "number", min: 0, max: 100, default: 15, label: "at least" },
    vix_max: { type: "number", min: 0, max: 100, default: 30, label: "at most" },
    ma_n: { type: "int", min: 2, max: 400, default: 200, label: "SMA days" },
  },
};

const html = (el: React.ReactElement) => renderToString(el).replace(/<!--\s*-->/g, "");

describe("the entry presets", () => {
  it("lists every preset under its group, Custom set apart, and the filters to add", () => {
    const out = html(<EntryPicker schema={SCHEMA} onFill={() => {}} initial="rsi_pullback" />);
    for (const label of ["Daily at a time", "Pullback in an uptrend", "Custom", "Time", "Trend and mean reversion"]) expect(out).toContain(label);
    expect(out).toContain("is-custom");
    expect(out).toContain("+ Add filter");
    expect(out).toMatch(/<optgroup label="Volatility"><option value="vix_band">VIX within a band/);
    expect(out).toContain("a starting point, not a tested edge");
    expect(out).toContain("Fill in the entry");
  });

  it("shows only the chosen preset's settings, at its own defaults", () => {
    const out = html(<EntryPicker schema={SCHEMA} onFill={() => {}} initial="rsi_pullback" />);
    expect(out).toContain("RSI length");
    expect(out).not.toContain("minutes to enter");
    expect(out).toMatch(/value="15:50"/);
    expect(startSettings(SCHEMA, SCHEMA.entries["rsi_pullback"]!)).toEqual({ rsi_n: 2, at: "15:50" });
  });

  it("comes back to the filters stacked earlier, each with its own settings and a remove", () => {
    const out = html(<EntryPicker schema={SCHEMA} onFill={() => {}} auto initialFilters={[{ filter: "vix_band", settings: { vix_min: 12, vix_max: 25 } }, { filter: "gone", settings: {} }]} />);
    expect(out).toContain("and · VIX within a band");
    expect(out).toMatch(/value="12"/);
    expect(out).toContain('aria-label="Remove filter VIX within a band"');
    expect(out).not.toContain("Fill in the entry");
  });

  it("groups in the module's order", () => {
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
