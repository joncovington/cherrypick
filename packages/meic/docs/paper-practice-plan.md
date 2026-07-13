# Build plan — `paper_practice.py` (0DTESPX practice-session backtester)

A **side backtesting project**: a ToS-compliant historical backtester that drives our real MEIC
decision logic through 0DTESPX.com **practice sessions** (server-side sim) instead of extracting
their data. Proven end-to-end by the spike + one-day prototype (2026-07-13). This is the sanctioned
counterpart to the disabled `paper_replay.py` (see [0dtespx-api.md](0dtespx-api.md) for the ToS that
kills the raw-extraction approach).

## Goal & scope

- **Validate the SPX side** of the paper experiment cells fast, on realistic fills/settlement:
  `large-spx`, `explore-spx-tightcredit`, and the four ladder tiers on SPX. **SPX-only** — the
  platform has no XSP/QQQ/IWM, so the small-iwm / medium-qqq / small-xsp / medium-xsp-wide /
  explore-xsp-loosecredit cells are **out of scope** here and stay forward-paper-only (tastytrade).
- **Separate from** forward paper (`paper_loop.py`, tastytrade cost basis) and from the disabled
  `paper_replay.py`. Results live on **0DTESPX's cost basis** and are tagged as such — not directly
  comparable to tastytrade P&L.
- **Reuse, don't fork, the strategy logic**: import `paper.evaluate_entry`, `load_profiles`,
  `_merged_params`, `all_profile_names`, `union_widths_for_symbol`, the staggering, and the profile
  registry unchanged. Only the fill / mark / exit / settlement layer is new (it targets the 0DTESPX
  order + position model instead of `paper.synthetic_entry_fill` / `evaluate_open_trade`).

## Compliance guardrails (non-negotiable)

The ToS forbids bulk extraction / building a local copy or derivative of the historical **dataset**.
This module stays inside the line by:

- **Never persisting raw option-chain snapshots.** Chain reads are consumed in-memory to make a
  trade decision that tick, then discarded. Only our **own trades/results** (strikes, fills, exits,
  P&L) are written to disk.
- **Reading the chain only when a trade decision needs it** — at entry-eligible ticks — not
  systematically walking the archive. Stop monitoring uses the **free** position marks, never a
  chain read.
- **Rate-limit budgeting** well under the 10k bucket (see below), and honoring `Retry-After`.
- A module-level docstring stating the compliance posture, mirroring the warning already on
  `paper_replay.py`.

If the `iv_rank` source (below) ends up reading a daily-VIX lookback, that decision gets an explicit
ToS sign-off first — a scalar daily-VIX series is arguably not "their dataset," but confirm before
building it.

## Architecture

Three layers in one module (plus reuse of `paper.py` / `db.py`):

1. **`_client` — thin 0DTESPX API client.** Auth via the existing keyring token
   (`pr._token()` + the Cloudflare `User-Agent`). Methods: `open_practice(date)`, `set_clock(sid, ts)`,
   `snapshot(ts)` (metered), `positions(sid)` / `session(sid)` / `transactions(sid)` (free),
   `place(sid, order)`, `order(sid, oid)`. Central `_request` with: bare `Authorization`, JSON,
   429→`Retry-After` backoff, and a `usage_percent` pre-check that pauses when the bucket is low.
2. **`_day` — one-session backtest driver.** Opens a practice session, walks the clock 09:35→16:00
   ET at the live 120s cadence, and per tick runs: (a) stop management off free marks, (b) entry
   evaluation via `paper.evaluate_entry` for **every SPX-eligible profile against one shared
   snapshot** (one metered read serves all profiles that tick), (c) settlement handling. Writes each
   book's trades to the results DB.
3. **`_report` / CLI** — `run --date`, `run --start/--end` (multi-day batch with pacing),
   `--profiles`, `status`; plus the reused `login` / `set_token` helpers. Rollup reuses
   `db.py get_range_summary` (already profile-agnostic).

### Data shapes (locked in by the spike)

- **Snapshot:** `GET /market-data/option-chain-snapshots/{YYYY-MM-DDTHH:MM:SS}` — UTC, **no `Z`**;
  flat `call_<k>`/`put_<k>` → `{bid, ask, delta}` (delta unsigned 0..1), `null` for unlisted.
