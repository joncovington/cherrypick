# Draft advisor bounds — for sign-off before anything is enabled

> **Status: proposed, not applied.** Nothing in this file is in a deployed config. The advisor
> refuses every module whose config carries no `advice` block, so until someone pastes one of these
> in and flips `advisor.enabled`, the whole pipeline is inert by construction.

A module's `advice.bounds` manifest is the human half of the contract: it names every parameter the
advisor may move, and the closed range it may move within. The advisor cannot add a key, widen a
range, or route around one — `cherrypick.core.advice` validates against this manifest on the way out
and each loop re-validates against it on the way in, and **one out-of-range value rejects the whole
artifact**.

Three principles behind every range below, and they are worth arguing with before signing:

1. **Narrow beats interesting.** An advised book that differs from its control on five axes measures
   nothing. Two or three params per module is a comparison; a dozen is a different strategy.
2. **Bracket the current value, don't reach past it.** Each range sits close to what the module runs
   today, so the advised book is a neighbour of the control rather than a stranger. Widen later, on
   evidence.
3. **Nothing that changes which trades open.** Entry-side screens and sizing change the *population*
   the two books face, and a paired comparison needs them to face the same one. Those ideas are
   still worth having — they arrive as `creative` proposals for a human to act on.

Tightening any range later takes effect the next evening, without touching the running experiment:
`enact` re-reads the deployed config every night and re-validates before it issues.

---

## MEIC — `~/.cherrypick/config/meic.json`

The deployed config currently has **no** `advice` block at all (the example config has carried one
since the first advise pipeline). Suggested starting manifest, narrowed from that example:

```json
"advice": {
  "enabled": false,
  "base_profile": "control",
  "bounds": {
    "stop_trigger_ratio": { "min": 0.85, "max": 0.95 },
    "daily_ic_trade_target": { "min": 1, "max": 3 }
  }
}
```

**Why these two.** The per-side stop is the single largest loss mechanism in the paper book and the
one parameter the ledger already instruments well enough to judge a change by (`put_max_cost`,
`call_max_cost`, `put_settle_value`, `call_settle_value` — "would a wider trigger have fired?" is
answerable from stored rows). The trade target paces how many entries a session accumulates.

**What I left out of the example's list, deliberately.** `entry_price_strategy` is a `choices`
enumeration spanning five fill mechanics; changing it changes what a fill *means*, which makes the
advised book's fills incomparable to the control's rather than merely different. Add it later if you
want it, as its own experiment, on its own sessions.

**Note on the target's floor:** the example allows `min: 0`, which lets the advisor propose an arm
that takes no trades at all. That is a legitimate thing to want to test and a terrible thing to
discover by accident, so the draft floors it at 1. Say the word if you want 0 back.

## Flies — `~/.cherrypick/config/flies.json`

```json
"advice": {
  "enabled": false,
  "base_arm": "control",
  "bounds": {
    "min_credit_pct_of_width": { "min": 0.15, "max": 0.30 },
    "fee_buffer": { "min": 0.05, "max": 0.20 },
    "min_floor_dollars": { "min": -12.0, "max": 25.0 }
  }
}
```

**Why these three.** They are the three gates the module's own experiment log keeps returning to:
the entry credit floor (currently 0.20 of width), the completion price buffer (0.10), and the floor
bar a completion must clear (−10.0). All three are per-position gates on a book held to settlement,
so an advised arm differing in one of them is a clean single-variable read.

**One caution worth carrying into the sign-off**, straight from that module's honesty rule 6:
`fee_buffer` is what actually bounds the downside of a completion — the price gate caps the
completing debit at `credit − fee_buffer`, so the worst floor a passing completion can carry is
about `fee_buffer × 100 − fees − reserve`. Loosening it moves a limit `min_floor_dollars` cannot
reach past. The 0.05 floor here is deliberately not much below today's 0.10; I would not sign off on
lower without a measurement.

`min_floor_dollars`' range is asymmetric on purpose: it can tighten a long way (up to +25) and
loosen only slightly (to −12), because the module's own rule says a book needing negative floors to
look viable is telling you something rather than asking to be tuned.

## Earnings — `~/.cherrypick/config/earnings.json`

Dotted names, `"<strategy>.<param>"`, and **management/exit params only** — see the earnings
CLAUDE.md on why a twin cannot express an entry-side change.

```json
"advice": {
  "enabled": false,
  "bounds": {
    "iron_fly.profit_target_pct":            { "min": 0.15, "max": 0.50 },
    "iron_fly.stop_loss_credit_multiple":    { "min": 1.25, "max": 2.50 },
    "iron_condor.profit_target_pct":         { "min": 0.25, "max": 0.65 },
    "double_calendar.stop_loss_pct_of_debit":{ "min": 0.35, "max": 0.75 },
    "double_calendar.leg_stop_delta_abs":    { "min": 0.35, "max": 0.55 }
  }
}
```

Current deployed values, for reference: `iron_fly` 0.25 / 1.5, `iron_condor` 0.50 / 1.5,
`double_calendar` 0.15 / 0.50 / 0.45. Every range above brackets its current value.

**Two strategies are missing from this list on purpose.** `atm_calendar` and
`broken_wing_butterfly` have thinner samples; adding them widens the surface without adding
comparable evidence. `directional_credit_spread` is left out for the same reason — add whichever you
want once its book is deep enough to read.

---

## Before enabling: the supervised sequence

1. Paste the signed-off blocks into the deployed configs (`~/.cherrypick/config/*.json`), leaving
   `advice.enabled: false` in each for now.
2. `python -m cherrypick.advisor init-db`, then
   `python scripts/advisor_checkpoint.py --slot am --dry-run` — this prints the real fact pack and
   the real prompt and **invokes nothing**. Read the pack: check the live/paper labelling, check the
   token budget, check that nothing in it surprises you.
3. Flip `advice.enabled: true` on the modules you signed off, and `advisor.enabled: true` in the
   suite config.
4. One supervised light run (`--slot am --force`), then inspect the checkpoint and the console's
   Advisor page.
5. One supervised deep run (`--slot deep --force`), then inspect the enacted artifact at
   `state/advice/<module>-<next session>.json` and confirm it loads through
   `cherrypick.core.advice.load` with the deployed bounds.
6. Next morning: confirm `advised:control` appears in MEIC's `advice_active.json` and in its book
   rows. One morning further: confirm an `advised:strat_test:*` twin opens at the 15:35 scan and
   that its overlaid target governs the following morning's verdict.

Kill switches, in increasing order of blast radius: `python -m cherrypick.advisor kill <id>` (or the
console button) stops one experiment; `advice.enabled: false` in a module's config stops that
module accepting anything; `advisor.enabled: false` stops the whole thing; deleting an artifact
under `state/advice/` reverts tomorrow to baseline.
