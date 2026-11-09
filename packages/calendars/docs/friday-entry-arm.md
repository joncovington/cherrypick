# The Friday-entry regime

An optional second entry regime for the calendars module. It ships off (`friday_entry.enabled: false` in
`config.example.json`). It enters the week's double calendar on the **previous Friday** instead of Monday
morning, with the same expiration dates the Monday entry would use: shorts on that week's Friday, longs
the following Monday. The code is `clock.friday_entry_plan`, the Friday phase of `paper_loop.run_once`,
`engine.price_at_strikes` and `engine.FRIDAY_PREFIX`.

## The question

**Does the additional weekend theta decay add to the profitability of the double calendar, versus a
Monday entrance?** A calendar is short the front and long the back, and the front decays faster, so three
extra calendar days (two non-trading) are differential decay the Monday entry forfeits. The standard
counter is that market makers mark weekend decay into Friday's closing premiums, so it may already be paid
for. Which is true for SPY weeklies is empirical, and measuring it forward is what this module is for.

The entry goes on at the end of the session. That is the sharpest test: it owns the structure across the
whole weekend having paid the least time premium beforehand, and it equally maximises exposure to the
counter-argument. If the effect survives this entry time it is real.

## Why it is a second regime, not a book

The books (`control`, `path`, `advised:...`) all derive from **one entry plan**, so any divergence between
them is exit policy and nothing else. A Friday entry cannot share that plan: it prices a different
snapshot, computes its expected move over a 7-day horizon rather than a 4-day one, and so picks its own
strikes at its own debit. It therefore has its own arm set beneath it, so `control` never silently means
two things in one ledger.

| | Monday regime | Friday regime |
|---|---|---|
| entry session | Monday | the previous Friday |
| DTE at entry | front 4, back 7 | front 7, back 10 |
| structure tag | `dc_4_7` | `dc_7_10` |
| arms | `control`, `path`, `advised:...` | `friday:control`, `friday:path` |

`base_book` reads the last segment of a name, so a `friday:` arm manages, settles and disposes as its
base. There is no advised twin of a Friday arm (it would double the advisor surface for a regime with
little history).

