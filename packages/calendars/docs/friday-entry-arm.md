# The Friday-entry arm — a design proposal

*Drafted 2026-08-24 (user idea the same day). A design, not shipped work. The module had taken
exactly one position when this was written — 2026-08-24's `dc_4_7`, entered at 11:24 after two
consecutive skipped weeks — which is what makes this the cheapest moment this convention will ever
be changeable.*

## The idea

Enter the week's double calendar on the **previous Friday** instead of Monday morning, targeting
the same expiration dates the Monday entry would have used: shorts on that week's Friday, longs the
following Monday.

The appeal is **weekend theta**. A calendar is short the front and long the back, and the front
decays proportionally faster; three extra calendar days — two of them non-trading — is differential
decay the Monday entry simply forfeits. The standard counter is that market makers mark weekend
decay into Friday's closing premiums, so the theta may already be paid for. Which of those is true
for SPY weeklies is an empirical question, and measuring it forward is what this module is for.

## Why this is not a "book"

The existing books (`control`, `path`, `advised:control`) all derive from **one entry plan** —
identical strikes, identical mids, identical modeled costs — so any divergence between them is exit
policy and nothing else. That pairing is the module's central design property.

A Friday entry cannot share that plan. It prices a different snapshot, computes its expected move
over a 7-day horizon rather than a 4-day one, and therefore picks its own strikes at its own debit.
**So this is not a fourth book — it is a second entry regime, and it needs its own book set beneath
it** to answer the exit question within itself:

| | Monday regime | Friday regime |
|---|---|---|
| entry session | Monday | the previous Friday |
| DTE at entry | front 4, back 7 | front 7, back 10 |
| structure tag | `dc_4_7` | `dc_7_10` |
| books | `control`, `path`, `advised:control` | `friday:control`, `friday:path` |

The `friday:` prefix follows the `advised:` precedent. It matters that `control` never silently
means two different things in one ledger.

## What is already handled, and what is not

**Already correct, no work needed:**

- **Structure tags never pool** (honesty rule 4), and `clock.structure_tag` derives the tag from
  (entry, front, back) — so a Friday entry is tagged `dc_7_10` automatically and is a distinct
  population by construction.
- **The read-side exit grid already groups by structure tag** — `exit_policies.py` states that
  distinct tags never pool. The whole policy derivation therefore extends to the new regime for
  free, including its validation against the real books.
- **`cherrypick.core.ledgers`' `dc_week` schema** reads positions regardless of entry day.

**Needs building:**

- A Friday entry phase in `paper_loop.run_once`, with its own window (Friday is not Monday and
  should not borrow Monday's hours), gated on its own config block so the regime is off by default.
- `session_books` extended so the Friday regime resolves its own book set.
- `stream_request.py` must declare the expirations **a session earlier** — the Friday entry needs
  the coming week's chains on Friday, not Monday. This is the one change that touches the producer:
  it widens the declared expiration set, and per the 2026-08-24 subscription incident that is a
  load question to land deliberately, outside market hours.

## The ordering problem, and it is the real one

**Friday is already the busiest session in this design.** `control` sells every leg in the Friday
exit window, the shorts run to Friday settlement, and physical settlement hands over shares that
ride the weekend. Adding an entry to that session means one tick both closes week N−1 and opens
week N+1.

The existing Monday sequence is explicit about this class of problem — *dispose shares, then longs,
then enter* — "so the overlap day never contends." The Friday regime needs the same discipline
stated up front:

> **exit window → settlement → new entry**, in that order, and the new entry must never be able to
> consume a slot or a buying-power assumption the exit half has not finished releasing.

An entry that fires before the exit completes would silently change what the exit measured, which
is the failure the Monday ordering rule already exists to prevent.

## Two costs to state before building

**Ex-dividend refusals get more common, and asymmetrically.** Entry refuses the whole week when a
declared ex-date falls inside `[entry_session, back_expiration]`. Moving entry three days earlier
widens that span, so the Friday regime will skip weeks the Monday regime trades. Those weeks are
then present in one population and absent from the other — the two regimes are **not** a matched
set week-for-week, and any comparison has to say so rather than assume alignment.

**Positions and buying power roughly double.** Two regimes, each entering one double calendar per
week, each held to its own resolution. That is the honest price of running both, and it is the
reason to decide deliberately rather than to leave both on forever.

## The confound, stated plainly

`dc_7_10` differs from `dc_4_7` in entry timing **and** in the strikes each regime picks from its
own expected move. A difference in outcome cannot be attributed to weekend theta alone.

This is tolerable — the arm answers a real question ("is the Friday-entered version of this trade
better?") and that question is worth answering as posed. But if the isolation is wanted later, it
costs one recorded row and no position: on Monday, **re-price the strikes the Friday regime already
chose** and record that alongside the real entry. Identical contracts, identical strikes, two entry
moments — the difference is then entry timing exactly, with the strike choice held constant.

## Measurement rules this arm inherits

1. Net of the full modeled fee and slippage stack; gross is not a result (rule 1).
2. `dc_7_10` never pools with `dc_4_7` on any surface (rule 4) — including the headline.
3. A refused mark is still a row (rule 6); a Friday-entered week with a hole in its path is
   `derivable: False`, never zero.
4. The regime's arrival is a **journaled boundary** for the module, even though it pools with
   nothing: the ledger gains a population, and a reader comparing "the module's weeks" across that
   date needs to know which regime they are reading.
5. It is an **entry-timing** experiment, and the module's stated purpose is the exit question. Both
   can be true at once, but the exit grid should be read per-regime and never averaged across them.

## Open questions

- **Entry window on Friday.** Late in the session captures the most weekend decay and gives the
  least room to retry; the 2026-08-24 incident is a live demonstration that a narrow window plus a
  bad feed equals a skipped week. Pick the window and its retry budget together.
- **Does the advised twin extend to it?** `advice.base_book` is `control`. An
  `advised:friday:control` is buildable but doubles the advisor surface for a regime with no
  history — probably not in v1.
- **What would retire the Monday regime?** Running both forever is a decision by default. Declare
  up front roughly what evidence would justify collapsing to one, so the answer is not "whichever
  looked better the week someone asked."