- **Instrument (OCC):** `"SPXW  {YYMMDD}{C|P}{strike*1000:08d}"` (6-char root `SPXW` + two spaces).
- **Order:** `{type:"limit", price, price_effect:"credit"|"debit", legs:[{instrument, quantity,
  action}]}`; `action ∈ {sell to open, buy to open, buy to close, sell to close}`. A 4-leg IC is one
  order. Response: `fill_price`, `execution_price`, `fees`, `status`, per-leg `transactions`,
  `buying-power-effect`.
- **Positions (free):** per leg `{instrument, direction, quantity, cost_basis, price (mark),
  delta, unrealized_profit_loss, ...}` — the per-side stop input.
- **Session (free):** `net_liquidation_value`, `equity_options_realized_profit_loss` /
  `_unrealized_` / `_fees`, buying power. Settles at 16:00 ET when the clock reaches close.

## The backtest loop (per SPX day)

```
open practice session(date); fetch whole-day spx[,vix,expected-move] series once (spot lookup)
for clock t in [09:35 .. 16:00 ET] step 120s:
    set_clock(t); spot = series[t]
    # 1. stop management (FREE) — one positions() read, shared across all profiles
    marks = positions(sid)
    for each open IC (all profiles): per-side cost = short_mark - long_mark
        if side_open and cost >= stop_trigger * net_credit: place closing limit(debit); confirm fill
    # 2. entries — ONE metered snapshot(t) shared across all SPX-eligible profiles
    if any profile is entry-eligible this tick:
        snap = snapshot(t); build candidates for union_widths_for_symbol("SPX")
        for profile in spx_eligible_profiles:
            snapshot_view = {..spot, iv_rank(source), vix, gex:{ok:False}, candidates, now_et:ET(t)..}
            entered, reason, chosen = paper.evaluate_entry(view, params, open_ics[profile],
                                                           account_open_count, todays[profile], last[profile])
            if entered: place IC limit(credit) from chosen legs; on fill -> record trade, update stagger state
set_clock(16:00); read session realized P&L + transactions -> finalize each book
```

- **Shared reads:** one `positions()` and at most one `snapshot()` per tick serve **all** profiles —
  the metered cost is per-tick, not per-profile. SPX-eligible profiles = ladder ×4 + `large-spx` +
  `explore-spx-tightcredit` (6 books).
- **SPX is cash-settled** → MEIC leaves ICs to expire; the session settles them at 16:00 ET. Verify
  0DTESPX auto-settles open positions at close (the prototype saw realized P&L populate at close);
  if a leg lingers, force-close as a backstop.
- **Force-close/event days:** reuse the FOMC/quarterly logic from `paper.force_close_active` for the
  event closes (SPX cash-settled otherwise expires).

## Open decision — `iv_rank` source (the one real design question)

`min_iv_rank` is a binding gate but a true rank is a **VIX percentile over ~252 days**, and the
per-date endpoint returns intraday-only. Options, pick one:

