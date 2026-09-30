# cherrypick-review — history

> The dated narratives behind the rules in [`../CLAUDE.md`](../CLAUDE.md), moved here verbatim
> when that file was consolidated on 2026-09-29. CLAUDE.md is authoritative for the rules; this
> file keeps the incidents and the numbers they were measured on.

## Why the arms are split (2026-08-12)

A module-level row averages them and hides the finding. On 2026-08-12 MEIC's module total showed
19.6% on risk; underneath it, `open` took **zero stops all session** and returned 21.6%, while
`width-5` stopped 28% of its book and returned 16.7% — and on the down session, 08-11, the width
arms stopped 91-93% of trades and lost more than `open` did. None of that is visible without the
split, and the split is where every interesting question about these modules lives.

## The detector's three conditions

Review also **detects** regime changes nobody journaled and reports them — it never writes one,
because deciding that a book changed is a judgement about what the module did. The detector needs
all three of its conditions to stay useful: a departure from the trailing median, a departure from
the immediately preceding session, and an absolute floor. Without the second it re-reports one
event every session until the median catches up (MEIC's launch flagged three times); without the
third it fires on ratios between trivial counts (earnings going from 6 trades to 2). With all
three, 24 backfilled sessions produce two flags, both real: flies on 2026-07-29 and MEIC on
2026-08-07 — the four-stream launch, which its journal records as 2026-08-11.

## Why the narrative is a script, not a package

That is the distinction that retired `orchestrator/eod_insight.py` rather than moving it — it lived in
the package whose watchdog fired it, one refactor from the reliability path.

## The narrative's first run

**It earned its place on the first run.** Reading only the artifact, it noticed that flies'
`gex-intrinsic` and `control` reported byte-identical results and called it a probable attribution
bug. It was not a bug — `gex-intrinsic` had degraded to ATM centring for the whole session, which is
this module's documented fallback — but it *was* a real problem: the arm was running the control's
strategy under another name, and the fact set was counting it as an independent observation. The
`centred_by` field and the render's collapsed-arms note exist because of that.

## Concentration (fact set v6, 2026-08-26)

Each module's facts carry `concentration` beside `by_profile`: every arm's contribution, the largest
one, the net recomputed without it, and `sign_flips_without_largest`.

Requested by the advisor on 2026-08-19 and it is a presentation rule rather than a trading one — its
own closing line was "no bounded parameter can fix a presentation defect". flies published +6,748.01
for that session. One seven-fill book returned +7,828.42 and the other twelve came to −1,080.41, so
the sign of the day was that arm's sign, on a book whose modelled worst was 3.5× the credit it
collected and which settled positive because price stayed put. Two sessions earlier make the point in
the other direction: −8,071.69 and −4,023.05, both dominated by width-ladder books on 4–7 fills.

**Read `sign_flips_without_largest` first.** A total that changes sign when its biggest contributor is
removed is a measurement of that arm, not of the module. Being *dominated* is not the same as being
*inverted* — 08-17 and 08-18 were dominated and kept their sign, and a flag that fired on all three
would be ignored inside a week.

Two share denominators, because one lies in exactly the case worth flagging. `share_of_net` is the
signed arm/total and goes past 100% when the other arms net against the leader — width-10's 116% is
the honest number and it is *why* the total cannot be read alone; it is `None` at a ~0 total, where
the ratio is meaningless rather than large. `share_of_movement` is |arm| / Σ|arm|, bounded and stable.

The arithmetic lives in `cherrypick.core.ledgers`, over the records every schema reader already
normalises, so the answer is the same for every module — the request was "for every module net", and
a per-module implementation would be seven chances to disagree about what a share is. This package
publishes it and labels nothing: whether the leading arm clears its own module's sample and day bars
is that module's rule, so the leader's trade and session counts travel with it and the gate stays
where it belongs.


## Every module with a ledger reader is reviewed (2026-08-26)

`MODULES` gained **bwb** and **curve**. Both had SQLite ledgers, `cherrypick.core.ledgers` readers,
console pages and watchdog entries from the day they landed, and were simply never added to this
dict — so the suite's cross-module end-of-day review did not know they existed. bwb was carrying
**twelve open positions** at the time.

That is precisely the failure this package was created to prevent: answering "what did the suite do
today" per-package produced six incomparable report families, and a module missing from the one
place that unifies them is back to having no report at all.

`tests/test_module_coverage.py` now fails when a schema has a `core.ledgers` reader and no entry
here, driven off `READERS` rather than a list kept in the test — a module is covered the moment it
gains a normalised reader, which is the earliest point at which the review could have read it. It
also checks the reverse (a reviewed module naming a schema that does not exist would KeyError every
session) and that both `HEALTH_READERS` and `EXPECTED_READERS` carry an entry for each module, since
`build_module_facts` indexes them directly.

curve is included despite having no positions yet. A module with no results reports as a module with
no results; being absent is a different statement, and it is the one that was wrong.
