# Paper-trading experiment cells (account-size study)

> ## ⚠️ RETIRED — 2026-07-18
>
> **The 15 experiment cells described below were removed from `config.risk.json`.** It now holds
> only the four-tier ladder. This document is kept because the **per-profile mechanism is still
> fully supported by the engine** — `symbols`, `wing_widths_by_symbol`, `wing_selection`,
> `stagger_entries`, `short_delta_target`, `regime_gex_require_positive`, and
> `per_side_stop_management` all still work exactly as described, so this is the reference for
> rebuilding cells if the study resumes. The cells themselves are recoverable from git history.
>
> **Why they were retired.** Each cell pinned a *symbol* as part of its identity
> (`large-spx`, `small-xsp`, …). That collided with the portfolio model the paper study now runs on,
> where **the symbol is its own axis**: one portfolio is formed per **(profile × symbol)** pair, each
> with its own `max_concurrent_ics` and daily-entry budget. Under that model a symbol-pinned cell is
> a category error — it fuses the two axes into one name, so `large-spx` and a hypothetical
> `large-qqq` could never be compared as "the same strategy on two instruments."
>
> **If the study resumes**, define cells as **symbol-agnostic branches** (one gate-config change,
> no `symbols` pin) and let the portfolio grain supply the per-symbol split. See
> [risk-profiles.md](risk-profiles.md) for the ladder's design rationale and the two-axis model.

---

The parallel-shadow paper engine (`src/paper.py`, driven unattended by `src/paper_loop.py`)
evaluates **every** profile in `config.risk.json` against each iteration's market snapshot, per
symbol, writing all books to `~/.cherrypick/data/meic/paper_trades.db`. Beyond the four-tier risk
ladder (conservative → very-aggressive), the registry *used to* hold **experiment cells** whose
purpose was to collect enough variation in the placed iron condors to analyze optimal risk profiles
for **small, medium, and large accounts** — by varying wings, symbols, and the minimum credit, and
by staggering entries across the day. This was paper-only; nothing here ever touched the live
account.

## What makes an experiment cell different from a ladder tier

The ladder tiers are complete presets that share one risk-appetite axis. Each experiment cell is a
**partial overlay** merged onto `config.json` that pins one `(symbol, wing, min-credit)` cell using
per-profile keys the ladder does not carry:

| Key | Meaning |
|---|---|
| `symbols` | The subset of the account's symbols this profile trades. A cell pinned to `["XSP"]` is skipped entirely for an SPX snapshot. Absent ⇒ trades all base `symbols` (the ladder's behavior). |
| `wing_widths_by_symbol` | This profile's own wing shortlist per symbol. `paper_loop` builds each symbol's candidate menu from the **union** of every profile's widths; each profile then picks from its own subset. |
| `wing_selection` | How to pick among clearing candidates: `widest` (default, fee-drag bias), `narrowest` (small-account cells), or `fixed` (the shortlist's own order). |
| `stagger_entries` | Opt-in. When true, enforces the entry window (`entry_window_start`/`_end`), a hard daily cap (`daily_ic_trade_target`), and spacing (`min_minutes_between_entries`) so entries spread across the session instead of filling in the first passing iterations. The ladder omits this and keeps the prior unstaggered behavior. |
| `min_minutes_between_entries` | Minimum spacing between this profile's entries (staggering). |

**Account-size character lives in wing width + symbol** (the dollar risk per IC), not in throttling
how many samples get collected. The cells use *throughput* caps (concurrency 4 / daily 6) so several
time-cohorts stay open at once for denser time-of-day coverage; reconstruct a strict small account
(e.g. 2 concurrent) by filtering the tagged rows in analysis.

## The current roster

| Profile | Symbol | Wings | Pick | min_credit | Concurrent | Daily | Spacing | Tier / purpose |
|---|---|---|---|---|---|---|---|---|
| `small-xsp` | XSP | 2, 3 | narrowest | 0.12 | 4 | 6 | 45m | Small acct, cash-settled |
| `small-iwm` | IWM | 3, 5 | narrowest | 0.12 | 4 | 6 | 45m | Small acct, physically-settled |
| `medium-qqq` | QQQ | 5 | fixed | 0.15 | 4 | 6 | 45m | Medium, physically-settled |
| `medium-xsp-wide` | XSP | 5, 10 | widest | 0.15 | 4 | 6 | 45m | Wing-risk contrast vs `small-xsp` |
| `large-spx` | SPX | 5, 10 | widest | 0.15 | 4 | 6 | 45m | Large acct |
| `explore-spx-tightcredit` | SPX | 5 | fixed | 0.20 | 4 | 6 | 45m | Credit-floor sweep (stricter) |
| `explore-xsp-loosecredit` | XSP | 2 | fixed | 0.10 | 4 | 6 | 45m | Credit-floor sweep (looser) |

QQQ and IWM are physically settled — they exercise the paper engine's existing early
force-close + modeled friction / pin-penalty path (see `docs/paper-trading.md`); no new settlement
code was added for them.

## How staggering behaves through the day

The two caps do different jobs: **concurrency** holds several time-cohorts open at once, while the
**daily cap + spacing** govern cadence. Example — `small-xsp` (concurrent 4 / daily 6 / 45m) over the
10:00–14:30 window enters around 10:00 → 10:45 → 11:30 → 12:15 (four cohorts live), then refills at
~13:00 and ~13:45 **as earlier ICs stop out and free a slot**, up to the daily cap of 6. Because
0DTE cash-settled XSP is left to expire and only frees a slot intraday on a per-side stop, on a calm
day a cell simply holds its four cohorts; on a choppy day it rotates through more.

## Reading the results

Every book is tagged with its profile name in `ic_trades.risk_profile`, and the whole read side is
profile-name-agnostic:

- `python src/db.py get_range_summary --start <d> --end <d>` groups metrics by profile.
- The daemon's deterministic EOD report (`logs/paper-eod-<day>.md`) tables every profile that
  traded, with a **Symbol** column — so each cell reads directly as an account-size / wing / credit
  comparison — and notes which configured profiles were idle.
- The live dashboard's profile selector lists whatever tags exist.

## Validation (forward paper on tastytrade)

All cells are validated **forward** by the automated paper engine (`paper_loop.py`) against live
tastytrade data — the cells accumulate real, tagged trades day to day and surface in
`get_range_summary` / the EOD report / the dashboard.

> ⚠️ The SPX historical replay tool (`src/paper_replay.py`) is **not** used: its bulk-extraction
> design is incompatible with 0DTESPX's terms of service (confirmed 2026-07-13). See
> [0dtespx-api.md](0dtespx-api.md) and the warning in [paper-trading.md](paper-trading.md). Historical
> backtesting is therefore out of scope here; the sanctioned server-side alternatives (0DTESPX
> practice sessions / strategy backtester) would require re-expressing MEIC in their order model and
> are not currently pursued.

## Load note

Per-profile symbol pinning keeps the per-iteration work modest (~11 profile×symbol evaluations, each
pinned cell touching one symbol). The staggering DB read is issued only for profiles that opt in. If
the roster is later expanded into a dense grid, batch `process_symbol`'s per-profile `get_open_trades`
shell-outs into one read per iteration before adding many more cells.
