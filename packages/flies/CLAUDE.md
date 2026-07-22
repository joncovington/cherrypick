# cherrypick-flies

0DTE net-credit butterflies on SPX — the "profit forest". A **paper module**: it measures whether the
strategy makes money net of costs, and it is built so that a negative answer is a usable result rather
than something to tune away.

## What the strategy actually is

A long symmetric butterfly pays `max(0, W - |S - K|)` at expiry. That is bounded to `[0, W]` and is
never negative. So a fly **held for a net credit** cannot lose at expiry — its worst case is the credit
itself. Several of them at different strikes give a risk graph that is green across a band with a peak
at each centre: a forest of profit zones sitting on a positive floor.

You cannot simply buy such a fly. Paying a negative debit for a non-negative payoff would be arbitrage.
The credit has to be manufactured, and there are exactly two ways, both of which were observed in real
order chains and both of which this module implements:

**`legged`** — sell a defined-risk credit spread for credit `C`, then buy the spread that completes it
into a symmetric fly for debit `D < C`. You end up holding a butterfly for `C - D` of net credit. This
produces a genuine, unconditional, **per-position** floor.

**`outright`** — buy a cheap fly for a debit, paid for out of premium the book already took in. This
manufactures nothing; it spends an existing floor. The result is a **book-level** floor that only holds
inside the funding spreads' wings.

Keeping those two straight is the module's main job. See "The honesty rules" below.

## Layout

