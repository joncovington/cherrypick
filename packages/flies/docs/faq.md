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

Several parallel variants ("arms" — `gex`, `time_window`, `control`, plus a wing-width sweep)
run side by side every session, each changing exactly *one* variable (how the centre strike is
picked, when it trades, how wide the wings are) against the same `control` baseline, so results
are comparable rather than a mix of confounded changes.

What the module is actually *for* isn't the trade idea itself — it's measuring whether either
way of building the credit survives real trading costs once a position actually settles:
commissions, fees, bid/ask slippage, and (as of 2026-07-30) the $5/contract exercise-assignment
fee on any leg that finishes ITM. See the [module README](../README.md) for the full
walkthrough and current results, and `CLAUDE.md`'s "honesty rules" for the specific claims this
module is built to refuse to make (a per-position floor is not a book-level floor, an
uncompleted credit spread is not risk-free, and so on).

## Why not trade SPY, /ES, or /MES instead of SPX/XSP, now that the pre-close ITM exit closes ITM positions anyway?

A larger notional — SPY (~$550–600) or the E-mini/Micro E-mini S&P 500 futures (/ES ~$250k+,
/MES ~$25k notional per contract, vs. XSP's ~$70–80) — would shrink the $5/contract
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
  pre-close ITM exit (`engine.evaluate_pre_close_exit`) gets to *choose*, in the closing minutes,
  whether buying a position back is cheaper than the assignment fee it would otherwise incur. An
  American-style option can be assigned at **any point** it's ITM, with no warning and no window
  to react in first — the entire mechanism this module built to dodge the fee depends on
  assignment being a scheduled, predictable, expiration-only event. Per CME Group's own FAQ,
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
equity/ETF assignment fee is **the same $5/contract** as the index-option exercise fee this
module already models (`cherrypick.core.fees.ic_expire_fee`). None of SPY, /ES, or /MES would
save anything on the cost being escaped — each would only add early-assignment risk this module
isn't built to hold. (SPX/XSP exist as separate Cboe products specifically to fill the gap
CME's own futures-options complex on the S&P 500 doesn't: there is no European-style,
cash-settled route through /ES or /MES options at all.)

If the real question underneath is *"is XSP too small a notional for a flat $5/contract fee to
be survivable,"* that's exactly what the width-arm sweep is already measuring, and **SPX** — same
cash-settled, no-early-exercise mechanics, ~10x XSP's notional — is the lever to pull if XSP
proves too small. Not a physically-settled or futures-settled symbol.
