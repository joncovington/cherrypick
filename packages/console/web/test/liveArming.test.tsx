import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ArmAttempt, LiveArmReadout, LiveArmingState, LiveLoopSwitch, LiveTodayLoop, LiveTodayPayload } from "@console/shared";
import { ArmStrip, ConfirmDialog, HALT_LOCK, RefusedDialog, failedAttempt, isExactYes, stripModel } from "../src/pages/Live/ArmSwitch";
import { LiveTodayView, armFor } from "../src/pages/Live/LiveTodayPage";
import { latestAttempt } from "../src/lib/liveArming";

/**
 * The Live page's arm switches, as the browser draws them. Pinned: the switches are absent unless the
 * server says they are on; a loop with no switch (flies, bwb) has none; the switch shows what the
 * server's state says (it never reads a click as a state); a halt locks it; a not-ready loop is shown
 * its blockers and no confirmation; the typed box starts empty and the confirm button starts disabled;
 * the disclaimer on the page is the server's text, not the page's.
 */

const loop = (id: LiveTodayLoop["loop"], over: Partial<LiveTodayLoop> = {}): LiveTodayLoop =>
  ({
    loop: id,
    to: `/live/${id}`,
    ledger: "ok",
    ledgerError: null,
    arm: { armed: false, date: null, at: null, stale: false, disarmTime: "17:00", pastDisarm: false, liveEnabled: true, arm: "pm-200", symbol: "SPX" },
    loopState: null,
    open: 0,
    working: 0,
    workingBasis: "",
    worstCase: null,
    worstCaseBasis: "",
    entriesToday: 0,
    today: { trades: 0, gross: 0, fees: 0, settlementFees: 0, settlementRecorded: 0, settlementTrades: 0, net: 0, feesTrades: 0, netRecorded: 0, basis: "" },
    pairs: [],
    mark: null,
    breaker: { limit: null, figure: null, figureKind: "net", tripped: null, basis: "" },
    orphans: { at: null, count: 0 },
    held: 0,
    brokerHealth: null,
    overdue: [],
    moduleEnabled: true,
    retired: false,
    ...over,
  }) as LiveTodayLoop;

const payload = (loops: LiveTodayLoop[], halted = false): LiveTodayPayload =>
  ({
    generatedAt: "2026-11-24T16:00:00Z",
    session: "2026-11-24",
    halted,
    loops,
    attention: [],
    suite: { loops: loops.length, armed: [], net: 0, gross: 0, fees: 0, settlementFees: 0, trades: 0, summed: [], excluded: [], feesUnrecorded: [], unrealised: { value: 0, at: null, summed: [], unmarked: [] } },
  }) as unknown as LiveTodayPayload;

const sw = (l: string, over: Partial<LiveLoopSwitch> = {}): LiveLoopSwitch => ({ loop: l, state: "armable", lockedReason: null, ...over });
const attempt = (over: Partial<ArmAttempt> = {}): ArmAttempt => ({
  id: "a1",
  loop: "meic",
  session: "2026-11-24",
  startedAt: "2026-11-24T15:29:00Z",
  finishedAt: null,
  state: "running",
  step: "waiting",
  recordWritten: true,
  needsSupervisor: true,
  noop: false,
  message: "",
  detail: [],
  armedToday: null,
  ...over,
});

const ON: LiveArmingState = { enabled: true, switches: [sw("meic"), sw("ladder"), sw("meic.margin", { state: "locked", lockedReason: "kill criteria not written for this arm" })], attempts: [] };

const clean = (s: string): string => s.replace(/<!-- -->/g, "");
const html = (d: LiveTodayPayload, arming?: LiveArmingState) =>
  clean(
    renderToString(
      <MemoryRouter>
        <LiveTodayView data={d} arming={arming} />
      </MemoryRouter>,
    ),
  );

