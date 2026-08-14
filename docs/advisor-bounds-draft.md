# Advisor bounds — what each module will accept advice about

> **Status: signed off and applied 2026-08-14.** These blocks are in the deployed configs and
> `advisor.enabled` is on for all three modules. The ranges below are the ones actually running.
>
> **They were re-derived against the DEPLOYED values before being applied.** The first draft
> bracketed `config.example.json`, which has drifted from what the machine runs: MEIC's
> `daily_ic_trade_target` is 200 (effectively uncapped), not 3; flies' `min_floor_dollars` is +1.0,
> not -10; earnings' `double_calendar.stop_loss_pct_of_debit` is 1.0, which the drafted 0.35-0.75
> range would have *excluded* outright. Every range below brackets the value the module is running
> today, which is the whole point of "a neighbour of the control, not a stranger".

A module's `advice.bounds` manifest is the human half of the contract: it names every parameter the
advisor may move, and the closed range it may move within. The advisor cannot add a key, widen a
range, or route around one — `cherrypick.core.advice` validates against this manifest on the way out
and each loop re-validates against it on the way in, and **one out-of-range value rejects the whole
artifact**.

Three principles behind every range below:

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

The deployed config carried no `advice` block until now; the example config has had one since the
first advise pipeline, and this is that list narrowed to a single parameter:

```json
"advice": {
  "enabled": true,
  "base_profile": "control",
  "bounds": {
    "stop_trigger_ratio": { "min": 0.85, "max": 0.95 }
  }
}
```

**Why this one.** The per-side stop is the single largest loss mechanism in the paper book and the
one parameter the ledger already instruments well enough to judge a change by (`put_max_cost`,
`call_max_cost`, `put_settle_value`, `call_settle_value` — "would a wider trigger have fired?" is
answerable from stored rows). Deployed is 0.95, the ceiling here, so the advisor can only propose
triggering *earlier* — a tighten-only range on the loss mechanism, which is the right asymmetry for
a first experiment.

**`daily_ic_trade_target` was dropped, not floored.** The question was whether to allow 0 (an arm
that takes no trades) and the answer was no — but the deployed value is **200**, i.e. effectively
uncapped, so any small range would have handed the advised arm a fraction of the control's
opportunity count and measured pacing while pretending to measure the stop. That is the exact
failure flies' config notes record twice. If pacing is worth testing it needs its own experiment,
with a range that brackets 200.

**What I left out of the example's list, deliberately.** `entry_price_strategy` is a `choices`
enumeration spanning five fill mechanics; changing it changes what a fill *means*, which makes the
advised book's fills incomparable to the control's rather than merely different. Add it later if you
want it, as its own experiment, on its own sessions.

## Flies — `~/.cherrypick/config/flies.json`

```json
"advice": {
  "enabled": true,
  "base_arm": "control",
  "bounds": {
    "min_credit_pct_of_width": { "min": 0.15, "max": 0.30 },
    "fee_buffer": { "min": 0.10, "max": 0.20 },
    "min_floor_dollars": { "min": -10.0, "max": 15.0 }
  }
}
```

**Why these three.** They are the three gates the module's own experiment log keeps returning to:
the entry credit floor (deployed 0.20 of width), the completion price buffer (deployed 0.10), and
the floor bar a completion must clear (**deployed +1.0**, not the example's −10.0). All three are per-position gates on a book held to settlement,
so an advised arm differing in one of them is a clean single-variable read.

**`fee_buffer` is floored at today's value**, deliberately: it is what actually bounds a
completion's downside — the price gate caps the completing debit at `credit − fee_buffer`, so the
worst floor a passing completion can carry is about `fee_buffer × 100 − fees − reserve`, and
loosening it moves a limit `min_floor_dollars` cannot reach past. The advisor may only make
completions *stricter* here; widening below 0.10 needs a measurement first.

`min_floor_dollars`' range is asymmetric for the same reason: from the deployed +1.0 it may tighten
to +15 but loosen only to −10, because the module's own honesty rule 6 says a book that needs
negative floors to look viable is telling you something rather than asking to be tuned. The −10 end
is also roughly where the price gate refuses first anyway (~−11.89 on 5-wide SPX), so nothing below
it would be reachable.

## Earnings — `~/.cherrypick/config/earnings.json`

Dotted names, `"<strategy>.<param>"`, and **management/exit params only** — see the earnings
CLAUDE.md on why a twin cannot express an entry-side change.

```json
"advice": {
  "enabled": true,
  "bounds": {
    "iron_fly.profit_target_pct":            { "min": 0.25, "max": 0.60 },
    "iron_fly.stop_loss_credit_multiple":    { "min": 1.25, "max": 2.50 },
    "iron_condor.profit_target_pct":         { "min": 0.25, "max": 0.65 },
    "double_calendar.stop_loss_pct_of_debit":{ "min": 0.60, "max": 1.25 },
    "double_calendar.leg_stop_delta_abs":    { "min": 0.35, "max": 0.55 }
  }
}
```

Current **deployed** values, for reference: `iron_fly` 0.50 / 1.5, `iron_condor` 0.50 / 1.5,
`double_calendar` 0.25 / **1.0** / 0.45. Every range above brackets the deployed value — these are
not the example config's numbers, which is exactly what the first draft got wrong.

**Two strategies are missing from this list on purpose.** `atm_calendar` and
`broken_wing_butterfly` have thinner samples; adding them widens the surface without adding
comparable evidence. `directional_credit_spread` is left out for the same reason — add whichever you
want once its book is deep enough to read.

---

## Before enabling: the supervised sequence

Run on 2026-08-14. Each config was backed up beside itself before its block was added
(`<name>.json.advisor-bak`).

1. ✅ Blocks pasted into `~/.cherrypick/config/{meic,flies,earnings}.json`.
2. ✅ `python -m cherrypick.advisor init-db`; the `--dry-run` pack inspected (86 KB deep pack, live
   section labelled read-only, every section populated).
3. ✅ `advice.enabled: true` per module, `advisor.enabled: true` in the suite config.
4. ✅ One supervised light run (`--slot am --force`).
5. ✅ One supervised deep run (`--slot deep --force`), enacted artifact inspected.
6. **Still to confirm, next morning:** `advised:control` in MEIC's `advice_active.json` and in its
   book rows. One morning further: an `advised:strat_test:*` twin opening at the 15:35 scan, and its
   overlaid target governing the following morning's verdict.

Kill switches, in increasing order of blast radius: `python -m cherrypick.advisor kill <id>` (or the
console button) stops one experiment; `advice.enabled: false` in a module's config stops that
module accepting anything; `advisor.enabled: false` stops the whole thing; deleting an artifact
under `state/advice/` reverts tomorrow to baseline.
