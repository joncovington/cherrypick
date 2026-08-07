# Flies FAQ

Questions worth answering once, here, rather than re-deriving each time they come up.

## What is the "flies" strategy?

It trades 0DTE (same-day-expiring) net-credit butterflies on SPX/XSP index options — nicknamed
the "profit forest." A long butterfly's payoff at expiration is bounded between $0 and its
maximum width and can never go negative, so a butterfly held **for a net credit** (paid more up
front than the position could ever cost back) has a worst case that's still a profit — a
genuine, unconditional floor.

No one sells a butterfly for a net credit directly — that would be free money, and free money
doesn't trade — so the module manufactures the credit itself, two ways:

- **Legged** (the default mode): sell a defined-risk credit spread, then buy the spread that
  completes it into a full butterfly for a smaller debit once the market moves the right way.
  The difference (credit − debit) becomes the position's floor. If the completing leg never
  gets cheap enough to buy, the position simply stays an ordinary credit spread with full
  defined risk instead — that's the expected common outcome, not an edge case, and it's tracked
  and reported separately (`completion_rate` is the number the whole thesis turns on).
- **Outright** (currently disabled — see `README.md`'s "Status"): buy an already-cheap
  butterfly directly, funded from premium the day's book has already collected. This doesn't
  manufacture a new floor of its own; it spends part of an existing one, so its safety only
  holds up at the book level, within the price range the funding trades cover.

Two further entry constructions are also running, each with an ATM twin so the construction and
the centring can be separated (see the arms question below): **`debit_first`**, which is `legged`'s
two trades in the opposite order — buy the debit vertical first, complete by *selling* the credit
spread once spot comes back — and **`bwb_roll`**, which enters a broken-wing butterfly whole for a
credit and later rolls its wide wing in.

Several parallel variants ("arms" — `gex`, `time_window`, `control`, `debit-first`, `bwb`, their
ATM twins `debit-first-atm` and `bwb-atm`, plus a wing-width sweep) run side by side every session,
each changing exactly *one* variable (how the centre strike is picked, when it trades, how wide the
wings are, how the credit is constructed) against the same `control` baseline, so results are
comparable rather than a mix of confounded changes.

What the module is actually *for* isn't the trade idea itself — it's measuring whether either
way of building the credit survives real trading costs once a position actually settles:
commissions, fees, bid/ask slippage, and (as of 2026-07-30) the $5-per-ITM-strike exercise-assignment
fee on any leg that finishes ITM. See the [module README](../README.md) for the full
walkthrough and current results, and `CLAUDE.md`'s "honesty rules" for the specific claims this
module is built to refuse to make (a per-position floor is not a book-level floor, an
uncompleted credit spread is not risk-free, and so on).

## What is the trade-off in the `bwb_roll` arm — and is it survivable?

**Unknown, and deliberately not yet claimed either way.** `CLAUDE.md` pointed here for this answer
before the section existed, so this is that answer, written to say what is actually established.

A broken-wing butterfly is entered *whole* for a net credit: a near wing at the usual `wing_width`
on the protected side, a far wing at `wing_width × bwb_far_width_ratio` on the risk side. The extra
room bought with the wider wing is what manufactures the credit — and it is also a **real negative
tail**. Unlike a symmetric fly, this position genuinely can lose: its payoff floor is
`wing_width − far_width`, not zero, and `fly.position_floor`'s `bwb` branch reports that honestly
rather than pretending it is bounded at zero.

The position is meant to become an ordinary symmetric fly by **rolling**: sell the held far wing,
buy the strike the symmetric fly needs (`centre ∓ wing_width`), which is a debit vertical spanning
exactly `far_width − wing_width` — the tail being bought back.

**The researched trap** is that this roll should cheapen under precisely the drift that makes the
position profitable, and grow expensive precisely when the tail is threatened. Spot moving *away*
from the structure carries the roll further out of the money and makes it cheap; spot running
*toward* the far wing makes the roll dear at the exact moment you most want it. If that is how it
behaves in practice, the arm's protection is unavailable whenever it matters, and the construction
does not work.

**What we do not yet know is whether that is true**, because the first three sessions of this arm
measured two bugs rather than the market (both fixed 2026-08-07):

- The roll priced the wrong legs — it bought the *near* wing, which the position already holds,
  giving a spread of width `far + wing` instead of `far − wing`, **3× too wide** at the default
  ratio. Failing rolls came in at a median 3.58× the credit against a defect worth exactly 3×.
- The side rule was `legged`'s, which put the roll spread **in the money**. An ITM vertical cannot
  be bought below its intrinsic, so the roll could never clear its price gate at all.

All 25 positions from 2026-08-04..08-06 are void as a result. The trap remains a hypothesis with no
clean measurement behind it.

**One thing the corrections did establish**, by arithmetic rather than by sampling: a bwb credit
decomposes as `(C(K+w) − C(K+f)) − butterfly(K−w, K, K+w)`, so it is capped by the gap between the
two candidate far wings — and that gap collapses as the structure is pushed further out of the
money. **Safety and credit trade against each other directly here.** Moving the tail away from spot
is exactly what shrinks the credit, which is why `min_bwb_credit_pct_of_tail`, rather than the price
gate, is now expected to be the binding constraint on entry.

## Why not trade SPY, /ES, or /MES instead of SPX/XSP, to shrink the assignment fee's bite?

A larger notional — SPY (~$550–600) or the E-mini/Micro E-mini S&P 500 futures (/ES ~$250k+,
/MES ~$25k notional per contract, vs. XSP's ~$70–80) — would shrink the $5-per-strike
exercise-assignment fee's bite as a fraction of a structure's value. That's the same lever the
width-arm sweep (`control` through `width-5`) is already testing, just applied to the underlying
instead of the wing. It doesn't work for any of these three, and the reason has nothing to do
with fee size: **SPX and XSP are European and cash-settled. SPY and futures options on
/ES and /MES are all American-style, and none of them settle to cash on exercise.** That
distinction is a hard requirement in this module's own guardrails, not a stylistic preference —
see `config.example.json`'s `_symbols_note`: *"SPX and XSP are the only symbols this module
should ever run — both European cash-settled, so a short middle strike left open into
expiration settles to cash and assignment is structurally impossible; a physically-settled
symbol would need the assignment machinery MEIC carries."*

Two concrete things break once exercise isn't a cash-settlement event:

- **Early assignment.** A cash-settled index option can only be exercised at expiration, so the
  cost is *scheduled*: the module knows the exact moment it can be charged, prices it in advance
  (`fly.expire_fee`), and reserves it in every position's floor (`fly.WORST_CASE_ITM_LEGS`). An
  American-style option can be assigned at **any point** it's ITM, with no warning and no window
  to react in first — a position could be broken open mid-session, which turns a known, bounded,
  reservable fee into an unbounded structural risk the floor could not honestly be computed
  against at all. Per CME Group's own FAQ,
  E-mini S&P 500 options are explicitly American-style and exercisable "until 7:00 p.m. CT on
  any business day the option is traded," not just at expiration.
- **The position doesn't resolve to cash.** The strategy's whole thesis — a long butterfly's
  payoff is bounded to `[0, W]`, so a fly held for a net credit can't lose — assumes the
  position resolves to cash at expiry. On SPY, assignment delivers actual shares. On /ES or
  /MES, CME's own documentation is explicit that exercise creates a live futures position
  ("the futures positions created as a result of the exercise of the options... will be marked
  to market at the daily settlement price of the underlying futures") — a leveraged, margined
  position, and a bigger overnight risk than share delivery, not a smaller one. This module has
  no machinery to manage or close out either outcome, which is the same reason `CLAUDE.md`'s
  guardrails call out "no early-exercise machinery to get wrong" as a property SPX/XSP give you
  for free and a physically-settled symbol would not.

The fee-avoidance argument for switching doesn't hold up on its own terms either: tastytrade's
equity/ETF assignment fee is **the same $5** as the index-option exercise fee this
module already models (`cherrypick.core.fees.ic_expire_fee`). None of SPY, /ES, or /MES would
save anything on the cost being escaped — each would only add early-assignment risk this module
isn't built to hold. (SPX/XSP exist as separate Cboe products specifically to fill the gap
CME's own futures-options complex on the S&P 500 doesn't: there is no European-style,
cash-settled route through /ES or /MES options at all.)

If the real question underneath is *"is XSP too small a notional for a flat $5-per-strike fee to
be survivable,"* that's exactly what the width-arm sweep is already measuring, and **SPX** — same
cash-settled, no-early-exercise mechanics, ~10x XSP's notional — is the lever to pull if XSP
proves too small. Not a physically-settled or futures-settled symbol.