describe("the strip's model", () => {
  const meic = loop("meic");
  it("an unarmed loop reads Not armed today, and a click may open the dialog", () => {
    expect(stripModel(meic, sw("meic"), null, false)).toMatchObject({ state: "unarmed", on: false, title: "Not armed today" });
  });
  it("an armed loop reads on, and can be switched off", () => {
    const armed = loop("meic", { arm: { ...meic.arm, armed: true, date: "2026-11-24" } });
    expect(stripModel(armed, sw("meic"), null, false)).toMatchObject({ state: "armed", on: true, title: "Armed for today" });
    // Even with the halt up: switching off is always allowed.
    expect(stripModel(armed, sw("meic"), null, true)).toMatchObject({ state: "armed", on: true });
  });
  it("the switch does not move until the server says: a running attempt is pending, not armed", () => {
    expect(stripModel(meic, sw("meic"), attempt(), false)).toMatchObject({ state: "pending", on: true, title: "Arming…" });
    expect(stripModel(meic, sw("meic"), attempt({ state: "failed", finishedAt: "2026-11-24T15:31:00Z" }), false)).toMatchObject({ state: "unarmed", on: false });
  });
  it("a halt locks arming, with the words", () => {
    expect(stripModel(meic, sw("meic"), null, true)).toMatchObject({ state: "locked", on: false, lockedReason: HALT_LOCK });
    expect(HALT_LOCK).toBe("Clear the halt first. Arming never undoes a halt.");
  });
  it("a second login's account is locked with its blocker", () => {
    const m = stripModel(loop("meic.margin"), sw("meic.margin", { state: "locked", lockedReason: "kill criteria not written for this arm" }), null, false);
    expect(m).toMatchObject({ state: "locked", on: false, lockedReason: "kill criteria not written for this arm" });
  });
  it("a failed attempt stays readable for a while, then goes", () => {
    const f = attempt({ state: "failed", finishedAt: "2026-11-24T15:31:00Z" });
    expect(failedAttempt(f, Date.parse("2026-11-24T15:40:00Z"))).toBe(f);
    expect(failedAttempt(f, Date.parse("2026-11-24T17:00:00Z"))).toBeNull();
    expect(failedAttempt(attempt(), Date.now())).toBeNull();
  });
});

