# cherrypick-streamer

**The suite's single market-data producer.** One long-lived daemon holds the DXLink connection and
writes the shared stream cache that every other package reads. Nothing else in the suite writes it.

That makes this the smallest package with the largest blast radius: if it stops, every module keeps
running and quietly stops seeing the market. A 34-hour silent stall from an external data dependency
is the incident half the suite's invariants cite as their reason, and it is why the orchestrator
watches this daemon more closely than anything else it runs.

## What it produces

`~/.cherrypick/data/marketdata/stream_cache.db` — quotes, greeks, open interest, per-symbol summary
(day open/high/low, previous close), and the opening range. Consumers open it **read-only**. The path
is deliberately a neutral scope owned by no trading module; override it with `source.stream_cache_db`.

Two things worth knowing about what lands there:

- **Open interest, and therefore GEX, exists only because this daemon subscribes DXLink Summary** for
  each underlying's ATM window. No streamer, no GEX.
- **The opening range is captured here, not by a consumer** (`orb.py`): each underlying's 9:30–9:35 ET
  high/low is accumulated from live Trade ticks and written to `orb_ranges` when the window closes. It
  lives here because a consumer's tick cadence is not guaranteed to land inside a five-minute window —
  the streamer sees every tick. This is a generic hook, not MEIC policy; any consumer reads the table.

## Who gets streamed: the subscription registry

The daemon does **not** hold a hardcoded symbol list. Each consumer writes one file,
`~/.cherrypick/state/stream_requests/<module>.json`, and the streamer streams the **union**:

- `symbols` — underlyings, each bringing a spot subscription, an ATM window, GEX, and the opening range.
- `leg_sources` — `{db, query}` specs. The streamer opens each DB read-only and re-runs the module's
  `SELECT` every poll, keeping each returned symbol subscribed beyond the ATM window. This is how MEIC
  keeps the legs of an open iron condor fresh. (`legs` is a static list for a module that would rather
  name symbols than query for them.)

The coupling surface is **data plus the module's own SQL — never code**, so no package imports another.
A consumer writes only its own file; the streamer only ever reads them.

## Running it

```bash
python run.py                             # foreground daemon (Ctrl-C / SIGTERM to stop)
python run.py --status                    # one JSON health object, then exit
python run.py --stop                      # SIGTERM a running daemon
python run.py --symbol SPX --symbol XSP   # override configured symbols for this run
python run.py --secrets-set               # store the shared tastytrade OAuth bearer secrets (hidden input)
```

Config: `~/.cherrypick/config/streamer.json`, or copy `config.example.json` → `config.json` in this
package. Credentials live in the OS keyring only, under the shared `meicagent` service — the daemon
needs just the two bearer secrets and never makes an account-scoped call.

Normally you do not start this by hand: `cherrypick install` starts it, and the orchestrator keeps it
alive.

## How the orchestrator supervises it

The streamer gets its own supervisor job, `streamer-health`, separate from the 10-minute watchdog tick
and running every 60 s inside the session. That is deliberate — the failure window here is
unrecoverable in a way nothing else's is:

- **The 09:30 deadline is hard.** A producer that is down through 09:30–09:35 loses that day's opening
  range permanently; there is no backfill.
- **A restart is not instant.** After a restart the daemon needs a settling window (~240 s) before its
  data is trustworthy, so "restart it at 09:29" is already too late. Aim to be alive by ~09:20.
- **Death is not the only failure.** The 34-hour stall was a live-but-silent socket: the process was up
  and the connection was open, and no data was moving. So supervision restarts on **silence**, not just
  on a dead PID.

If the streamer needs tighter watching, the answer is a shorter cadence on this job — a config value —
never a faster full watchdog tick.

## Boundaries

- **Exactly one producer writes the cache.** MEIC's in-module streamer writes the same file and is
  disabled; it survives only as the rollback path. Two producers means two DXLink connections into one
  account. A PID single-instance guard plus the orchestrator starting only one is what enforces this.
- **No trading policy lives here.** No account REST poller, no HTTP API, no order path. Those belong to
  a trading module's wrapper (`packages/meic/src/cherrypick/meic/streamer.py`), which layers them onto
  the same shared engine.
- **The engine itself is in `cherrypick.core`** (`core.streamer`, `core.streamcache`). Do not fork it
  into this package — the GEX math once drifted ~75× when it was copied.

Development guidance and the full invariant list: [CLAUDE.md](CLAUDE.md). Why the streamer was split
out of MEIC: [docs/streamer-package-plan.md](../../docs/streamer-package-plan.md).
