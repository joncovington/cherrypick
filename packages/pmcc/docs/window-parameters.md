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

## The dividend calendar lapsed for TQQQ, and was refreshed the same evening

Every TQQQ entry attempt on 2026-08-24 refused `dividend_calendar_lapsed` — 327 of them. The
`dividends` block was declared only through August, while an entry that day spanned a ~21 DTE back
expiration into mid-September, past the declared coverage. That is the guard working exactly as
designed ("a lapsed table stops entries loudly, by design"), and it is not fixable in code: the
dates cannot be computed (the third-Friday rule fails on SSGA's own June 2026 date) and are never
fetched, because nothing on a loop path may touch the network.

**Refreshed 2026-08-24 from ProShares' own distribution schedule** (`proshares.com/resources/
distributions/distribution-schedule`), which is the authority the config names:

| quarter | ex date | record | payable |
|---|---|---|---|
| Q3 2026 | **2026-09-23** | 2026-09-23 | 2026-09-29 |
| Q4 2026 | **2026-12-23** | 2026-12-23 | 2026-12-30 |
| excise (potential) | **2026-12-31** | 2026-12-31 | 2027-01-07 |

Three notes on how that was taken, since this module's docs warn that aggregators disagree by a day:

- **The issuer page is the source; a second source is the check on it.** An independent aggregator
  was consulted separately and agreed on 9/23, which is what makes the date worth declaring rather
  than a single unverified read.
- **The potential excise-tax distribution is included deliberately.** It may not happen; refusing a
  week that *might* carry an ex-date is the safe direction for a module that does not model early
  assignment at all.
- **`declared_through` is 2026-12-31, so the calendar lapses again for any entry reaching into
  2027** — from roughly early December, when a ~21 DTE back expiration first crosses the year end.
  That is the design working, not a bug to route around: refresh it from the issuer page before
  then.

Effect, verified against the live config: an entry on 2026-08-25 with a back expiration of 09-11 or
09-18 enters; one reaching 09-25 refuses `ex_dividend_span` on 2026-09-23; one reaching 2027 refuses
`dividend_calendar_lapsed`. **TQQQ trades again from 2026-08-25**, with a natural blackout across
the September ex-date. The 2026-08-24 session itself remains TQQQ-less — a sample-composition fact
worth knowing when reading this era.
