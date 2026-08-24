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

- A Friday entry phase in `paper_loop.run_once`, with its own window (15:50–16:00, see below) and
  its own state gate on the session's exits, under its own config block so the regime is off by
  default.
- `session_books` extended so the Friday regime resolves its own book set.
- `stream_request.py` must declare the expirations **a session earlier** — the Friday entry needs
  the coming week's chains on Friday, not Monday. This is the one change that touches the producer:
  it widens the declared expiration set, and per the 2026-08-24 subscription incident that is a
  load question to land deliberately, outside market hours.

## Entry time: the close (user directive, 2026-08-24)

**The Friday entry goes on at the end of the session**, which is the right choice for the stated
question and also the sharpest possible test of it. Entering at the close means owning the structure
across the entire weekend having paid the least time premium beforehand — the maximum capture the
idea can produce. It equally maximizes exposure to the counter-argument, since Friday's closing
premiums are exactly where market makers have already marked weekend decay down. If the effect
survives *this* entry time it is real; if it does not survive here it does not exist at a gentler
one.

It also lands the entry directly on top of the session's existing traffic, so the window has to be
chosen against the clock the module already runs:

| | |
|---|---|
| exit window (`control` sells every leg) | 15:45 – 15:55 |
| **Friday entry window** (settled 2026-08-24) | **15:50 – 16:00, gated on the exit half being done** |
| settlement (`settle_time`) | 16:20 |

**Ordering is enforced by state, not by the clock.** The window deliberately opens at 15:50, inside
the exit window's tail, and the entry is blocked until this session's exits have completed. That
buys twenty attempts at a 30s cadence instead of ten, and it lets the entry start as soon as the
exits are actually done rather than waiting out a clock boundary they may have cleared minutes
earlier — while still making it impossible to open the new week before closing the old one.

The state gate is what makes this safe, and it is necessary rather than belt-and-braces: the phase
order inside a single tick is *dispose → enter → mark → manage*, so **entry runs BEFORE
management**. A window that merely overlapped the exit window with no state gate would let one tick
open week N+1 before week N's exits fired.

> **⚠️ The gate must be "every book that intends to exit today has done so" — never "the book is
> flat."** `path` deliberately never closes; it holds shorts to Friday settlement and rides the
> longs over the weekend. A naive wait-until-flat condition is therefore never satisfied on any
> Friday, and the entry would silently never fire — a deadlock that would look exactly like a
> skipped week. Read the pending-exit verdicts, not the position count.

**The fragility this addresses was demonstrated the day it was designed.** 2026-08-24 is a live
example of a narrow window plus a starved feed producing a skipped week — twice, on consecutive
Mondays. SPY liquidity into the close is not the concern; the feed is.

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

## The declared question, and what it implies for the measurement

**Stated intent (user, 2026-08-24):** *does the additional weekend theta decay add to the
profitability of the overall double calendar trade, versus a Monday entrance?*

That is a question about a **small, systematic** effect, asked of a **weekly** cadence — and those
two facts together decide how this must be measured.

`dc_7_10` differs from `dc_4_7` in entry timing **and** in the strikes each regime picks from its
own expected move. Read as two independent strategies that is not a confound at all — each picks
its strikes the way it would if it were the only regime, and "which entrance is more profitable"
is answered directly. The problem is **variance, not validity**. Week-to-week P&L on this structure
swings with strike placement and the spot path by far more than a weekend's differential decay is
worth; at one entry per week, a small systematic edge sits underneath a much larger noise term for
a very long time. Two independent regimes would answer this eventually, in years rather than
months.

**Holding the strikes constant collapses almost all of that noise, because of a property specific
to this structure:** if both entrances hold the *same strikes and the same expirations*, they hold
the *same contracts*, so from Monday onward their value paths are identical. The entire P&L
difference between entering Friday and entering Monday is therefore **what each paid** —

> `weekend capture = D_monday − D_friday`, net of the cost stack,

where `D` is the structure's debit at each entry moment. Nothing else differs. That single figure
is the answer to the stated question, it is recordable without opening a second position, and its
variance is a fraction of the per-week P&L's.

Note it is also the *complete* answer for the three-day difference, not just the theta half: if
spot gaps over the weekend and moves away from the strikes, `D_monday` reflects that too. So the
figure nets weekend decay against weekend gap risk, which is exactly the trade-off in question.

**So the arm is built as posed, and the primary measurement is the paired debit.** On Monday, at the
Monday entry moment, re-price the strikes the Friday regime already chose and record that row
alongside the real entry. The arm supplies the realistic answer and exercises the machinery — the
Friday ordering, the execution gate, the ex-div refusals, real exits on real books. The paired debit
supplies the low-variance answer to the mechanism question, weeks rather than years sooner.

Two caveats on the "identical from Monday onward" claim, both small and worth stating: an exit rule
expressed as a percentage of debit is keyed to a different base in each regime, and the Friday
entrant is exposed to stops or targets over the weekend that the Monday entrant is structurally not
in the trade for. Both are second-order against the debit differential, but neither is exactly zero.

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

- **Does the advised twin extend to it?** `advice.base_book` is `control`. An
  `advised:friday:control` is buildable but doubles the advisor surface for a regime with no
  history — probably not in v1.
- **Retirement: the advisor reports, a human decides** (settled 2026-08-24). The advisor's contract
  is bounded, expiring *parameter* advice applied to a synthetic `advised:` book — there is no verb
  in that vocabulary for retiring a population, and curve already draws this line explicitly, where
  `hook_threshold` and `close_dte` are "journaled-break territory, not overlay territory." So: put
  the per-structure comparison (`dc_4_7` against `dc_7_10` — net, return on risk, sessions,
  effective n, and the exit grid per regime) into the advisor's fact pack so every checkpoint reads
  both populations side by side and can say what the evidence supports; retiring either regime stays
  a journaled measurement break made deliberately. **The bar is still undeclared** — pick it while
  neither regime has a result to be attached to, so the decision cannot be retroactive.