| file | role |
|---|---|
| `src/fly.py` | payoffs, quote pricing, fees, position and book floor math. Pure. |
| `src/engine.py` | centre selection, entry gates, the completion gate, settlement. Pure. |
| `src/provider.py` | builds snapshots from MEIC's stream cache, read-only. No decisions. |
| `src/paper_loop.py` | session driver: fetch, run every arm, settle at the bell. |
| `src/book.py` | wires engine decisions to the paper DB; one book per (date, arm, symbol). |
| `src/db.py` | `fly_positions` (ledger) and `fly_books` (roll-up with the floor's price band). |
| `src/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `src/dashboard.py` | loopback HTTP dashboard: Today / History / Performance. |
| `src/section.py` | the compact `cherrypick.core.viz` card for the suite dashboard. |
| `src/eod.py` | `paper-eod-<day>.md` and `eod-analysis-<day>.md`. |
| `src/cli.py` | `once` / `settle` / `status` / `dashboard` / `section`. |
| `tests/fixtures/books.json` | three real tastytrade order chains, transcribed. |

## The read side

**Everything reads through `analytics.py`.** MEIC grew three call sites that disagree about what "net"
means — its Today grid uses raw `pnl`, its profile comparison uses `pnl - fees` — so here the dashboard,
the section card, and the EOD writer all share one layer and a test asserts the headline figure agrees
across surfaces.

**Two journal tables, deliberately.** `fly_decisions` records *why* every entry was made or refused,
collapsing consecutive identical reasons into one counted run (a gate that blocked all morning is one
row with `occurrences: 18`). `fly_iterations` records what each arm *wanted* on each iteration, before
any gate could veto it — collapsing would destroy exactly what arm divergence needs.

**And a feed ledger, `fly_snapshots`.** One row per (tick × symbol) recording what the feed gave us —
`status` "ok" with the quote counts, or the provider's refusal reason. It is separate from
`fly_iterations` on purpose: that table is per-arm and only written when a snapshot *succeeds*, so a
refused tick never reaches the arm loop and, without this, leaves no trace at all. That is the gap this
closes — a stretch of the day with refused rows is a feed problem (`no_fresh_quotes`, `no_spot_price`);
a stretch with *no rows at all* is the loop not running. Before `fly_snapshots` those two silences were
identical on every read surface, and `quote_stats` reached only the module log. The timeline now labels
each of its gaps accordingly ("no data · 100m · loop silent" vs "· no_fresh_quotes ×20"), which is how
2026-07-20's outage is legible as an ops failure rather than a quiet market. Recording lives in
`paper_loop.run_once` on both the built and the refused path; it is pure telemetry and touches no
decision.

**Four measurements this strategy needs and generic P&L reporting cannot give:**

- **Completion rate** — how often a leg-in actually became a fly. If this is near zero the strategy is
  short verticals wearing a costume, and no P&L on the completed ones changes that.
- **The counterfactual** (`best_completing_debit`) — for misses, whether *the market never offered it*
  or *our fee buffer refused it*. Identical in the P&L, opposite remedies.
- **Completion latency** — a fly that took 40 minutes and 8 points of drift is far likelier to fill live
  than one that appeared for seconds. This is the paper-vs-live gap, measured.
- **Arm divergence** — how often the arms picked different centres. High agreement means the experiment
  cannot separate them, which is a finding to surface in week one, not month three.

**The last three of those live on a time axis, so the dashboard has one.** `analytics.session_timeline`
assembles the day from rows already written — spot and every arm's wanted centre on each iteration,
entries and completions, and each leg-in as a span running to its completion, so latency is a length
beside the drift that bought it. `settle_now` replays the book at each tick: what it would have been
worth had the session ended at that moment and that price. That is an expiry payoff evaluated at a
live spot and **not a mark** — nothing here is quoted intraday, and the label says so on the page.

Replaying requires rewinding. A legged position is a short vertical until it completes and a fly
afterwards, but the stored row only ever holds its latest state; drawing straight from it would show
the morning as though every fly existed from the moment its credit spread was sold, which asserts the
per-position floor rule 3 exists to withhold. The rewind is exact, not approximate — the completing
purchase is a 2-leg vertical, so the pre-completion fee is `vertical_open_fee` and the pre-completion
net is the recorded `credit`.

The dashboard binds to **127.0.0.1 only** and draws its charts with plain canvas — no CDN, so it works
offline and adds no third-party dependency to a surface whose only job is reading a local SQLite file.
Both charts refuse to smooth over what they do not know: the timeline **breaks its lines across a gap**
in the record rather than interpolating a shape through it (the 2026-07-20 session has a 100-minute
silence, and a straight segment across it would read as a calm market), and the payoff curve draws one
line per arm rather than a blended book.

## Data source

This module **runs no streamer**. `provider.py` reads MEIC's `stream_cache.db` read-only — the same
piggyback path `cherrypick-gex` uses — so the suite runs one streamer rather than three, and flies can
never disturb the loop that is actually trading. MEIC's streamer must be running and subscribed to the
symbols in `config.json`; open interest, and therefore GEX, exists only because it subscribes DXLink
Summary for its ATM window.

The provider refuses rather than guesses. Stale quotes (older than `max_quote_age_seconds`), crossed
quotes, a missing spot, an empty chain — each returns `{"ok": False, "reason": ...}`, which the loop
logs and steps past. Refusals are ordinary and frequent; they are not errors. `quote_stats` is recorded
on every snapshot so a barren session can be read afterwards as "the data was thin" rather than
mistaken for "the strategy found nothing".

`src/_core` is the `cherrypick-core` submodule (same URL and pinned SHA as every other package).
`cherrypick.core.fees` supplies the fee schedule and `cherrypick.core.gex.compute_gex` the per-strike
GEX profile — neither is reimplemented here.

## The three arms

Separate books, differing **only** in where and when they centre a structure. Every gate is shared, so
the comparison measures the signal rather than a bundle of confounded changes.

- `gex` — centre on the strongest positive per-strike net GEX near spot. Degrades to ATM when the
  streamer has no OI cached yet, and records `center_reason` so those samples can be excluded later.
- `time_window` — ATM, entering only inside configured windows. The windows are **not** ranked; we
  have no intraday history to rank them with. Each trade is tagged with its window and the ranking
  comes out of our own sessions.
- `control` — one fixed midday ATM entry. Without this, a profitable `gex` arm would prove nothing.

## The honesty rules

These are the constraints the module exists to enforce. Breaking one makes the numbers worthless.

1. **Every result is net of the modeled fee and slippage stack.** This suite has already recorded a
   trade collecting $4.00 against $4.96 of fees. Gross credit is not a result.
2. **"Risk-free" is a measurement, never an assumption.** `position_floor` is computed after fees and
   `is_risk_free` can and does return `False` for a fly with a positive gross credit.
3. **A per-position floor and a book-level floor are different claims.** `book_floor` returns
   `unbounded_below` and a price `band` precisely so a book leaning on open short verticals is never
   reported as unconditionally safe.
4. **The uncompleted branch is reported separately.** When a legged entry never completes, you are
   holding an ordinary credit spread with full defined risk. `completion_rate` is expected to be the
   number that decides whether this strategy is real.
5. **No adjustments after establishment.** No stops, no wing moves — hold to cash settlement. v1 is
   measuring a base rate, and an adjustment rule tuned before a single completion rate exists would be
   fitting noise.
6. **If the floor comes out negative after fees, that is the finding.** The answer is to stop, not to
   loosen `fee_buffer` until the numbers look better.

## Guardrails (suite-wide)

- Paper only. SPX/XSP only — both European cash-settled, so assignment is structurally impossible and
  there is no early-exercise machinery to get wrong.
- **No AI, no MCP, and no network on any decision path.** `fly.py` and `engine.py` are pure functions
  over a pre-fetched snapshot. Learning happens offline in the orchestrator's read side (`report`,
  `calibrate`, `eod-insight`) over closed rows — never inside the loop.
- Credentials in the OS keyring only. Account numbers masked to `****1234`.
- Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI attribution.
- Instruction files hold no code.

## Status

**Complete and tested:** decision engine, floor accounting, paper DB, snapshot provider, session
driver, CLI, and the orchestrator `fly_book` wiring across all four schema registries. 185 tests,
including a provider suite built against the real `cherrypick.core.streamcache` DDL so an upstream
schema change fails here rather than silently producing empty snapshots. The package runs in CI (its
own cell in the `.github/workflows/ci.yml` matrix, `ruff` + `pytest` on every push and PR).

**First live paper session: 2026-07-20.** Eleven structures, 80% completion rate, +$14.89 net —
which is the floor and nothing more, since no fly finished inside its wings. Fees were 82% of gross.
Two things to keep watching, both visible in that one session: completions arrived only after 10–21
points of drift away from the centre (the mechanism that makes completion cheap is the one that
walks spot out of the wings), and `control` vs `time_window` wanted the identical centre on 141 of
141 shared iterations, so only the disjoint windows separate them. `gex` vs `control` disagreed 84%
of the time and is the comparison with real power.

**Settlement is marked in the database, not on disk.** `session_already_settled` asks whether every
`fly_books` row for the day is `settled`. It used to ask whether `paper-eod-<day>.md` existed, which
made the marker settable by anything that could write a file — on 2026-07-20 a test run against the
real managed home created that file mid-session, the loop read its own day as finished, and eleven
positions went unsettled under a report describing a fixture. A marker for "settlement happened"
must be writable only by settlement. Tests are isolated by an autouse fixture in `tests/conftest.py`
rather than one each test opts into, for the same reason.

**Settlement is approximate.** `--settle` defaults to the last streamed trade, which is close to but
not the official settlement print. The difference is systematic rather than random, and a position
centred within a point of spot can settle on the wrong side of its centre because of it. Pass
`--price` with the official print for any book whose result matters.

## If this ever goes live

The engine already returns decisions rather than performing fills, which is the same split MEIC uses,
and `cherrypick.core.broker` carries the write path and governor. Two things must be resolved first,
and neither is a detail:

- **Legging is where live diverges hardest from paper.** In paper the completion gate is a clean
  inequality. Live, step 1 fills and step 2 is a working limit that may sit unfilled or fill worse — so
  the completion rate measured here is an **upper bound** on the live rate, not an estimate of it.
- **`fund_from_open_credit` needs a real buying-power check.** Funding an outright fly from a still-open
  credit spread spends premium that has not been earned.