describe("guard: the switches are absent when live_arming is off", () => {
  const d = payload([loop("meic"), loop("ladder")]);
  it("the cards are exactly as before with no arming state, or with it off", () => {
    expect(html(d)).not.toContain("live-arm-strip");
    expect(html(d, { enabled: false, switches: [], attempts: [] })).not.toContain("live-arm-strip");
    expect(html(d, { enabled: false, switches: ON.switches, attempts: [] })).not.toContain("live-arm-strip");
    expect(armFor(loop("meic"), d, undefined)).toBeUndefined();
    expect(armFor(loop("meic"), d, { enabled: false, switches: ON.switches, attempts: [] })).toBeUndefined();
  });
  it("is drawn for meic and the ladder when on, and for no loop the server gave no switch", () => {
    const flies = loop("flies");
    const bwb = loop("bwb");
    const out = html(payload([loop("meic"), loop("ladder"), flies, bwb]), ON);
    expect(out.match(/live-arm-strip"|live-arm-strip /g)?.length).toBe(2);
    expect(armFor(flies, d, ON)).toBeUndefined();
    expect(armFor(bwb, d, ON)).toBeUndefined();
    expect(armFor(loop("meic", { retired: true }), d, ON)).toBeUndefined();
  });
  it("shows a second login's switch locked, with its blocker, and no way to arm", () => {
    const out = html(payload([loop("meic.margin")]), ON);
    expect(out).toContain("live-arm-switch locked");
    expect(out).toContain("kill criteria not written for this arm");
    expect(out).toContain('aria-disabled="true"');
  });
  it("draws a halt as a locked switch with the reason", () => {
    const out = html(payload([loop("meic")], true), ON);
    expect(out).toContain(HALT_LOCK);
    expect(out).toContain('aria-disabled="true"');
  });
  it("draws the pending steps while an attempt runs, and the latest attempt is the newest", () => {
    const run = attempt();
    const out = html(payload([loop("meic")]), { ...ON, attempts: [attempt({ id: "old", state: "done", finishedAt: "2026-11-23T20:00:00Z" }), run] });
    expect(out).toContain("Arming…");
    expect(out).toContain("You can leave this page.");
    expect(out).toContain("Waiting for the supervisor");
    expect(latestAttempt([attempt({ id: "old" }), run], "meic")).toBe(run);
  });
});

const READOUT: LiveArmReadout = {
  ok: true,
  error: null,
  loop: "meic",
  title: "meic · SPX",
  session: "2026-11-24",
  ready: true,
  blockers: [],
  warnings: ["no daily-loss breaker set (live.daily_loss_halt_dollars)"],
  halted: false,
  armedToday: false,
  supervisorFresh: true,
  facts: [
    { label: "account", value: "****1234" },
    { label: "symbol · arm", value: "SPX · pm-200" },
    { label: "self-disarms", value: "17:00 ET" },
  ],
  mode: null,
  modeNote: null,
  disclaimer: "# Disclaimer\n\nThe server's text <b>verbatim</b> & whole.\n",
  dialogId: "tok",
};

describe("the dialog", () => {
  const render = (r: LiveArmReadout) => clean(renderToString(<ConfirmDialog r={r} note={null} onClose={() => undefined} onChanged={() => undefined} onStarted={() => undefined} />));

  it("states the facts and shows the server's disclaimer whole, then asks for YES in an empty box", () => {
    const out = render(READOUT);
    expect(out).toContain("Step 1 of 2");
    expect(out).toContain("****1234");
    expect(out).toContain("SPX · pm-200");
    expect(out).toContain("no daily-loss breaker set");
    // The text is the server's, character for character (React escapes it; nothing is rewritten).
    expect(out).toContain("# Disclaimer\n\nThe server&#x27;s text &lt;b&gt;verbatim&lt;/b&gt; &amp; whole.\n".replace("&#x27;", "&#x27;"));
    expect(out).toMatch(/<input[^>]*value=""/);
  });

  it("starts with the confirm button disabled, whatever the last dialog held", () => {
    const out = render(READOUT);
    expect(out).toMatch(/<button[^>]*class="live-arm-btn live-arm-btn-go"[^>]*disabled/);
  });

  it("holds no disclaimer text of its own", () => {
    const out = render({ ...READOUT, disclaimer: "A DIFFERENT TEXT ENTIRELY" });
    expect(out).toContain("A DIFFERENT TEXT ENTIRELY");
    expect(out).not.toContain("NOT FINANCIAL ADVICE");
  });

  it("the ladder says whether it is dry-run", () => {
    const out = render({ ...READOUT, loop: "ladder", title: "ladder", mode: "dry-run", modeNote: "Dry-run note" });
    expect(out).toContain("dry-run: places nothing");
    expect(out).toContain("Dry-run: the loop reads the broker and places nothing.");
    const live = render({ ...READOUT, loop: "ladder", title: "ladder", mode: "live" });
    expect(live).toContain("LIVE: places real orders");
    expect(live).toContain("Real money, in the real account.");
  });

  it("only the exact word enables confirm", () => {
    expect(isExactYes("YES")).toBe(true);
    for (const t of ["", "yes", "Yes", "YES ", " YES", "YE", "YESS", "Y E S", "ＹＥＳ"]) expect(isExactYes(t), t).toBe(false);
  });
});

describe("guard: a not-ready loop is shown its blockers and no confirmation", () => {
  const refused = (r: LiveArmReadout) => clean(renderToString(<RefusedDialog r={r} onClose={() => undefined} />));

  it("lists each blocker as the loop worded it, with no input and no YES button", () => {
    const out = refused({ ...READOUT, ready: false, dialogId: null, blockers: ["the supervisor is not running: nothing would tick", "live gate unmet: live.gate0_confirmed"] });
    expect(out).toContain("the supervisor is not running: nothing would tick");
    expect(out).toContain("live gate unmet: live.gate0_confirmed");
    expect(out).toContain("not ready · 2 blockers");
    expect(out).not.toContain("<input");
    expect(out).not.toContain("live-arm-btn-go");
    expect(out).not.toContain("accept the disclaimer");
  });

  it("a halt says to clear it first, and offers no confirmation either", () => {
    const out = refused({ ...READOUT, halted: true, dialogId: null });
    expect(out).toContain(HALT_LOCK);
    expect(out).not.toContain("<input");
  });

  it("a status that could not be read says so", () => {
    const out = refused({ ...READOUT, ok: false, error: "MEIC status unavailable", ready: false, dialogId: null });
    expect(out).toContain("MEIC status unavailable");
    expect(out).not.toContain("<input");
  });

  it("the strip itself, rendered, carries no confirmation", () => {
    const out = renderToString(<ArmStrip l={loop("meic")} sw={sw("meic")} attempt={null} halted={false} />);
    expect(out).not.toContain("<input");
    expect(out).toContain('role="switch"');
    expect(out).toContain('aria-checked="false"');
  });
});
