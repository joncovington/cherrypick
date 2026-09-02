# The producer's subscription burst — incident record and the fix

*Written 2026-08-24, during and immediately after the incident; the fix section re-checked against
the code and rewritten 2026-08-26. The mitigation described under "What was done during the session"
is live. **Four of the five fixes are built and one item — the heartbeat gap — is still open**; each
is marked individually below rather than by a status line at the top, because the top-level line is
the part that went stale.*

## What happened

The standalone producer crash-looped for the whole first half of the 2026-08-24 session. Every
cycle: connect → subscribe → tastytrade kills the socket ~5s later with `Fatal streamer error:
Your subscription rate is too high` → wait 60s → repeat. **79 reconnects before the mitigation,
against 10 across the previous three sessions combined.**

The producer therefore delivered roughly **five seconds of data per sixty-five second cycle**.
Quotes sat 45–60s stale, and pmcc and bwb refused every evaluation on `no_fresh_quotes` — 23
refusals each and not one usable snapshot until the mitigation landed. The refusals were correct:
the providers did their job and the feed underneath them was broken. **The watchdog never reported
the producer itself** — it was alive, answering `--status` truthfully, and reconnecting. What
surfaced were two downstream module warnings, which is a diagnosis one level removed from the
cause.

## Why now

The failure mode is older than the incident — the first `subscription rate is too high` in the log
is **2026-08-16**, and it fired then on a large option-window re-center (`+242 symbols` at once).
What changed is the size of the burst. On reconnect the producer re-subscribes **everything**, and
by this session that was **12,047 symbol×event-type subscriptions**.

Two declarations account for most of the growth, and neither was wrong to make:

