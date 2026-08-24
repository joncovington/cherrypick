# The deep window, and the day it was changed under an outage

*Dated record. `deep_window_pct` is an ENTRY parameter — it bounds which strikes the entry snapshot
can see — so a change to it belongs on the record rather than in a config diff alone.*

## 2026-08-24: cut to 0.12 mid-session, restored to 0.20 after the close

**What happened.** The suite's market-data producer spent the first half of the session
crash-looping on DXLink's subscription rate limit (see `docs/streamer-subscription-budget.md`).
While diagnosing it, this module's `deep_window_pct` was cut **0.20 → 0.12** as emergency load
shedding: it drives the `window_hints` this module asks the producer for, and XSP's hint of 163
strikes per side was the single largest window in the suite.

**It was restored to 0.20 the same evening, and the measurement is why.** With the producer's
subscribe burst now paced, the load argument for the cut was gone — and 0.12 turned out to be
*wrong*, not merely conservative:

| symbol | 85–90 delta call, ~21 DTE | measured |
|---|---|---|
| TQQQ | strikes 56.0–58.5 against spot 68.995 | **15.2 – 18.8% ITM** |

A 12% bound stops short of that entire range. Had it stayed, this module could not have found its
deep-ITM long on TQQQ at all — the entry would have refused `no_deep_itm_long` indefinitely, and
the escalation ladder would have walked the hint up trying to reach a strike the bound forbade.

**It did not bite, for an unrelated reason.** TQQQ entries were already refusing
`dividend_calendar_lapsed` (327 attempts that session — see below), so the cut never actually
blocked a reachable entry. That is luck, not margin.

**What this says about 0.20.** It is the right order of magnitude and it has ~1–5 points of headroom
over the measured need, which is thin enough to be worth knowing. It descends from 0.45 under the
pre-2026-08-23 ~99-delta design (whose longs sat **36.5% ITM** — the four historical TQQQ positions
in the ledger all bought the 45 strike against a ~71 spot) and was set to 0.20 when the redesign
moved to an 85–90 delta stock substitute. The number above is the first *measurement* against it.

**Note the symbols differ a lot, and the bound is shared.** XSP's own 85–90 delta strike sits far
closer to spot than TQQQ's — the 2026-08-24 XSP entry bought 734 against a 764.5 spot, **4.0% ITM**
— because a 3× leveraged ETF carries several times the implied volatility of a broad index. One
`deep_window_pct` serves both, so it is necessarily sized for the deepest symbol and is
correspondingly wasteful for the shallowest. If the XSP window's cost ever matters again, the fix
is a per-symbol bound, not a smaller shared one.

## Standing item: the dividend calendar is lapsed for TQQQ

Separately, and still open at the time of writing: every TQQQ entry attempt on 2026-08-24 refused
`dividend_calendar_lapsed` — 327 of them. The config's `dividends` block is declared through August,
and an entry now spans a ~21 DTE back expiration into mid-September, past the declared coverage.

That is the guard working exactly as designed ("a lapsed table stops entries loudly, by design"),
and it is **not fixable in code**: the dates are declared from ProShares' own distribution schedule
because they cannot be computed (the third-Friday rule fails on SSGA's own June 2026 date) and are
never fetched (no network on a loop path). **TQQQ cannot enter until the September ex-date is filled
in and `declared_through` is advanced.** Until then this module trades XSP only, which is a sample
composition fact, not a preference — any read of this era's TQQQ population should know it stops
here.