What carries over unchanged: structure tags never pool (honesty rule 4), and `clock.structure_tag`
derives the tag from (entry, front, back), so a Friday entry is a distinct population by construction.
The read-side exit grid groups by tag, and reads named arms: `friday:path` (the grid's substrate) and
`friday:control` are in `PATH_ARMS` / `VALIDATED_ARMS`, so Friday weeks feed the table under their own
`dc_7_*` tag and are validated to the cent. `stream_request.wanted_expirations` already declares the
coming week's front and back dates days ahead, so enabling the regime subscribes nothing new.

## Entry window and ordering

| | |
|---|---|
| exit window (`control` sells every leg) | 15:45 - 15:55 |
| **Friday entry window** | **15:50 - 16:00, gated on the exit half being done** |
| settlement (`settle_time`) | 16:20 |

The order is **exit window -> settlement -> new entry**, and the new entry must never consume a slot or a
buying-power assumption the exit half has not finished releasing; an entry that fires first would change
what the exit measured.

**Ordering is enforced by state, not by the clock.** The window opens at 15:50, inside the exit window's
tail, and the entry is blocked until this session's exits have completed. That buys twenty attempts at a
30s cadence instead of ten and lets the entry start as soon as the exits are done. The gate is necessary:
the phase order inside a tick is *dispose -> enter -> mark -> manage*, so entry runs BEFORE management, and
a window with no state gate would let one tick open the new week before the old week's exits fired.

> **The gate is "every arm that intends to exit today has done so", never "the book is flat".** `path`
> deliberately never closes (it holds shorts to settlement and rides the longs over the weekend), so a
> wait-until-flat condition is never satisfied and the entry would silently never fire, a deadlock that
> looks exactly like a skipped week. Read the pending-exit verdicts (`db.pending_closing_exits`, which
> must exclude any arm whose base is `path`), not the position count.

## Two costs

- **Ex-dividend refusals are more common, and asymmetric.** Entry refuses the whole week when a declared
  ex-date falls in `[entry_session, back_expiration]`; entering three days earlier widens that span. The
  two regimes skip different weeks, so they are **not** a matched set week-for-week, and any comparison
  must say so rather than assume alignment.
- **Positions and buying power roughly double.** Two regimes each enter one double calendar a week, which
  is why the regime is off by default.

## The measurement: the paired debit

`dc_7_10` differs from `dc_4_7` in entry timing and in the strikes each picks from its own expected move.
Read as two independent strategies, that is valid; the problem is variance. Week-to-week P&L swings with
strike placement and the spot path by far more than a weekend's differential decay is worth, so a small
systematic edge sits under a much larger noise term for years.

If both entrances hold the **same strikes and the same expirations** they hold the same contracts, so from
Monday onward their value paths are identical. The whole P&L difference is then what each paid:

> `weekend capture = D_monday - D_friday`, net of the cost stack,

where `D` is the structure's debit at each entry moment. This nets weekend decay against weekend gap risk,
which is the trade-off in question. So on Monday, at the Monday entry moment, the module re-prices the
strikes the Friday regime chose (`engine.price_at_strikes`) and records one row per (week, side) in
`dc_paired_debits` with both debits. It records itself from the Monday entry pass and does nothing on a
week the Friday regime holds nothing. The Friday regime supplies the realistic answer and exercises the
machinery (ordering, execution gate, ex-div refusals, real exits on real books); the paired debit supplies
the low-variance one.

Two small caveats on "identical from Monday onward": an exit rule expressed as a percentage of debit is
keyed to a different base in each regime, and the Friday entrant is exposed to stops or targets over the
weekend that the Monday entrant is not in the trade for.

`dc_paired_debits` has rows only from 2026-10-19, and past weeks cannot be backfilled (the cache keeps no
Monday-morning history). The retirement bar below counts from that date.

## Measurement rules this regime inherits

1. Net of the full modelled fee and slippage stack; gross is not a result (rule 1).
2. `dc_7_10` never pools with `dc_4_7` on any surface, the headline included (rule 4).
3. A refused mark is still a row (rule 6); a Friday-entered week with a hole in its path is
   `derivable: False`, never zero.
4. Its arrival is a journaled boundary for the module even though it pools with nothing: the ledger gains
   a population. It was never journaled for the first entry (2026-09-04); say so when reading across it.
5. It is an **entry-timing** experiment inside a module whose purpose is the exit question. Read the exit
   grid per regime and never average across them.

## Checking a Friday afterwards

The regime reports on itself. After the window closes with nothing entered it journals
`friday_week_skipped` into `dc_decisions`, with the **dominant attempt outcome** as the detail, which is
the whole diagnosis (the two silences are otherwise identical). The window ends at the bell, when the
resident loop stops, so the row is written by the first **off-session** tick after it
(`calendars-paper-offsession`, every 60 s; the settle pass included). A week with the off-session job
switched off journals nothing. Rows exist only from 2026-10-12; two earlier skipped Fridays have none.

| detail | what it means | what to do |
|---|---|---|
| `awaiting_session_exits` | **The exit gate never cleared. This is the deadlock, and it is a BUG.** | Read `db.pending_closing_exits`; it must exclude any arm whose base is `path`. |
| `no_fresh_quotes` | The feed could not price the structure in the window. A market or ops outcome. | Check the producer: `reconnect_count`, and the watchdog's churn finding. |
| a plan refusal (`no_intersection_strike`, `non_positive_debit`, ...) | The structure could not be built from that snapshot. | Ordinary; the module refusing rather than failing. |
| `ex_dividend_week` / `dividend_calendar_lapsed` | The declared calendar refused the week. | Expected, and more often than Monday's because the span is wider. |

A successful Friday leaves four `dc_positions` rows (two arms x two sides) with `entry_session` on the
Friday, `week_of` on the following Monday and a `dc_7_*` structure tag. Confirm the ordering held by
checking that the expiring Monday-regime positions' `closed_at` precedes those rows' `entry_time`.

## The retirement bar

A **floor for opening the question**, declared before either regime had a result. Nothing retires itself.
The advisor reports and a human decides: the advisor's contract is bounded, expiring parameter advice on a
synthetic `advised:` book, with no verb for retiring a population. The per-structure comparison (`dc_4_7`
against `dc_7_10`: net, return on risk, sessions, effective n, and the exit grid per regime) belongs in the
advisor's fact pack so every checkpoint reads both side by side.

The question may be opened once all three hold:

1. **20 completed weeks in both regimes.** Weeks, not positions: a week is one market event however many
   arms it wrote. At one per week that is roughly two quarters, long enough to span more than one vol
   regime.
2. **At least 15 of those weeks paired**, both regimes entered the same week. The paired count, not the
   total, is the comparison's effective n. Paired weeks count from 2026-10-19.
3. **A paired-debit difference whose sign is consistent** across at least 70% of paired weeks. The
   headline number is `monday_debit - friday_debit`; a mean that is positive only because two weeks
   dominate it is not an edge.

The decision is then still a human one, journaled as a measurement break, with the losing regime's rows
kept as history. If the bar is met and the difference is not material, the honest outcome is to retire the
Friday regime and keep the simpler design. Only a mechanical discovery (the ordering proving unsafe, or
the weekend gap producing an assignment path the module does not model) moves the bar earlier; that is a
correctness question and does not wait for a sample.