- **(A) Incremental daily-close percentile (recommended).** Retain one scalar per backtested date
  (VIX close, or 0DTESPX's `spxExpectedMove`) in a small local `iv_history` table and compute a
  rolling percentile. Bootstrap the lookback once by reading N daily closes (a scalar series, **not**
  the chain dataset). Keeps the gate faithful. **ToS caveat:** confirm a daily-VIX/expected-move
  scalar series is acceptable before building the bootstrap (it is not the proprietary 0DTE chain
  archive, but get the sign-off).
- **(B) VIX-band gate.** Replace `min_iv_rank` with a VIX-level threshold for practice runs only
  (documented divergence from live). Simplest, unambiguously compliant, but changes gate semantics.
- **(C) Expected-move percentile.** Same as (A) but keyed on `spxExpectedMove` (already fetched once
  per day), so no separate VIX bootstrap.

Recommendation: **(A) or (C)** — retain a per-day scalar and compute a rolling rank; tag every
practice trade `iv_rank_source="vix_percentile"` / `"expected_move_percentile"` so it's auditable and
distinct from forward-paper's native rank.

## Fee & slippage alignment — investigated (Phase 5): no alignment needed

Finding (probed 2026-07-13): **0DTESPX's per-SPX-IC fees already match our tastytrade model to the
cent**, so there is nothing to align:

| Action (SPX IC) | 0DTESPX (`fee_schedule`) | ours (`cherrypick.core.fees`) |
|---|---|---|
| open (4 legs) | 4 × 1.72 = **6.88** | **6.8866** |
| close one side (2 legs) | 2 × 0.72 = **1.44** | **1.4433** |
| close full (4 legs) | 2.88 | 2.8866 |
| expire | 0 | 0 |

`PATCH /user` **does** accept `fee_schedule`/`slippage` (204), so they are tunable — but that mutates
the account **globally** (it would change the web app too), and since the fees already match, aligning
them would move per-IC P&L by <$0.01 while risking the user's real settings. **Decision: do not mutate
the account; run on 0DTESPX's cost basis (tagged `cost_basis="0dtespx"`), which is already ≈ our
tastytrade fees.**

The one residual vs. forward paper is 0DTESPX's **0.05 slippage**, which is baked into their fills and
cannot be removed retroactively — so practice P&L is fee-comparable to forward paper but carries a
small extra modeled slippage cost on each entry/stop. (The entry *decision* already uses our fee model
via `evaluate_entry`'s fee-adjusted floor; only the *fill* uses theirs — and the two fee models agree.)

## Results storage & reporting

- **Reuse the `ic_trades` schema** (via `db.py`), tagged `execution_mode="practice_0dtespx"` and a
  `cost_basis` note, in a **dedicated DB** (e.g. `~/.cherrypick/data/meic/practice_trades.db`) so the
  three cost bases (live, forward-paper, practice) never blend. Map 0DTESPX fills → our columns
  (strikes, `net_credit` from `fill_price`, `fees`, `pnl` from realized settlement, `risk_profile`).
- Reporting is **free**: `get_range_summary` / the dashboard profile selector / the EOD roll-up are
  already profile- and mode-agnostic. Store **only** our trades — never the raw chains.

## Order/fill hardening (gaps the prototype exposed)

1. **Confirm every fill.** The prototype saw one stop-close return `fill: None`. Production must poll
   `GET .../orders/{oid}` (or re-read positions) to confirm a close executed; handle `pending` /
   partial; retry with a more marketable price if unfilled after a tick.
2. **Idempotency keys** on order POSTs (the API supports `Idempotency-Key`) so a retried tick can't
   double-place.
3. **Marketable close pricing** from current marks + a small cushion (mirrors live `stop_limit_ratio`).
4. **429/backoff** and a `usage_percent` pre-flight guard; pause the batch when the bucket is low.

## Testing

- Unit-test `_day` against a **canned client** (a handful of our own recorded API responses as
  fixtures — small, our captures, never their bulk dataset): assert entry→order→stop→settlement
  drives the expected trades and the stop math matches `paper`'s per-side rule.
- CI never hits the live API (no token in CI). Reuse `paper`'s gate tests as-is (unchanged logic).
- A `--dry` mode that logs intended orders without placing them, for a safe first live run.

## Phasing & effort

1. **Client + hardened one-profile day** (harden the prototype: fill confirmation, idempotency,
   backoff). ~½–1 day.
2. **All SPX-eligible profiles, shared per-tick reads**, results DB with `execution_mode` tag. ~1 day.
3. **`iv_rank` source** (decision A/B/C) + `iv_history` table + bootstrap. ~½ day + the ToS sign-off.
4. **Multi-day batch** CLI with pacing + reporting integration (`get_range_summary`, dashboard, EOD).
   ~½ day.
5. **Fee-alignment mode** (if `PATCH /user` allows) + docs. ~½ day.

## Risks / non-goals

- **SPX-only** — does not validate 5 of the 7 experiment cells; those remain forward-paper-only.
- **Cost basis differs** from tastytrade — a separate lens, not a substitute for forward paper.
- **`iv_rank` compliance nuance** (daily-scalar bootstrap) needs confirmation before Phase 3.
- **Not** real-time, **not** the raw-chain replay (disabled), **not** the 0DTESPX strategy-builder
  approach (that would re-express MEIC declaratively and lose fidelity).
