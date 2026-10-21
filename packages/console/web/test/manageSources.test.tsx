import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ManageLeg } from "@console/shared";
import { IvCell, LATE_AFTER_MIN, snapshotLate } from "../src/pages/Live/ManagePage";
import { ageText, bandText, etStamp } from "../src/pages/Live/manageParts";

/**
 * Live → positions and portfolio say where each figure came from: a leg's IV and its source, the
 * snapshot's time in ET with its age, and a warning only when a 30-minute run looks missed.
 */

function leg(over: Partial<ManageLeg> = {}): ManageLeg {
  return {
    label: "-1 2026-11-20 42C",
    quantity: -1,
    expiration: "2026-11-20",
    dte: 30,
    openPrice: 1.1,
    mid: 0.92,
    capture: 0.16,
    delta: 0.31,
    iv: 0.1907,
    ivSource: "mid",
    wide: false,
    ...over,
  };
}

const html = (l: ManageLeg) => renderToString(<IvCell l={l} />).replace(/<!-- -->/g, "");

describe("a leg's IV says where it came from", () => {
  it("marks the broker's IV, so it is visible beside a mid IV", () => {
    const broker = html(leg({ ivSource: "broker", wide: true }));
    expect(broker).toContain("19%");
    expect(broker).toContain(">broker<");
    expect(broker).toContain("wide");
    const mid = html(leg());
    expect(mid).toBe("19%");
  });

  it("says a leg no IV prices is unpriceable, with the model's missing dividend as the reason", () => {
    const out = html(leg({ iv: null, ivSource: "unpriceable", delta: null }));
    expect(out).toContain("— unpriceable");
    expect(out).toContain("no dividend");
  });

  it("marks a wide quote solved from its own mid anyway", () => {
    expect(html(leg({ wide: true }))).toContain(">wide<");
  });
});

describe("the snapshot's time", () => {
  it("is read in ET whatever offset it was written in", () => {
    expect(etStamp("2026-10-21T14:35:47-0600")).toBe("2026-10-21 16:35 ET");
    expect(etStamp("2026-10-21T18:45:01.528155+00:00")).toBe("2026-10-21 14:45 ET");
    expect(etStamp(null)).toBeNull();
  });

  it("states its age in the unit a person reads", () => {
    expect(ageText(12 * 60_000)).toBe("12 min");
    expect(ageText((3 * 60 + 5) * 60_000)).toBe("3 h 05 min");
    expect(ageText(3 * 24 * 60 * 60_000)).toBe("3 days");
  });

  const at = (ts: string) => Date.parse(ts);
  // Wednesday 2026-10-21, EDT (UTC-4).
  const snap = at("2026-10-21T13:00:00-04:00");

  it("warns only when a 30-minute run looks missed during the runs' hours", () => {
    expect(LATE_AFTER_MIN).toBe(45);
    expect(snapshotLate(snap, at("2026-10-21T13:40:00-04:00"), true)).toBeNull(); // 40 min: on time
    expect(snapshotLate(snap, at("2026-10-21T13:50:00-04:00"), true)).toBe(50); // 50 min: late
    expect(snapshotLate(snap, at("2026-10-21T13:50:00-04:00"), null)).toBe(50); // unknown day is not a holiday
  });

  it("stays quiet outside the runs' hours, on a weekend and on a day the watchdog says is not a trading day", () => {
    const yesterday = at("2026-10-20T16:35:00-04:00");
    expect(snapshotLate(yesterday, at("2026-10-21T10:30:00-04:00"), true)).toBeNull(); // the 10:00 run's allowance
    expect(snapshotLate(yesterday, at("2026-10-21T10:50:00-04:00"), true)).not.toBeNull();
    expect(snapshotLate(snap, at("2026-10-21T18:00:00-04:00"), true)).toBeNull(); // after the last run
    expect(snapshotLate(snap, at("2026-10-24T12:00:00-04:00"), true)).toBeNull(); // Saturday
    expect(snapshotLate(snap, at("2026-10-22T12:00:00-04:00"), false)).toBeNull(); // a holiday
    expect(snapshotLate(null, at("2026-10-21T13:50:00-04:00"), true)).toBeNull();
  });
});

describe("a missing band says which is missing", () => {
  const short = (bandMissing: string | null) => ({ band: null, bandMissing, beyondBand: null });

  it("keeps 'not in the snapshot' for no band file, and names the name or expiry otherwise", () => {
    expect(bandText(short("band not in the snapshot"))).toBe("band not in the snapshot");
    expect(bandText(short(null))).toBe("band not in the snapshot");
    expect(bandText(short("no band for the name in the snapshot"))).toBe("no band for this name");
    expect(bandText(short("no band for this expiry"))).toBe("no band for this expiry");
  });
});
