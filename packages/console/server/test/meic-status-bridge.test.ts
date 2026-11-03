import { afterEach, describe, expect, it } from "vitest";
import { readMeicReadiness, resetMeicStatusCache, setMeicStatusCaller, shapeReadiness } from "../src/services/meicStatusBridge.js";

/**
 * The console asks MEIC's `live_loop --status` whether live may be armed rather than deriving it.
 * What these pin is the console's side: the verdict is handed on as the module gave it, a failure or
 * a document without a verdict is `{ok: false}` and never "ready", and the read is memoised.
 */

afterEach(() => {
  setMeicStatusCaller();
  resetMeicStatusCache();
});

const STATUS = {
  ok: true,
  arm_ready: { ready: false, blockers: ["the supervisor is not running: nothing would tick"], warnings: ["halt flag set"] },
  open_ics: ["LIVE-SPX-14 SPX 7565p/7615c 10w, credit 1.8, holds call, stop 2.0x credit"],
  criticals_today: [
    { at: "2026-11-02T10:05:00-05:00", message: "CRITICAL first" },
    { at: "2026-11-02T10:40:00-05:00", message: "CRITICAL second" },
  ],
};

describe("the MEIC status bridge", () => {
  it("hands on the verdict, the held ICs and the criticals as the module gave them", () => {
    expect(shapeReadiness({ ok: true, json: STATUS, error: null })).toEqual({
      ok: true,
      ready: false,
      blockers: ["the supervisor is not running: nothing would tick"],
      warnings: ["halt flag set"],
      openIcs: STATUS.open_ics,
      criticals: STATUS.criticals_today,
    });
  });

  it("reads a clean verdict as ready", () => {
    const out = shapeReadiness({ ok: true, json: { ...STATUS, arm_ready: { ready: true, blockers: [], warnings: [] } }, error: null });
    expect(out).toMatchObject({ ok: true, ready: true, blockers: [] });
  });

  it("never reads a ready flag that lists blockers as ready", () => {
    const out = shapeReadiness({ ok: true, json: { ...STATUS, arm_ready: { ready: true, blockers: ["x"], warnings: [] } }, error: null });
    expect(out).toMatchObject({ ok: true, ready: false });
  });

  it("reports a failed spawn as an error, not as ready", () => {
    const out = shapeReadiness({ ok: false, json: null, error: "MEIC status unavailable — exit 1" });
    expect(out).toEqual({ ok: false, error: "MEIC status unavailable — exit 1" });
  });

  it("reports a document without a verdict (an older meic package) as an error, not as ready", () => {
    const { arm_ready: _drop, ...old } = STATUS;
    const out = shapeReadiness({ ok: true, json: old, error: null });
    expect(out.ok).toBe(false);
  });

  it("reports a status that refuses itself as an error", () => {
    expect(shapeReadiness({ ok: true, json: { ok: false, error: "no config" }, error: null })).toEqual({ ok: false, error: "no config" });
  });

  it("memoises: one subprocess serves reads inside the window, a later read asks again", async () => {
    let calls = 0;
    setMeicStatusCaller(() => {
      calls += 1;
      return { ok: true, json: STATUS, error: null };
    });
    await readMeicReadiness(1_000);
    await readMeicReadiness(20_000);
    expect(calls).toBe(1);
    await readMeicReadiness(40_000);
    expect(calls).toBe(2);
  });
});
