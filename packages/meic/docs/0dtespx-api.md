# 0DTESPX.com API — reference notes

**What this covers:** a technical and legal reference on a third-party historical-data API, what the
module may do with it (practice sessions through `paper_practice.py`) and what it may not (bulk
extraction through `paper_replay.py`, which is disabled). More detailed and code-oriented than the rest
of this module's docs. Part of the [MEIC module](../README.md) in the cherrypick suite.

Research notes on the [0DTESPX.com](https://www.0dtespx.com) API, sourced from the OpenAPI spec
(`/openapi.yaml`), the [walkthrough](https://www.0dtespx.com/docs/api/walkthrough),
[`/llms.txt`](https://www.0dtespx.com/llms.txt), and the
[rate-limits](https://www.0dtespx.com/docs/api/rate-limits) / [access](https://www.0dtespx.com/docs/access)
pages, cross-checked against live probing. `cherrypick/meic/paper_replay.py` (disabled) and
`cherrypick/meic/paper_practice.py` both talk to this API. **Read the Acceptable-use verdict first.**

## Acceptable-use verdict

**A local-cache replay violates 0DTESPX's terms.** Verbatim from the rate-limits page and `/llms.txt`:

> "Bulk extraction of the historical dataset is **not permitted, at any pace**. Systematically
> walking the archive of past sessions … to build **a local copy or a derivative dataset** violates
> the terms even when paced under the credit budget — the budget bounds burst load, it is not a data
> license; offending accounts may be suspended and their networks may lose access."

`cherrypick/meic/paper_replay.py` does exactly the prohibited thing: it walks each session 09:30–16:00 at a 120s
cadence, **caches every day to `replay_cache/<date>.json`**, and derives a local `paper_trades.db`.
That is "systematically walking the archive … to build a local copy or a derivative dataset." Pacing
under the credit budget does not make it permitted. **The replay tool stays disabled; never rewrite it
to pull raw option-chain snapshots.**

**Sanctioned alternatives** for historical validation (the platform runs the simulation server-side;
you never extract the dataset):
- **Practice sessions** — `POST /practice/sessions {"date": …}` opens a $100k sandbox replay of any
  past day with manual clock control (`PATCH …/{sid} {"time": …}`), and you place orders / read
  positions & transactions via the API. **These endpoints are unmetered (free).**
- **Strategy builder + backtester** — express a strategy in their builder (`/strategies`,
  `/strategies/preview`) and the platform backtests it over full history server-side.

The strategy builder requires re-expressing MEIC in *their* strategy model. Practice sessions let our
own entry logic drive their sandbox, which is what `paper_practice.py` does (below). The path that needs
no 0DTESPX at all is **forward paper on tastytrade** (what `paper_loop.py` does) and the quote-history
replay (`python -m cherrypick.meic.replay`, see [reference.md](reference.md)).

## Base URL & auth

- **Base:** `https://api.0dtespx.com` (no `/v1` prefix). Docs/spec host: `https://www.0dtespx.com`.
- **Token:** obtained from `POST /auth/sessions` (login) or `POST /auth/register`. Sent as the
  **bare** value of the `Authorization` header — **no `Bearer` prefix**.
  - ⚠️ Spec discrepancy: `openapi.yaml` declares an HTTP `bearerToken` scheme (which implies
    `Authorization: Bearer <token>`), but the walkthrough and live probing both confirm the **bare**
    token is what works. `cherrypick/meic/paper_replay.py` sends it bare — correct.
- **Cloudflare:** the API is behind Cloudflare, which rejects the default Python `urllib`
  User-Agent with HTTP 403 "error code: 1010". Send a browser-shaped `User-Agent` (handled in
  `paper_replay._USER_AGENT`).
- **Health:** `GET /health` — liveness, no auth.

## Access tiers (both free)

| Tier | Access |
|---|---|
| Visitor (no auth) | Live chart, most-recent completed session, public strategies, historical at **30s** resolution |
| Registered (free) | Every date at **1s** resolution, practice & live trading, strategies, **full API + WebSocket** |

"There is no paid tier, no trial clock, and nothing to upgrade to later." The web-app login and the
API token are the same kind of session.

## Rate limits

Per-account **leaky bucket**:
- **Capacity:** 10,000 credits. **Drain/refill:** ~0.116 credits/sec (~417/hr; **full refill ≈ 24h**).
- Only **market-data** and a few heavy reads are metered. **Auth, accounts, sessions, orders,
  positions, transactions, and practice simulations are free.**

| Endpoint | Cost |
|---|---|
| `GET /market-data/strikes/{date}` | 5 |
| `GET /market-data/historical/{date}` | 10 |
| `GET /market-data/option-chain-snapshots/{timestamp}` | 10 |
| `GET /market-data/option-chain-snapshots/{start}/{end}` | 5 × snapshots (≤30/call; max range = 30 × interval) |
| `GET /strategies/{id}/results/days/{date}` (drill-in) | 10 |
| Portfolio drill-in | member_count × 10 |
| AI assistant turn | 50 (refunded on failure) |

- **Headers:** `X-RateLimit-Used`, `X-RateLimit-Limit` (10000), `Retry-After` on 429. The `/user`
  profile also reports `usage_percent` (bucket fill 0–100).
- **429 codes:** `rate_limit_exceeded`, `too_many_active_backtests`, `too_many_active_turns`,
  `assistant_budget_exceeded`.
- **Concurrency caps:** 3 concurrent backtests/user; 2 in-flight assistant turns/account.
- **Unauthenticated per-IP throttle:** burst of 20, refill 10/min.

For scale: one day at our 120s cadence ≈ 195 marks. Single-snapshot pulls = ~1,950 credits/day; the
range endpoint = ~975 credits/day. Either way a handful of days drains the 24h bucket — **and it is
disallowed regardless of pacing** (see verdict above).

## Endpoint catalogue (by group)

- **auth:** `POST /auth/check-email` `{email}` (404 = free to register); `POST /auth/verify-email`
  `{email}` (204; 1/60s, code expires 15min, locks after 5 fails); `POST /auth/register`
  `{email, verification_code, password}`; `POST /auth/forgot-password`; `POST /auth/reset-password`
  `{reset_token, password}`; `POST /auth/sessions` `{email, password}` **or** `{email, verification_code}`
  → `{token}`; `DELETE /auth/sessions` (logout, 204).
- **user:** `GET /user`; `PATCH /user` `{email?, password?, verification_code?}` (email/password
  changes need a fresh code; not both in one call).
- **accounts:** `GET /accounts`; `POST /accounts` `{name, starting_capital 1000–10M, engine:"live_sim", type?}`;
  `GET|PATCH|DELETE /accounts/{id}`.
- **sessions (live):** `GET|POST /accounts/{id}/sessions`; `.../current`; `.../head`;
  `GET|DELETE .../sessions/{sid}`; `.../{sid}/history?interval=`; `.../{sid}/positions?at=`;
  `.../{sid}/transactions?at=`.
- **orders:** `GET|POST .../orders` (POST supports `Idempotency-Key` header;
  `{order_type, legs[], price?, stop_trigger?}`); `.../orders/dry-run`; `GET|PUT|DELETE .../orders/{orderId}`.
- **practice (replay sandbox):** `GET|POST /practice/sessions` `{date:"YYYY-MM-DD"}` → $100k sandbox;
  `GET|PATCH|DELETE /practice/sessions/{sid}` (`PATCH {time}` advances/rewinds the clock,
  `start_time ≤ time ≤ end_time`); `.../{sid}/history|orders|positions|transactions`.
- **market-data:** `GET /market-data/sessions` (public; date → `start-time/end-time/data-*-time`,
  flags `current/restricted/upcoming`); `.../strikes/{date}`; `.../historical/{date}?series=…`;
  `.../option-chain-snapshots/{timestamp}`; `.../option-chain-snapshots/{start}/{end}`;
  `.../average-expected-move` (public, 24h cache).
- **strategies / portfolios / bots:** server-side strategy builder, AI-assistant drafts, backtests,
  portfolio aggregation, and (not-yet-launched) live bots. See spec.
- **websocket:** real-time market-data and session/backtest/assistant event streams (replay cursor
  for reconnect). Spec section large; not detailed here.

## Market-data shapes (validated live)

- **Snapshot (single):** `GET /market-data/option-chain-snapshots/{YYYY-MM-DDTHH:MM:SS}` — timestamp
  in **UTC, no `Z` suffix** (a `Z` gives HTTP 400; an ET time like `09:35:00` gives 404 — must be the
  UTC instant, e.g. `13:35:00` for 09:35 ET EDT). Body is a flat dict keyed `call_<strike>` /
  `put_<strike>` → `{bid, ask, delta}`; delta is **unsigned** 0..1; strikes not listed are `null`.
- **Snapshot (range):** `.../{startTime}/{endTime}`, ISO-8601, ≤30 snapshots/call.
- **Historical series:** `GET /market-data/historical/{YYYY-MM-DD}?series=spx,vix,spxExpectedMove`
  (also `spxOTMBids`, `spxExtrinsic`; default `spx,spxExpectedMove`). Returns a 1s-cadence array of
  `{datetime (…Z), datetimeUnix, spx, vix, spx_expected_move}` (values are strings). Public for the
  current/most-recent session. Note: `series=VIX` (uppercase) returns rows **without** a value —
  use lowercase `vix`. There is **no** multi-day series in one call, so a VIX-percentile `iv_rank`
  would require one call per past date (which the acceptable-use policy forbids anyway).

## Fees & slippage (per-user, `GET /user`)

`GET /user` returns a per-user `fee_schedule` and `slippage` (default `0.05`). For SPX options the
schedule is `open 1.72/leg`, `close 0.72/leg`, `exercise 5` — which **matches cherrypick.core.fees to
the cent** (open 4×1.72 = 6.88 vs 6.8866; close/side 1.44 vs 1.4433; close full 2.88 vs 2.8866;
expire 0). `PATCH /user` accepts `fee_schedule`/`slippage` (204), so they are tunable, but that mutates
the account **globally** (web app included), and since the fees already match it would move per-IC P&L by
under a cent. **Do not mutate the account.** The practice backtester runs on 0DTESPX's basis (tagged
`cost_basis="0dtespx"`); the only residual against tastytrade forward paper is the 0.05 slippage baked into
its fills, so practice P&L is fee-comparable to forward paper but carries a small extra modelled slippage
on each entry and stop.

## The practice backtester (`paper_practice.py`)

`cherrypick/meic/paper_practice.py` drives MEIC's real decision logic through practice sessions instead of
extracting data. It is SPX-only (the platform has no XSP, QQQ or IWM), separate from forward paper and from
`paper_replay.py`, and its results live on 0DTESPX's cost basis, not directly comparable to tastytrade P&L.

- **Reuse, not a fork.** It imports `paper.evaluate_entry`, the arm loader, `_merged_params`,
  `union_widths_for_symbol` and the staggering unchanged. Only the fill, mark, exit and settlement layer is
  its own, targeting the 0DTESPX order and position model.
- **One tick.** The driver opens a practice session per arm, steps the clock 09:35 to 16:00 ET at 120 s,
  and per tick (a) manages stops from the free position marks, shared across arms, (b) takes at most ONE
  metered chain snapshot, shared by every SPX-eligible arm, and runs `paper.evaluate_entry` for each,
  placing the IC as one 4-leg limit order, and (c) lets the session settle at 16:00. SPX is cash-settled,
  so ICs are left to expire; event-day closes reuse `paper.force_close_active`.
- **Compliance by construction.** The chain is read only at an entry-eligible tick, consumed in memory
  and discarded; stop monitoring uses the free position marks, never a chain read; only our own trades
  and results are written. Credits are budgeted well under the 10,000 bucket, `429` honours
  `Retry-After`, and a `usage_percent` pre-check pauses a batch when the bucket is low. This is trading
  and research use, not walking the archive.
- **Fill hardening.** Fills are confirmed by position reconciliation (a stop-close can return no fill);
  order POSTs carry an `Idempotency-Key` so a retried tick cannot double-place; closing limits come from
  current marks plus a small cushion, mirroring `stop_limit_ratio`.
- **`iv_rank` is a VIX-band pseudo rank** (`_VIX_IV_RANK_BANDS`), because a true rank needs a year of VIX
  closes and the API has no multi-day series (one call per past date is itself disallowed). Every row is
  tagged `iv_rank_source="vix_band"` so it stays distinct from forward paper's native rank.
- **Results** go to `practice_trades.db` in the data home (the `ic_trades` schema, tagged
  `execution_mode="practice_0dtespx"`), so live, forward-paper and practice cost bases never blend.
- **CLI.** `python -m cherrypick.meic.paper_practice run --date D` (or `--start/--end` for a paced batch;
  `--profiles a,b`, `--cadence`, `--dry` to log intended entries without placing), `report --start --end`
  for the per-arm roll-up, `status` for the bucket fill, `login --email` to store a token.
  Tests (`tests/test_paper_practice.py`) run against canned fixtures and never touch the API.
- **Limits.** SPX-only, and a separate lens from forward paper, not a substitute.
