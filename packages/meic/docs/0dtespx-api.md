# 0DTESPX.com API — reference notes

Research notes on the [0DTESPX.com](https://www.0dtespx.com) API, sourced from the OpenAPI spec
(`/openapi.yaml`), the [walkthrough](https://www.0dtespx.com/docs/api/walkthrough),
[`/llms.txt`](https://www.0dtespx.com/llms.txt), and the
[rate-limits](https://www.0dtespx.com/docs/api/rate-limits) / [access](https://www.0dtespx.com/docs/access)
pages, cross-checked against live probing. Relevant because `src/paper_replay.py` drives our paper
engine from this API. **Read the Acceptable-use verdict first — it changes what we should build.**

## Acceptable-use verdict (decision-critical)

**Our local-cache replay design violates 0DTESPX's terms.** Verbatim from the rate-limits page and
`/llms.txt`:

> "Bulk extraction of the historical dataset is **not permitted, at any pace**. Systematically
> walking the archive of past sessions … to build **a local copy or a derivative dataset** violates
> the terms even when paced under the credit budget — the budget bounds burst load, it is not a data
> license; offending accounts may be suspended and their networks may lose access."

`src/paper_replay.py` does exactly the prohibited thing: it walks each session 09:30–16:00 at a 120s
cadence, **caches every day to `replay_cache/<date>.json`**, and derives a local `paper_trades.db`.
That is "systematically walking the archive … to build a local copy or a derivative dataset." Pacing
under the credit budget does not make it permitted. **Conclusion: do not rewrite the replay adapter
to pull raw option-chain snapshots; the tool should not be used against this API.**

**Sanctioned alternatives** for historical validation (the platform runs the simulation server-side;
you never extract the dataset):
- **Practice sessions** — `POST /practice/sessions {"date": …}` opens a $100k sandbox replay of any
  past day with manual clock control (`PATCH …/{sid} {"time": …}`), and you place orders / read
  positions & transactions via the API. **These endpoints are unmetered (free).**
- **Strategy builder + backtester** — express a strategy in their builder (`/strategies`,
  `/strategies/preview`) and the platform backtests it over full history server-side.

Both require re-expressing MEIC logic in *their* order/strategy model rather than running our own
engine, so neither is a drop-in for our local paper engine. The compliant path that needs no
0DTESPX at all remains **forward paper on tastytrade** (what `paper_loop.py` already does).

## Base URL & auth

- **Base:** `https://api.0dtespx.com` (no `/v1` prefix). Docs/spec host: `https://www.0dtespx.com`.
- **Token:** obtained from `POST /auth/sessions` (login) or `POST /auth/register`. Sent as the
  **bare** value of the `Authorization` header — **no `Bearer` prefix**.
  - ⚠️ Spec discrepancy: `openapi.yaml` declares an HTTP `bearerToken` scheme (which implies
    `Authorization: Bearer <token>`), but the walkthrough and live probing both confirm the **bare**
    token is what works. `src/paper_replay.py` sends it bare — correct.
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

## Endpoint catalog (by group)

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

## Bottom line for cherrypick-meic

`src/paper_replay.py` cannot be used against this API without violating its terms. The Cloudflare/UA
fix and the `login`/`request_code` helpers stay useful only if we pivot to the **sanctioned**
practice-session / backtest API (a separate, server-side integration). Otherwise, historical
validation of the paper experiment cells should come from **forward paper on tastytrade**, and this
doc plus the replay tool serve as reference only. Recommend not investing further in the
raw-snapshot replay path.