- **pmcc added XSP on 2026-08-23** (its config records "XSP went live 2026-08-23, the same day as
  the redesign") — a second underlying with two extra expirations and, critically, a
  `window_hints` entry of **163 strikes per side**.
- The regime recorder's 24 quote-only legs landed the same weekend. **Measured, these were not the
  cause**: they are ~0.2% of the load, and removing them mid-incident changed the reconnect
  cadence not at all. They are recorded here because they were the first suspect and the
  measurement matters more than the suspicion.

**The 163 is structural, not an escalation artifact.** pmcc's `deep_window_pct: 0.20` asks for
strikes down to `spot × 0.80`; XSP at ~764 puts that at 611, which is 153 strikes at $1 increments,
plus the declared margin of 10. The module computed exactly what it was told to want.

## The waste this exposed

**The producer's window is symmetric, but the need is not.** `streamcache.atm_window_syms` takes
one `strike_count` and returns that many strikes *on each side*. pmcc's deep-ITM long sits far
**below** spot and its short sits **at** spot — it has no use whatsoever for 163 strikes above
spot, yet asking for depth downward subscribes the mirror upward. Roughly **half of the largest
window in the suite is strikes no module will ever read.**

A second, smaller waste: cash legs are subscribed to Trade **and** Quote **and** Greeks. The
2026-08-24 entitlement probe established that indices publish **Trade only** — SKEW, VIX9D, VIX and
VIX1D all printed as Trade and none as Quote — and an index or ETF has no greeks to publish at all.
Every cash leg therefore carries two subscriptions that can never deliver an event.

## What was done during the session (live, and needs journaling)

Mitigation only — enough to get the session working, chosen to avoid touching any module's ability
to trade:

1. **`window_strike_count` 60 → 30** for the base window, via a new
   `~/.cherrypick/config/streamer.json`. That is ±150 points on SPX (0DTE ICs, flies' wings and
   bwb's expected-move entries all sit well inside it) and ±4% on SPY. Per-symbol `window_hints`
   still widen where a module declares a need, since the resolver takes `max(default, hint)`.
2. **pmcc `deep_window_pct` 0.20 → 0.12** (backup at `~/.cherrypick/config/pmcc.json.bak-20260824`).
   An 85–90 delta call at ~21 DTE sits roughly 5% in the money; 12% keeps better than a 2× margin
   over that while cutting the XSP window by a third.

Result: **12,047 → 8,551 subscriptions**, reconnects **0** across the following minutes with
`stale_age_s` at 0, and both starved modules recorded their first `ok` snapshot of the day.

> **⚠️ `deep_window_pct` is a pmcc entry parameter, changed mid-session.** It bounds which strikes
> the entry snapshot can see, so it belongs in that module's own journal as a dated note, and any
> comparison spanning 2026-08-24 should know it moved. It was changed under an outage and should be
> re-derived deliberately — 0.12 is a defensible bound for an 85–90 delta long, not a measured one.

## The fix — what shipped, and what is left

*Status re-checked against the code 2026-08-26. This section used to say "not built" about all five
items; three of them had shipped in the days after the incident and the file was never updated,
which is exactly the failure mode a status line in a document has. It is written as built/open now,
and anything still open says so in its own words.*

1. **Throttle the (re)subscribe burst — BUILT** (`core: pace the streamer's subscription messages`).
   Every subscribe and unsubscribe goes through one choke point in `cherrypick.core.streamer`'s
   `_send_subs`: chunked at `SUBSCRIBE_CHUNK = 200` and spaced by a GLOBAL minimum interval
   (`SUBSCRIBE_PACE_S = 0.15`), so four event types fired back to back cannot stack into one burst.
   A full 12k resubscribe now costs a few seconds against a 240s settling window. This was the
   actual bug — everything else here reduces the odds of tripping the limit; only this removes the
   failure mode.
2. **Asymmetric windows — BUILT 2026-08-26.** A `window_hint` may now be a plain count (symmetric,
   still the common case) or a `{"down": N, "up": M}` declaration; `streamcache.window_span`
   normalizes every form to a `(below, above)` pair, and the union takes the max PER SIDE so two
   modules with opposite needs on one symbol are both served. pmcc declares its deep window
   downward-only, which is the whole point: the strikes an equal distance above spot were the
   largest block of subscriptions in the suite that no module could read. The producer floors both
   sides at its own `window_strike_count`, so a directional hint asks for more on one side and can
   never narrow the base on the other.
3. **Stop subscribing Greeks (and Quote) for cash legs — BUILT 2026-08-26.** The daemon now filters
   both by what a symbol can actually PUBLISH rather than by what it is:
   `streamcache.publishes_quotes` / `publishes_greeks`. Nothing cash-settled has greeks at all, and
   an index has no order book to quote from — the 2026-08-24 entitlement probe found SKEW, VIX9D,
   VIX and VIX1D all printing Trade and none printing Quote. **ETF and single-name legs keep
   Quote**: they have a real book and modules price legs off it. The quoteless set is a DECLARED
   list, not a pattern match on the ticker, because "VIX-prefixed means index" would quietly cost a
   real symbol its quotes the first time a ticker broke the pattern, and a leg with no price at all
   is the expensive failure this suite has already paid for twice (2026-08-14 Summary, 2026-08-17
   Trade). An unlisted symbol keeps Quote: the default is a possibly-wasted subscription, never a
   starved reader.
4. **A declared subscription budget — BUILT.** `streamrequests.estimate_subscriptions` /
   `budget_status` model the cost from the same registry union the producer subscribes from, and
   `watchdog._check_subscription_budget` raises a finding past `DEFAULT_SUBSCRIPTION_BUDGET`
   (12,000). It names the WORST symbol as well as the total, because the 2026-08-24 book was
   dominated by one declaration and a total alone does not say which one to look at. It is an
   ESTIMATE and will not equal the producer's own `subscribed_symbols` — its job is a change in
   order of magnitude, not a reconciliation.
5. **Surface reconnect churn as a finding — BUILT.** `watchdog._streamer_churn_finding` reads the
   `reconnect_count` that `--status` was already reporting and nobody read. 79 reconnects in a
   morning is now a WARN in its own right rather than something inferred from two modules
   complaining about stale quotes — the same lesson the resident-job `starts_in_window` counter
   already encodes: *a process that keeps coming back looks healthy at every instant and is not.*

**Still open from this incident:** the heartbeat gap in the section below. Nothing else.

## Queued with this, same sitting — DONE

**Register the `curve` module.** Done: `modules.curve` is in the orchestrator config and
`state/stream_requests/curve.json` exists, so the module has a supervisor job and the producer
serves VXX's window and target expiration. It was landed after the throttle above, as this section
asked, so VXX arrived on a producer that paces its burst rather than one hoping to stay under an
undocumented ceiling.

The cost is on the record: curve's daily VIX/VIX3M regime series is its declared second product and
its value is continuity, so the sessions between 2026-08-22 and registration are simply missing from
it. The signal half is recoverable — `regime-history` replays the classification from stored
`stream_summary` closes — but a session's live trade record is not. That is also the case item 4
above now covers from the other side: a module declaring an expensive new underlying announces
itself at declaration time, and a module declaring *nothing at all* is visible in the same union.

## A monitoring gap the recovery exposed

Within twenty minutes of the fix, both recovered modules raised `paper data is stale` while
demonstrably healthy — each had entered its position for the session and was writing marks on
every tick.

The freshness check takes the freshest of three signals: the paper DB's mtime, the module log's
mtime, and the module's heartbeat. The first two are **conditional** writes — a WAL database's main
file only moves on a checkpoint, and a log line is a side effect of having something to say — so
they both go quiet for a hold-to-expiry module that has finished entering. The heartbeat is the one
unconditional per-tick signal, and **pmcc, bwb, curve and meic do not write one** (calendars,
console and flies do).

This is the calendars 2026-08-17 lesson resurfacing in the newer modules, and it is milder for a
good reason: a missing heartbeat degrades *safely* — `_resident_silent` returns False for a missing
file, so an unheartbeating module is simply not silence-supervised and nothing restarts it — and
the freshness check only warns. The cost is a false WARN whenever such a module is healthy and
idle, which for a hold-to-expiry book is most of the session. Fix is small: beat at the top of each
tick through `cherrypick.core.home.heartbeat_path`, the way flies' `paper_loop._beat` already does.

## What was re-derived afterwards (settled 2026-08-24, after the close)

- **`window_strike_count` stays at 30 — now by measurement, not by incident.** It serves only the
  symbols no module widens by hint (SPX, SPY). At 30 per side that is ±150 points on SPX's 5-point
  strikes and ±4.3% on SPY's, while meic's 10–16 delta condors, flies' wings, bwb's expected-move
  entries and calendars' EM strikes all sit far inside. Restoring 60 would have doubled those
  windows' subscriptions to serve strikes nothing reads. Pacing means 60 is now *safe*; it is still
  not *needed*.
- **pmcc's `deep_window_pct` restored 0.12 → 0.20, and the cut turned out to be wrong.** Measured
  against the live chain, TQQQ's 85–90 delta call at ~21 DTE sits **15.2–18.8% ITM** (strikes
  56.0–58.5 against a 68.995 spot), so a 12% bound stopped short of the entire target range and
  would have made the module's long unreachable on that symbol. It never bit only because TQQQ was
  independently refusing on a lapsed dividend calendar. Full record:
  `packages/pmcc/docs/window-parameters.md`.
- **The regime recorder's legs** were reverted during diagnosis and restored the same session; they
  are ~0.2% of the load and were never implicated.

The general lesson worth keeping from those two: **an emergency parameter cut is a guess with a
deadline.** One of these two proved harmless and one proved wrong, and the only thing separating
them was measuring afterwards rather than leaving the incident value in place.
