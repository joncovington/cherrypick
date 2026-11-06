---
description: Arm (or disarm) hand-placed LIVE earnings orders for TODAY, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Arm earnings live orders for today. Earnings has no live loop: a live earnings order is placed by
hand through `tt.py execute_trade --live`, against the real designated account, with real money.
From 2026-11-06 any such order that OPENS a position is refused unless today's **arm record**
(`~/.cherrypick/state/earnings-live-arm.json`) is present and the time is before 17:00 ET. A
closing order is never held back, armed or not. The halt flag, the kill criteria (`live.kill`) and
the one-contract-a-leg hand limit still apply on top. `enable_live_trading` in
`~/.cherrypick/config/earnings.json` stays the master switch: arming refuses while it is off.

`--stop`: disarm (see "Stop" below). Anything else: the arm flow.

## Arm (default)

1. From `packages/earnings`, run `python -m cherrypick.earnings.live_arm status` and show the user
   the result: whether today is already armed, and why not if not.
2. Say plainly what arming does: for the rest of today's session (until 17:00 ET) an opening live
   earnings order placed by hand can go to the broker. It places nothing by itself.
3. Ask the user to type the literal word `YES`. Anything else, including "yes", stops here with
   nothing written.
4. On `YES`, run `python -m cherrypick.earnings.live_arm arm --confirm YES` and report its output,
   including any quarter-end warning. A refusal (live switch off, past 17:00) is reported as it is;
   never work around it.

## Stop

Run `python -m cherrypick.earnings.live_arm disarm` and report it. Disarming needs no confirmation;
it only removes the ability to open.
