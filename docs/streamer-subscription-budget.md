# The producer's subscription burst — incident record and the fix

*Written 2026-08-24, during and immediately after the incident. The mitigation described under
"What was done during the session" is already live; **the fix under "The actual fix" is NOT built**
and is deliberately deferred to outside market hours.*

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

## The actual fix (not built — do this outside market hours)

In rough order of value:

1. **Throttle the (re)subscribe burst.** The producer should chunk its subscription messages and
   pace them under the broker's rate limit rather than emitting everything as fast as the socket
   accepts it. This is the actual bug: a burst that trips the limit cannot recover by retrying the
   same burst 60 seconds later, which is precisely the loop observed. Everything else here reduces
   the odds of tripping; only this removes the failure mode.
2. **Asymmetric windows.** Let a `window_hint` express depth per direction — `{"down": 163, "up":
   12}` — or let the resolver take a (below, above) pair. This halves the largest windows in the
   suite with no loss of anything a module reads. It also removes the incentive to solve depth by
   inflating a symmetric count.
3. **Stop subscribing Greeks (and Quote) for cash legs.** `daemon._extra_subscriptions` currently
   sends all three for every non-option leg. Trade is the only one that delivers, and the recorder
   already reads `stream_trades` accordingly.
4. **A declared subscription budget, checked when a request file changes.** The suite discovered
   its ceiling at the open, in production. A budget — even a logged warning at, say, 8,000 — turns
   "a module declared something expensive" into a message at declaration time rather than a
   starved session. Drive it off the same `streamrequests` union the producer subscribes from, so
   it cannot disagree with reality.
5. **Surface reconnect churn as a finding.** `--status` already reports `reconnect_count`; nothing
   reads it. 79 reconnects in a morning should be a WARN in its own right, not something inferred
   from two modules complaining about stale quotes. This is the same lesson the resident-job
   `starts_in_window` counter already encodes for module loops: *a process that keeps coming back
   looks healthy at every instant and is not.*

## Queued with this, same sitting

**Register the `curve` module.** It has never run: built 2026-08-22 and wired into nothing — no
`modules.curve` block in the orchestrator config, therefore no supervisor job, no stream request,
no data directory. (bwb was built the same day and *was* registered; curve was missed beside it.)
Everything else is already in place — `curve_vx` is in the schema registry and report, reconcile,
trade_notifier and eval_activity all account for it — so the missing piece is one config block.

It belongs in this sitting rather than during a session because **VXX is a real underlying, not a
quote-only leg**: it adds a chain window plus its target expiration (~1,000 subscriptions, taking
the producer to roughly 9,500), and a new underlying forces a producer restart and trips the
watchdog's subscription-growth recycle. Land it *after* the throttle above, so VXX arrives on a
producer that paces its burst rather than one hoping to stay under an undocumented ceiling, and
watch `reconnect_count` while it settles.

Note what this cost: curve's daily VIX/VIX3M regime series is its declared second product and its
value is continuity. The signal half is recoverable — `regime-history` replays the classification
from stored `stream_summary` closes — but a session's live trade record is not. This is also
exactly the case for item 4 above: a module declaring an expensive new underlying should be a
message at declaration time, and a module declaring *nothing at all* should be visible too.

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
